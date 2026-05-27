import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from openai import OpenAI

import settings
from database.connection import connect
from services.api_logger import log_openai_api_call
from services.s3_utils import download_from_s3
from services.template_extractor import (
    extract_image_positions_from_pdf,
    extract_sections_with_gemini,
    extract_text_from_pdf,
)


@dataclass(frozen=True)
class TemplateLayoutRow:
    template_layout_id: str
    template_id: str
    file_name: str
    file_path: str
    file_type: Optional[str]
    template_version: Optional[str]


def _has_vector_type(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_type WHERE typname = 'vector' LIMIT 1")
        return cur.fetchone() is not None


def _fetch_template_layout(template_layout_id: str) -> TemplateLayoutRow:
    """
    Loads the active tbl_template_layout row by template_layout_id (not template_id).
    Metadata is stored per layout in tbl_template_layout_metadata.
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tl.template_layout_id,
                       tl.template_id,
                       tl.file_name,
                       tl.file_path,
                       tl.file_type,
                       t.version
                FROM tbl_template_layout tl
                JOIN tbl_template t ON t.template_id = tl.template_id
                WHERE tl.template_layout_id = %s
                  AND tl.is_active = TRUE
                """,
                (template_layout_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(
                    f"No active template layout found for template_layout_id={template_layout_id}"
                )
            return TemplateLayoutRow(
                template_layout_id=str(row[0]),
                template_id=str(row[1]),
                file_name=str(row[2]),
                file_path=str(row[3]),
                file_type=row[4] if row[4] is None else str(row[4]),
                template_version=row[5] if row[5] is None else str(row[5]),
            )


def _embed_text(text: str, log_dir: str) -> list[float]:
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not found in settings")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    try:
        resp = client.embeddings.create(model=model, input=text)
    except Exception as e:
        log_openai_api_call(
            model=model,
            operation="template_embedding",
            success=False,
            error=str(e),
            log_dir=log_dir,
        )
        raise

    embedding = resp.data[0].embedding

    input_tokens = None
    output_tokens = None
    if hasattr(resp, "usage") and resp.usage:
        input_tokens = getattr(resp.usage, "prompt_tokens", None)
        output_tokens = getattr(resp.usage, "total_tokens", None)

    log_openai_api_call(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        prompt_length=len(text) if input_tokens is None else None,
        response_length=None,
        operation="template_embedding",
        success=True,
        log_dir=log_dir,
    )

    return embedding


def process_template_metadata(template_layout_id: str, log_dir: str) -> Dict[str, Any]:
    """
    Pipeline:
      1) Load tbl_template_layout by template_layout_id
      2) Download PDF from S3 using tbl_template_layout.file_path
      3) Extract text + image positions (PDF)
      4) Use Gemini to extract sections
      5) Store meta_data + vector_data into tbl_template_layout_metadata (keyed by template_layout_id)
    """
    layout = _fetch_template_layout(template_layout_id)

    file_type = (layout.file_type or "").lower().strip()
    if file_type and file_type != "application/pdf" and not layout.file_name.lower().endswith(".pdf"):
        raise ValueError(
            f"Template layout is not a PDF (file_type={layout.file_type!r}, file_name={layout.file_name!r})."
        )

    local_pdf = download_from_s3(layout.file_path)
    try:
        text_content = extract_text_from_pdf(local_pdf)
        template_image_positions = extract_image_positions_from_pdf(local_pdf)
        template_metrics = {
            "character_count": len(text_content),
            "word_count": len(text_content.split()),
            "paragraph_count": len([p for p in text_content.split("\n\n") if p.strip()]),
            "line_count": len([l for l in text_content.split("\n") if l.strip()]),
        }

        # Use Gemini to extract sections; write prompt artifacts into log_dir
        os.makedirs(log_dir, exist_ok=True)
        sections = extract_sections_with_gemini(text_content, log_dir=log_dir, output_dir=log_dir)

        meta_data = {
            "sections": sections,
            "template_image_positions": template_image_positions,
            "template_metrics": template_metrics,
            "source": {
                "template_id": layout.template_id,
                "template_layout_id": layout.template_layout_id,
                "file_name": layout.file_name,
                "file_path": layout.file_path,
                "file_type": layout.file_type,
            },
        }

        embed_text = json.dumps(
            {
                "sections": sections,
                "template_metrics": template_metrics,
            },
            ensure_ascii=False,
        )
        embedding = _embed_text(embed_text, log_dir=log_dir)

        # Store using pgvector adapter if available, otherwise cast.
        # If vector type is unavailable in DB, save metadata and set vector_data = NULL.
        with connect() as conn:
            has_vector = _has_vector_type(conn)
            with conn.cursor() as cur:
                if has_vector:
                    try:
                        from pgvector.psycopg import register_vector  # type: ignore
                        from pgvector import Vector  # type: ignore

                        register_vector(conn)
                        cur.execute(
                            """
                            UPDATE tbl_template_layout_metadata
                            SET meta_data = %s::jsonb,
                                vector_data = %s,
                                version = %s,
                                is_active = TRUE,
                                updated_at = now()
                            WHERE template_layout_id = %s
                            RETURNING template_layout_metadata_id
                            """,
                            (
                                json.dumps(meta_data),
                                Vector(embedding),
                                layout.template_version,
                                layout.template_layout_id,
                            ),
                        )
                        row = cur.fetchone()
                        if row:
                            metadata_id = row[0]
                        else:
                            cur.execute(
                                """
                                INSERT INTO tbl_template_layout_metadata
                                  (template_layout_id, meta_data, vector_data, version, is_active, created_at, updated_at)
                                VALUES (%s, %s::jsonb, %s, %s, TRUE, now(), now())
                                RETURNING template_layout_metadata_id
                                """,
                                (
                                    layout.template_layout_id,
                                    json.dumps(meta_data),
                                    Vector(embedding),
                                    layout.template_version,
                                ),
                            )
                            metadata_id = cur.fetchone()[0]
                    except Exception:
                        vector_literal = "[" + ",".join(str(float(x)) for x in embedding) + "]"
                        cur.execute(
                            """
                            UPDATE tbl_template_layout_metadata
                            SET meta_data = %s::jsonb,
                                vector_data = %s::vector,
                                version = %s,
                                is_active = TRUE,
                                updated_at = now()
                            WHERE template_layout_id = %s
                            RETURNING template_layout_metadata_id
                            """,
                            (
                                json.dumps(meta_data),
                                vector_literal,
                                layout.template_version,
                                layout.template_layout_id,
                            ),
                        )
                        row = cur.fetchone()
                        if row:
                            metadata_id = row[0]
                        else:
                            cur.execute(
                                """
                                INSERT INTO tbl_template_layout_metadata
                                  (template_layout_id, meta_data, vector_data, version, is_active, created_at, updated_at)
                                VALUES (%s, %s::jsonb, %s::vector, %s, TRUE, now(), now())
                                RETURNING template_layout_metadata_id
                                """,
                                (
                                    layout.template_layout_id,
                                    json.dumps(meta_data),
                                    vector_literal,
                                    layout.template_version,
                                ),
                            )
                            metadata_id = cur.fetchone()[0]
                else:
                    cur.execute(
                        """
                        UPDATE tbl_template_layout_metadata
                        SET meta_data = %s::jsonb,
                            vector_data = NULL,
                            version = %s,
                            is_active = TRUE,
                            updated_at = now()
                        WHERE template_layout_id = %s
                        RETURNING template_layout_metadata_id
                        """,
                        (
                            json.dumps(meta_data),
                            layout.template_version,
                            layout.template_layout_id,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        metadata_id = row[0]
                    else:
                        cur.execute(
                            """
                            INSERT INTO tbl_template_layout_metadata
                              (template_layout_id, meta_data, vector_data, version, is_active, created_at, updated_at)
                            VALUES (%s, %s::jsonb, NULL, %s, TRUE, now(), now())
                            RETURNING template_layout_metadata_id
                            """,
                            (
                                layout.template_layout_id,
                                json.dumps(meta_data),
                                layout.template_version,
                            ),
                        )
                        metadata_id = cur.fetchone()[0]

                cur.execute(
                    "UPDATE tbl_template_layout SET is_metadata = TRUE, updated_at = now() WHERE template_layout_id = %s",
                    (layout.template_layout_id,),
                )
            conn.commit()

        return {
            "template_id": layout.template_id,
            "template_layout_id": layout.template_layout_id,
            "template_layout_metadata_id": int(metadata_id),
            "meta_data": meta_data,
        }
    finally:
        try:
            os.remove(local_pdf)
        except Exception:
            pass

