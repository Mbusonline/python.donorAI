"""
DB-driven donor report pipeline.

Expects tbl_report_pipeline + tbl_report_pipeline_model + tbl_report_document to exist
before generation. Prompt text comes from tbl_report_pipeline_model.description
and optional tbl_report_pipeline.additional_prompt.
Context PDFs, CSVs, and images are listed in tbl_report_document for the pipeline.
Donor logo for the PDF cover comes from tbl_donor.logo (S3 path).
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from typing import Any, Dict, List, Optional, Tuple

import settings
from database.connection import connect
from services.content_assembler import assemble_report_with_model
from services.csv_processor import normalize_chart_type, process_csv
from services.image_processor import process_images_with_borders
from services.pdf_exporter import export_to_pdf
from services.section_mapping_service import map_image_to_section_text_only
from services.validator import validate_markdown
from services.report_cost import compute_report_cost
from services.s3_utils import download_from_s3, upload_local_file

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _is_image_doc(file_name: str, file_type: Optional[str]) -> bool:
    ft = (file_type or "").lower().strip()
    if ft.startswith("image/"):
        return True
    _, ext = os.path.splitext(file_name or "")
    return ext.lower() in _IMAGE_EXTS


def _is_pdf_path(file_name: str, file_type: Optional[str]) -> bool:
    ft = (file_type or "").lower().strip()
    if ft == "application/pdf" or "pdf" in ft:
        return True
    return (file_name or "").lower().endswith(".pdf")


def _is_tabular_path(file_name: str, file_type: Optional[str]) -> bool:
    fn = (file_name or "").lower()
    ft = (file_type or "").lower().strip()
    if ft in (
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ):
        return True
    return fn.endswith(".csv") or fn.endswith(".xlsx") or fn.endswith(".xls")


def _is_plain_text_context(file_name: str, file_type: Optional[str]) -> bool:
    if _is_tabular_path(file_name, file_type):
        return False
    fn = (file_name or "").lower()
    ft = (file_type or "").lower().strip()
    if ft == "text/plain":
        return True
    return fn.endswith(".txt")


def _extract_pdf_text(path: str) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ValueError(
            "PyMuPDF is required to read PDF context documents. Install with: pip install pymupdf"
        ) from e
    doc = fitz.open(path)
    try:
        parts = []
        for page in doc:
            parts.append(page.get_text())
        return "\n".join(parts)
    finally:
        doc.close()


def _seed_from_prompt_description(description: Optional[str]) -> Dict[str, Any]:
    d = (description or "").strip()
    if not d:
        return {}
    if d.startswith("{") and d.endswith("}"):
        try:
            obj = json.loads(d)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return {"Program context": d}


def _fetch_template_metadata(template_layout_id: str) -> Dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT meta_data FROM tbl_template_layout_metadata
                WHERE template_layout_id = %s AND is_active = TRUE
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (template_layout_id,),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                raise ValueError(
                    f"No template layout metadata for template_layout_id={template_layout_id}. "
                    "Run POST /api/templates/process-metadata with this template_layout_id first."
                )
            meta = row[0]
            return dict(meta) if isinstance(meta, dict) else json.loads(meta)


def _fetch_model_row(model_id: str) -> Tuple[str, str, Optional[str]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT title, provider, private_key
                FROM tbl_model
                WHERE model_id::text = %s AND is_active = TRUE
                """,
                (str(model_id),),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Model not found or inactive: model_id={model_id}")
            title = str(row[0])
            provider = str(row[1] or "openai").strip().lower()
            key = row[2]
            return title, provider, key if key is None else str(key)


