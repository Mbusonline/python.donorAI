import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

from openai import OpenAI
from PIL import Image

import settings
from database.connection import connect
from prompts.image_analysis import IMAGE_DESCRIPTION_PROMPT
from services.api_logger import log_gemini_api_call, log_openai_api_call
from services.s3_utils import download_from_s3


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _merge_pricing(breakdown: list[Dict[str, Any]]) -> Dict[str, Any]:
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


def normalize_document_id(document_id: Union[str, int]) -> str:
    """Primary keys may be UUID strings (Node) or legacy integer ids."""
    if document_id is None:
        raise ValueError("document_id is required")
    s = str(document_id).strip()
    if not s:
        raise ValueError("document_id is required")
    return s


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    if "```json" in text:
        return text.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


def _is_image_path(path: str) -> bool:
    _, ext = os.path.splitext(path or "")
    return ext.lower() in IMAGE_EXTENSIONS


@dataclass(frozen=True)
class DocumentRow:
    document_id: str
    file_name: str
    file_path: str
    file_type: Optional[str]
    is_metadata: bool


def _download_from_s3(file_path: str) -> str:
    # Backwards-compatible wrapper
    return download_from_s3(file_path)


def _generate_caption_and_description(
    image_path: str, log_dir: str
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found in settings")

    import google.generativeai as genai

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    print(
        f"[gemini] Calling model={settings.GEMINI_MODEL} operation=document_image_description "
        f"image={os.path.basename(image_path)}"
    )
    img = Image.open(image_path)
    prompt = IMAGE_DESCRIPTION_PROMPT

    response = model.generate_content([prompt, img])
    result_text = _extract_json(response.text)

    input_tokens = None
    output_tokens = None
    if hasattr(response, "usage_metadata"):
        usage = response.usage_metadata
        input_tokens = getattr(usage, "prompt_token_count", None)
        output_tokens = getattr(usage, "candidates_token_count", None)

    obj = json.loads(result_text) if result_text else {}
    description = (obj.get("description") or "").strip() or "Image description unavailable"
    caption = (obj.get("caption") or "").strip() or "Image from donor report"

    log_gemini_api_call(
        model=settings.GEMINI_MODEL,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        prompt_length=len(prompt) if input_tokens is None else None,
        response_length=len(result_text) if output_tokens is None else None,
        image_path=image_path,
        operation="document_image_description",
        success=True,
        log_dir=log_dir,
    )
    print(
        f"[gemini] Completed operation=document_image_description "
        f"input_tokens={input_tokens} output_tokens={output_tokens}"
    )

    usage = {
        "operation": "document_image_description",
        "provider": "google",
        "model": settings.GEMINI_MODEL,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    return {"description": description, "caption": caption}, usage


def _embed_text(text: str, log_dir: str) -> Tuple[list[float], Dict[str, Any]]:
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not found in settings")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    print(
        f"[openai] Calling model={model} operation=document_embedding chars={len(text)}"
    )
    resp = client.embeddings.create(model=model, input=text)
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
        operation="document_embedding",
        success=True,
        log_dir=log_dir,
    )
    print(
        f"[openai] Completed operation=document_embedding "
        f"input_tokens={input_tokens} total_tokens={output_tokens}"
    )

    usage = {
        "operation": "document_embedding",
        "provider": "openai",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    return embedding, usage


def _fetch_document_row(document_id: str) -> DocumentRow:
    document_id = normalize_document_id(document_id)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT document_id, file_name, file_path, file_type, COALESCE(is_metadata, FALSE)
                FROM tbl_document
                WHERE document_id = %s
                """,
                (document_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Document not found for document_id={document_id}")
            raw_id = row[0]
            id_str = str(raw_id) if raw_id is not None else document_id
            return DocumentRow(
                document_id=id_str,
                file_name=str(row[1]),
                file_path=str(row[2]),
                file_type=row[3] if row[3] is None else str(row[3]),
                is_metadata=bool(row[4]),
            )


def _has_vector_type(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_type WHERE typname = 'vector' LIMIT 1")
        return cur.fetchone() is not None


def _upsert_metadata_without_unique(
    cur,
    *,
    document_id: str,
    meta_json: str,
    vector_param: Optional[Any],
    use_vector_cast: bool = False,
) -> Any:
    """
    Upsert metadata by UPDATE-then-INSERT.
    Works even when no UNIQUE(document_id) exists on tbl_document_metadata.
    """
    if vector_param is None:
        cur.execute(
            """
            UPDATE tbl_document_metadata
            SET meta_data = %s::jsonb,
                vector_data = NULL,
                is_active = TRUE,
                updated_at = now()
            WHERE document_id = %s
            RETURNING document_metadata_id
            """,
            (meta_json, document_id),
        )
    elif use_vector_cast:
        cur.execute(
            """
            UPDATE tbl_document_metadata
            SET meta_data = %s::jsonb,
                vector_data = %s::vector,
                is_active = TRUE,
                updated_at = now()
            WHERE document_id = %s
            RETURNING document_metadata_id
            """,
            (meta_json, vector_param, document_id),
        )
    else:
        cur.execute(
            """
            UPDATE tbl_document_metadata
            SET meta_data = %s::jsonb,
                vector_data = %s,
                is_active = TRUE,
                updated_at = now()
            WHERE document_id = %s
            RETURNING document_metadata_id
            """,
            (meta_json, vector_param, document_id),
        )

    row = cur.fetchone()
    if row:
        return row[0]

    if vector_param is None:
        cur.execute(
            """
            INSERT INTO tbl_document_metadata
              (document_id, meta_data, vector_data, is_active, created_at, updated_at)
            VALUES (%s, %s::jsonb, NULL, TRUE, now(), now())
            RETURNING document_metadata_id
            """,
            (document_id, meta_json),
        )
    elif use_vector_cast:
        cur.execute(
            """
            INSERT INTO tbl_document_metadata
              (document_id, meta_data, vector_data, is_active, created_at, updated_at)
            VALUES (%s, %s::jsonb, %s::vector, TRUE, now(), now())
            RETURNING document_metadata_id
            """,
            (document_id, meta_json, vector_param),
        )
    else:
        cur.execute(
            """
            INSERT INTO tbl_document_metadata
              (document_id, meta_data, vector_data, is_active, created_at, updated_at)
            VALUES (%s, %s::jsonb, %s, TRUE, now(), now())
            RETURNING document_metadata_id
            """,
            (document_id, meta_json, vector_param),
        )
    return cur.fetchone()[0]


def process_document_metadata(document_id: Union[str, int], log_dir: str) -> Dict[str, Any]:
    """
    Pipeline:
      1) Load document row from tbl_document
      2) Download from S3 using file_path
      3) If image -> generate description+caption (Gemini Vision)
      4) Embed text -> vector
      5) Upsert metadata into tbl_document_metadata (meta_data includes generation_pricing for report cost rollup)
      6) Update tbl_document.is_metadata = true
    """
    document_id = normalize_document_id(document_id)
    doc = _fetch_document_row(document_id)

    if doc.is_metadata:
        return {
            "document_id": doc.document_id,
            "skipped": True,
            "reason": "is_metadata already true",
        }

    file_type = (doc.file_type or "").lower().strip()
    is_image_by_type = file_type.startswith("image/")
    is_image_by_ext = _is_image_path(doc.file_name) or _is_image_path(doc.file_path)

    if not (is_image_by_type or is_image_by_ext):
        raise ValueError(
            f"Document {doc.document_id} is not an image (file_type={doc.file_type!r})."
        )

    local_path = _download_from_s3(doc.file_path)
    try:
        meta, gemini_usage = _generate_caption_and_description(local_path, log_dir=log_dir)
        embed_text = f"{meta['caption']}\n\n{meta['description']}"
        embedding, openai_usage = _embed_text(embed_text, log_dir=log_dir)

        generation_pricing = _merge_pricing([gemini_usage, openai_usage])
        meta_payload = {**meta, "generation_pricing": generation_pricing}

        vector_value = embedding
        with connect() as conn:
            has_vector = _has_vector_type(conn)
            meta_json = json.dumps(meta_payload)
            with conn.cursor() as cur:
                if has_vector:
                    try:
                        from pgvector.psycopg import register_vector  # type: ignore
                        from pgvector import Vector  # type: ignore

                        register_vector(conn)
                        metadata_id = _upsert_metadata_without_unique(
                            cur,
                            document_id=doc.document_id,
                            meta_json=meta_json,
                            vector_param=Vector(vector_value),
                        )
                    except Exception:
                        # Fallback: store vector via explicit cast (no adapter)
                        vector_literal = "[" + ",".join(str(float(x)) for x in vector_value) + "]"
                        metadata_id = _upsert_metadata_without_unique(
                            cur,
                            document_id=doc.document_id,
                            meta_json=meta_json,
                            vector_param=vector_literal,
                            use_vector_cast=True,
                        )
                else:
                    # Database has no pgvector extension/type. Save metadata and continue.
                    metadata_id = _upsert_metadata_without_unique(
                        cur,
                        document_id=doc.document_id,
                        meta_json=meta_json,
                        vector_param=None,
                    )
                cur.execute(
                    "UPDATE tbl_document SET is_metadata = TRUE, updated_at = now() WHERE document_id = %s",
                    (doc.document_id,),
                )
            conn.commit()

        try:
            metadata_id_out = int(metadata_id)
        except (TypeError, ValueError):
            metadata_id_out = str(metadata_id)

        return {
            "document_id": doc.document_id,
            "document_metadata_id": metadata_id_out,
            "meta_data": meta_payload,
            "pricing": generation_pricing,
        }
    finally:
        try:
            os.remove(local_path)
        except Exception:
            pass