def _fetch_report_pipeline(report_pipeline_id: str) -> Dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT title, sub_title, donor_id, program_id, template_layout_id,
                       front_page_image, orientataion, location, additional_prompt
                FROM tbl_report_pipeline
                WHERE report_pipeline_id::text = %s AND is_active = TRUE
                """,
                (str(report_pipeline_id),),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Report pipeline not found or inactive: id={report_pipeline_id}")
            return {
                "report_pipeline_id": report_pipeline_id,
                "title": str(row[0] or ""),
                "sub_title": row[1],
                "donor_id": str(row[2]) if row[2] is not None else None,
                "program_id": str(row[3]) if row[3] is not None else None,
                "template_layout_id": str(row[4]) if row[4] is not None else None,
                "front_page_image": row[5],
                "orientation": (row[6] or "portrait") if row[6] is not None else "portrait",
                "location": row[7],
                "additional_prompt": str(row[8] or "").strip() if row[8] is not None else "",
            }


def _fetch_pipeline_model(report_pipeline_id: str) -> Dict[str, Optional[str]]:
    """
    Returns latest tbl_report_pipeline_model row details used by assembly.
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rpm.report_pipeline_model_id,
                       rpm.prompt_id,
                       rpm.model_id,
                       rpm.title,
                       rpm.description,
                       rpm.version
                FROM tbl_report_pipeline_model rpm
                WHERE rpm.report_pipeline_id::text = %s
                ORDER BY rpm.report_pipeline_model_id DESC
                LIMIT 1
                """,
                (str(report_pipeline_id),),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(
                    f"No tbl_report_pipeline_model row for report_pipeline_id={report_pipeline_id}"
                )
            if row[2] is None:
                raise ValueError(
                    f"tbl_report_pipeline_model.model_id is not set for report_pipeline_id={report_pipeline_id}."
                )
            return {
                "report_pipeline_model_id": str(row[0]),
                "prompt_id": str(row[1]) if row[1] is not None else None,
                "model_id": str(row[2]),
                "title": str(row[3] or "").strip() if row[3] is not None else "",
                "description": str(row[4] or "").strip() if row[4] is not None else "",
                "version": str(row[5] or "").strip() if row[5] is not None else "",
            }


def _fetch_donor_branding(donor_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Returns (logo_s3_path, donor_title) from tbl_donor."""
    if donor_id is None:
        return None, None
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT logo, title
                FROM tbl_donor
                WHERE donor_id::text = %s AND is_active = TRUE
                """,
                (str(donor_id),),
            )
            row = cur.fetchone()
            if not row:
                return None, None
            logo = row[0]
            title = row[1]
            return (
                str(logo).strip() if logo else None,
                str(title).strip() if title else None,
            )


def _fetch_pipeline_documents(report_pipeline_id: str) -> List[Dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rd.report_document_id,
                       rd.document_id,
                       rd.file_name AS rd_file_name,
                       rd.file_path AS rd_file_path,
                       rd.file_type AS rd_file_type,
                       rd.report_type,
                       d.file_name AS d_file_name,
                       d.file_path AS d_file_path,
                       d.file_type AS d_file_type,
                       d.program_id AS d_program_id
                FROM tbl_report_document rd
                LEFT JOIN tbl_document d ON d.document_id = rd.document_id AND d.is_active = TRUE
                WHERE rd.report_pipeline_id::text = %s
                ORDER BY rd.report_document_id
                """,
                (str(report_pipeline_id),),
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _resolve_document_paths(
    row: Dict[str, Any],
    expected_program_id: Optional[str],
) -> Tuple[Optional[str], str, str, Optional[str], Optional[str]]:
    raw_doc = row.get("document_id")
    doc_id = str(raw_doc).strip() if raw_doc is not None else None
    if doc_id == "":
        doc_id = None
    fn = row.get("d_file_name") or row.get("rd_file_name") or ""
    fp = row.get("d_file_path") or row.get("rd_file_path")
    ft = row.get("d_file_type") or row.get("rd_file_type")
    chart_type = row.get("report_type")

    if doc_id is not None and expected_program_id is not None:
        dp = row.get("d_program_id")
        if dp is not None and str(dp) != str(expected_program_id):
            raise ValueError(
                f"document_id={doc_id} belongs to program_id={dp}, pipeline program_id={expected_program_id}"
            )

    if not fp or not str(fp).strip():
        raise ValueError(
            f"report_document_id={row.get('report_document_id')} has no file_path "
            "(set document_id or file_path on tbl_report_document)."
        )
    return doc_id, str(fn or "document"), str(fp).strip(), ft if ft is None else str(ft), chart_type


def _resolve_report_document_file(
    row: Dict[str, Any],
) -> Tuple[str, str, Optional[str]]:
    """
    Resolve file fields strictly from tbl_report_document columns.
    Used for program-scoped context docs that should come from pipeline rows only.
    """
    fn = str(row.get("rd_file_name") or "document")
    fp = row.get("rd_file_path")
    ft = row.get("rd_file_type")
    if not fp or not str(fp).strip():
        raise ValueError(
            f"report_document_id={row.get('report_document_id')} has no file_path in tbl_report_document."
        )
    return fn, str(fp).strip(), ft if ft is None else str(ft)


def _fetch_document_metadata_meta(document_id: str) -> Dict[str, Any]:
    """
    Load caption/description from tbl_document_metadata when present.
    Returns {} if there is no row, null meta_data, or invalid JSON (caller may skip the image).
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dm.meta_data
                FROM tbl_document_metadata dm
                JOIN tbl_document d ON d.document_id = dm.document_id
                WHERE dm.document_id = %s AND dm.is_active = TRUE AND d.is_active = TRUE
                LIMIT 1
                """,
                (document_id,),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                return {}
            meta = row[0]
            try:
                out = dict(meta) if isinstance(meta, dict) else json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                return {}
            return out if isinstance(out, dict) else {}


def _merge_pricing(breakdown: List[Dict[str, Any]]) -> Dict[str, Any]:
    tin = 0
    tout = 0
    any_tok = False
    for b in breakdown:
        i = b.get("input_tokens")
        o = b.get("output_tokens")
        if isinstance(i, int):
            tin += i
            any_tok = True
        if isinstance(o, int):
            tout += o
            any_tok = True
    out: Dict[str, Any] = {"breakdown": breakdown}
    if any_tok:
        out["total_input_tokens"] = tin
        out["total_output_tokens"] = tout
    return out


def _assembly_api_key(provider: str, private_key: Optional[str]) -> Tuple[str, str]:
    prov = (provider or "openai").strip().lower()
    if prov in ("gemini", "google"):
        prov = "google"
    pk = str(private_key).strip() if private_key is not None else ""
    # Prefer tbl_model.private_key when set; fall back to env if missing or empty.
    if pk:
        return pk, "tbl_model.private_key"
    if prov == "openai" and (settings.OPENAI_API_KEY or "").strip():
        return settings.OPENAI_API_KEY.strip(), "env.OPENAI_API_KEY"
    if prov == "google" and (settings.GEMINI_API_KEY or "").strip():
        return settings.GEMINI_API_KEY.strip(), "env.GEMINI_API_KEY"
    raise ValueError(
        f"No API key for provider={provider!r}: set tbl_model.private_key or "
        "OPENAI_API_KEY / GEMINI_API_KEY in the environment."
    )


def _normalize_assembly_model_name(provider: str, model_name: str) -> str:
    prov = (provider or "").strip().lower()
    raw = (model_name or "").strip()
    if prov in ("gemini", "google"):
        if not raw:
            return settings.GEMINI_MODEL
        low = raw.lower()
        # DB can store display labels like "Gemini" instead of a concrete model id.
        if low in ("gemini", "google", "gemini pro", "gemini-pro"):
            return settings.GEMINI_MODEL
    if prov == "openai" and not raw:
        return settings.OPENAI_MODEL
    return raw


def run_db_report_pipeline(
    *,
    report_pipeline_id: str,
) -> Dict[str, Any]:
    """
    Load tbl_report_pipeline + assembly model from tbl_report_pipeline_model,
    source files from tbl_report_document, donor logo from tbl_donor.logo.
    Writes tbl_report and tbl_report_cost (pipeline document rows keep report_pipeline_id only).

    Order of work (section mapping needs template sections first):
      1) Template layout metadata (sections, positions, metrics)
      2) Prompt seed + context PDF/txt into seed_text
      3) Images: only those with both non-empty caption and description in tbl_document_metadata;
         others are skipped. Download/border, then section map
      4) CSV processing, assembly, PDF, S3, DB updates
    """
    def _set_pipeline_status(status: str) -> None:
        """
        Best-effort status update for tbl_report_pipeline.status.
        Values expected: pending, processing, completed, failed.
        """
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE tbl_report_pipeline
                        SET status = %s, updated_at = now()
                        WHERE report_pipeline_id::text = %s
                        """,
                        (status, str(report_pipeline_id)),
                    )
                conn.commit()
        except Exception as e:
            print(
                f"[generate-from-db] warning: failed to update tbl_report_pipeline.status={status!r}: {e}"
            )

    run_id = str(uuid.uuid4())
    output_dir = os.path.join(settings.OUTPUT_DIR, run_id)
    log_dir = os.path.join("logs", f"report_db_{run_id}")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    cost_breakdown: List[Dict[str, Any]] = []

    pipeline = _fetch_report_pipeline(report_pipeline_id)
    pipeline_model = _fetch_pipeline_model(report_pipeline_id)
    report_pipeline_model_id = str(pipeline_model["report_pipeline_model_id"])
    prompt_id = pipeline_model.get("prompt_id")
    model_id = str(pipeline_model["model_id"])
    _set_pipeline_status("processing")

    program_id = pipeline["program_id"]
    template_layout_id = pipeline["template_layout_id"]
    if program_id is None or template_layout_id is None:
        raise ValueError("tbl_report_pipeline must have program_id and template_layout_id set")

    title = pipeline["title"] or "Donor Report"
    subtitle = pipeline["sub_title"]
    location = pipeline["location"]
    orientation = str(pipeline["orientation"] or "portrait")
    front_page_image = pipeline["front_page_image"]
    effective_donor = pipeline["donor_id"]

    meta = _fetch_template_metadata(template_layout_id)
    template_sections = meta.get("sections") or []
    if not template_sections:
        raise ValueError("Template metadata has no sections[]")
    template_image_positions = meta.get("template_image_positions")
    template_metrics = meta.get("template_metrics")

    prompt_body = str(pipeline_model.get("description") or "")
    seed_text: Dict[str, Any] = _seed_from_prompt_description(prompt_body)
    additional_prompt = str(pipeline.get("additional_prompt") or "").strip()
    if additional_prompt:
        seed_text["Additional prompt"] = additional_prompt

    doc_rows = _fetch_pipeline_documents(report_pipeline_id)
    context_chunks: List[str] = []
    budget = settings.MAX_TEXT_LENGTH
    csv_jobs: List[Tuple[str, str]] = []
    image_jobs: List[Tuple[str, str, str, Optional[str]]] = []

    for row in doc_rows:
        doc_id, fn, fp, ftype, chart_type = _resolve_document_paths(row, program_id)
        if _is_tabular_path(fn, ftype):
            # report_type in DB may be 'Bar Chart', 'Line Chart', 'pie Chart', etc.
            ct = normalize_chart_type(chart_type)
            csv_jobs.append((fp, ct))
        elif _is_image_doc(fn, ftype):
            if doc_id is None:
                raise ValueError(
                    f"Image entries in tbl_report_document must reference document_id (report_document_id={row.get('report_document_id')})"
                )
            # Image file path/name/type should come from pipeline runtime rows.
            i_fn, i_fp, i_ftype = _resolve_report_document_file(row)
            image_jobs.append((doc_id, i_fn, i_fp, i_ftype))
        elif _is_pdf_path(fn, ftype) or _is_plain_text_context(fn, ftype):
            # Context docs (PDF/TXT) must come from tbl_report_document for this pipeline.
            c_fn, c_fp, c_ftype = _resolve_report_document_file(row)
            local = download_from_s3(c_fp)
            try:
                if _is_pdf_path(c_fn, c_ftype):
                    text = _extract_pdf_text(local)
                else:
                    with open(local, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                label = f"doc_{doc_id}" if doc_id is not None else c_fn
                piece = text.strip()
                if len(piece) > budget:
                    piece = piece[:budget] + "\n[truncated]"
                context_chunks.append(f"--- {label} ({c_fn}) ---\n{piece}")
                budget = max(0, budget - len(context_chunks[-1]))
                if budget <= 0:
                    break
            finally:
                try:
                    os.remove(local)
                except OSError:
                    pass
        else:
            raise ValueError(
                f"Unsupported document for pipeline: {fn!r} (type={ftype!r}). "
                "Use PDF, TXT, CSV/Excel, or image types."
            )

    if context_chunks:
        seed_text["Reference documents"] = "\n\n".join(context_chunks)

    processed_dir = os.path.join(output_dir, "processed_images")
    os.makedirs(processed_dir, exist_ok=True)

    raw_image_paths: List[str] = []
    meta_by_index: List[Dict[str, Any]] = []
    for doc_id, fn, fp, ftype in image_jobs[: settings.MAX_IMAGES_TO_PROCESS]:
        meta_doc = _fetch_document_metadata_meta(doc_id)
        caption = (meta_doc.get("caption") or "").strip()
        description = (meta_doc.get("description") or "").strip()
        if not caption or not description:
            print(
                f"[report_pipeline] skip image document_id={doc_id}: "
                "need both caption and description in tbl_document_metadata"
            )
            continue

        local_img = download_from_s3(fp)
        safe_fn = os.path.basename(fn) or f"image_{doc_id}"
        dest = os.path.join(processed_dir, f"doc_{doc_id}_{safe_fn}")
        try:
            if os.path.exists(dest):
                os.remove(dest)
            try:
                # os.replace can fail on Windows when source/destination are on different drives.
                os.replace(local_img, dest)
            except OSError:
                shutil.move(local_img, dest)
            raw_image_paths.append(dest)
            meta_by_index.append(
                {
                    "document_id": doc_id,
                    "caption": caption,
                    "description": description,
                    "generation_pricing": meta_doc.get("generation_pricing"),
                }
            )
        except Exception:
            try:
                os.remove(local_img)
            except OSError:
                pass
            try:
                if os.path.exists(dest):
                    os.remove(dest)
            except OSError:
                pass
            raise

    # Roll document-metadata API usage (caption/description + embedding) into report cost.
    # Stored on tbl_document_metadata.meta_data.generation_pricing when metadata was generated.
    for info in meta_by_index:
        gp = info.get("generation_pricing")
        if not isinstance(gp, dict):
            continue
        for b in gp.get("breakdown") or []:
            if not isinstance(b, dict):
                continue
            row = dict(b)
            row["source"] = "document_metadata_generation"
            row["document_id"] = info["document_id"]
            cost_breakdown.append(row)

    bordered_paths = (
        process_images_with_borders(
            raw_image_paths,
            processed_dir,
            border_width=settings.IMAGE_BORDER_WIDTH,
            border_color=settings.IMAGE_BORDER_COLOR,
        )
        if raw_image_paths
        else []
    )

    image_mappings: List[Dict[str, Any]] = []
    for idx, bordered_path in enumerate(bordered_paths):
        info = meta_by_index[idx]
        section, usage = map_image_to_section_text_only(
            template_sections=template_sections,
            seed_text=seed_text,
            description=info["description"],
            caption=info["caption"],
            log_dir=log_dir,
            document_id=info["document_id"],
        )
        cost_breakdown.append(usage)
        image_mappings.append(
            {
                "image_index": idx,
                "path": bordered_path,
                "description": info["description"],
                "section": section,
                "caption": info["caption"],
            }
        )

    csv_data_list: List[Dict[str, Any]] = []
    for csv_fp, chart_type in csv_jobs:
        local_csv = download_from_s3(csv_fp)
        try:
            csv_data_list.append(
                process_csv(csv_path=local_csv, chart_type=chart_type, output_dir=output_dir)
            )
        finally:
            try:
                os.remove(local_csv)
            except OSError:
                pass

    model_title, model_provider, model_key = _fetch_model_row(model_id)
    model_name = _normalize_assembly_model_name(model_provider, model_title)
    api_key, key_source = _assembly_api_key(model_provider, model_key)
    print(
        f"[generate-from-db] assembly model={model_name!r} provider={model_provider!r} key_source={key_source}"
    )

    markdown_content, asm_usage = assemble_report_with_model(
        template_sections=template_sections,
        seed_text=seed_text,
        image_mappings=image_mappings,
        csv_data=csv_data_list if csv_data_list else None,
        template_image_positions=template_image_positions,
        template_metrics=template_metrics,
        output_dir=output_dir,
        log_dir=log_dir,
        api_key=api_key,
        model_name=model_name,
        provider=model_provider,
    )
    asm_usage = dict(asm_usage)
    asm_usage["model_id"] = model_id
    cost_breakdown.append(asm_usage)

    is_valid, validation_errors = validate_markdown(
        markdown=markdown_content,
        image_mappings=image_mappings,
        csv_data=csv_data_list if csv_data_list else None,
        template_sections=template_sections,
    )

    pdf_path = os.path.join(output_dir, "donor_report.pdf")
    logo_path, donor_name = _fetch_donor_branding(effective_donor)
    fp_center_local: Optional[str] = None
    funder_logo_local: Optional[str] = None
    to_cleanup: List[str] = []

    if front_page_image and str(front_page_image).strip():
        s = str(front_page_image).strip()
        try:
            if s.lower().startswith("s3://"):
                fp_center_local = download_from_s3(s)
                to_cleanup.append(fp_center_local)
            elif os.path.isfile(s):
                fp_center_local = os.path.abspath(s)
            else:
                # Allow plain S3 keys like "report-pipeline/front-page/file.jpg".
                fp_center_local = download_from_s3(s)
                to_cleanup.append(fp_center_local)
        except Exception as e:
            raise ValueError(
                f"front_page_image must be a valid local path or resolvable S3 location, got {s!r}: {e}"
            ) from e

    if logo_path:
        try:
            if logo_path.lower().startswith("s3://"):
                funder_logo_local = download_from_s3(logo_path)
                to_cleanup.append(funder_logo_local)
            elif os.path.isfile(logo_path):
                funder_logo_local = os.path.abspath(logo_path)
            else:
                funder_logo_local = download_from_s3(logo_path)
                to_cleanup.append(funder_logo_local)
        except Exception as e:
            print(f"Warning: could not load donor logo from {logo_path!r}: {e}")
            funder_logo_local = None

    try:
        front_page_data = {
            "report_title": title,
            "report_subtitle": subtitle,
            "report_date": None,
            "location": location,
            "report_type": None,
            "funder_logo_path": funder_logo_local,
            "funder_name": donor_name,
            "front_page_image_path": fp_center_local,
        }
        export_to_pdf(
            markdown_content=markdown_content,
            output_path=pdf_path,
            image_dir=output_dir,
            orientation=orientation,
            front_page_data=front_page_data,
            output_dir=output_dir,
        )
    finally:
        for p in to_cleanup:
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass

    s3_key = f"{settings.S3_REPORTS_PREFIX}{run_id}/donor_report.pdf"
    file_uri = upload_local_file(pdf_path, s3_key)

    token_usage = _merge_pricing(cost_breakdown)
    input_token_count, output_token_count, total_pricing, _ = compute_report_cost(
        cost_breakdown
    )

    report_id: Optional[str] = None

    # 1) Persist tbl_report in its own transaction so report creation is not rolled back
    # if cost insert fails on legacy schemas.
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tbl_report
                  (report_pipeline_id, donor_id, program_id, title, file_name, file_path, file_type, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
                RETURNING report_id
                """,
                (
                    report_pipeline_id,
                    effective_donor,
                    program_id,
                    title,
                    "donor_report.pdf",
                    file_uri,
                    "application/pdf",
                ),
            )
            report_id = str(cur.fetchone()[0])
        conn.commit()

    # 2) Best-effort cost write in separate transaction.
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tbl_report_cost
                      (report_id, report_pipeline_model_id,
                       input_token_count, output_token_count, pricing,
                       created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, now(), now())
                    """,
                    (
                        report_id,
                        report_pipeline_model_id,
                        input_token_count,
                        output_token_count,
                        total_pricing,
                    ),
                )
            conn.commit()
        print(
            f"[generate-from-db] tbl_report_cost: in={input_token_count} out={output_token_count} "
            f"pricing={total_pricing}"
        )
    except Exception as e:
        print(f"[generate-from-db] warning: failed tbl_report_cost insert: {e}")

    # 3) Mark pipeline as reported and completed (best-effort).
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tbl_report_pipeline
                    SET is_report = TRUE, status = 'completed', updated_at = now()
                    WHERE report_pipeline_id::text = %s
                    """,
                    (str(report_pipeline_id),),
                )
            conn.commit()
    except Exception as e:
        print(
            f"[generate-from-db] warning: failed to update tbl_report_pipeline.is_report/status: {e}"
        )

    return {
        "success": True,
        "run_id": run_id,
        "output_dir": output_dir,
        "log_dir": log_dir,
        "pdf_path": pdf_path,
        "s3_uri": file_uri,
        "report_pipeline_id": report_pipeline_id,
        "report_pipeline_model_id": report_pipeline_model_id,
        "report_id": report_id,
        "prompt_id": prompt_id,
        "validation": {"is_valid": is_valid, "errors": validation_errors},
        "input_token_count": input_token_count,
        "output_token_count": output_token_count,
        "pricing": total_pricing,
        "token_usage": token_usage,
    }
