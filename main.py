"""
Donor report API — DB-driven pipeline and metadata helpers only.
"""

import os
import traceback
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, field_validator
from anyio import to_thread

import settings
from services.document_metadata_processor import normalize_document_id, process_document_metadata
from services.redis_queue import enqueue_documents_bulk, mark_done, queue_length
from services.template_metadata_processor import process_template_metadata
from services.report_pipeline_service import run_db_report_pipeline
from database.connection import connect
from database.http_errors import (
    raise_http_if_database_unreachable,
    register_database_exception_handlers,
)
from services.provider_http_errors import (
    raise_http_if_provider_error,
    register_provider_exception_handlers,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Postgres: verify reachability unless skipped (e.g. DATABASE_URL still points at localhost with no server).
    from database.connection import connect

    def _pg_check() -> None:
        with connect(connect_timeout_seconds=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

    if settings.SKIP_STARTUP_DB_CHECK:
        print(
            "[startup] SKIP_STARTUP_DB_CHECK=1: skipping Postgres check. "
            "Set DATABASE_URL to your real DB; DB routes will fail until it connects."
        )
    else:
        try:
            await to_thread.run_sync(_pg_check)
        except Exception as e:
            print(
                "[startup] Postgres unreachable. "
                "Fix DATABASE_URL (e.g. Supabase pooler URL) or start PostgreSQL on the host/port in the URL. "
                "To start the API anyway, set SKIP_STARTUP_DB_CHECK=1 in .env.\n"
                f"  Error: {e}"
            )
            raise

    # Redis (optional): set socket timeouts to avoid startup hangs
    try:
        import redis  # type: ignore

        r = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await to_thread.run_sync(r.ping)
    except Exception as e:
        print(
            f"[startup] Redis not reachable (optional). REDIS_URL={settings.REDIS_URL!r}. Error: {e}"
        )

    yield


app = FastAPI(
    title="Donor Report API",
    description="Postgres-driven report generation and document/template metadata",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_database_exception_handlers(app)
register_provider_exception_handlers(app)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    error_details = []
    for error in errors:
        error_details.append(
            {
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "detail": error_details,
            "body": str(exc.body) if hasattr(exc, "body") else None,
        },
    )


class ProcessDocumentMetadataRequest(BaseModel):
    document_id: str

    @field_validator("document_id", mode="before")
    @classmethod
    def coerce_document_id(cls, v):
        return normalize_document_id(v)


class EnqueueDocumentMetadataRequest(BaseModel):
    """
    Queue documents for the metadata worker (fast bulk LPUSH), then run
    `python workers/document_metadata_worker.py` to generate caption/description one-by-one.

    Use `document_id` for one id, or `document_ids` for many.
    Values must be `tbl_document.document_id` (UUID strings). Ensure `tbl_document.file_path`
    is set before the worker runs (processor loads the image from S3 using that path).
    """

    document_id: Optional[str] = None
    document_ids: Optional[List[str]] = None
    # False (default): push all ids to Redis in one fast batch; worker processes sequentially.
    # True: skip Redis and run metadata in this API process after the response (no worker).
    inline_process: bool = False

    @field_validator("document_id", mode="before")
    @classmethod
    def coerce_document_id_enqueue(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return normalize_document_id(v)

    @field_validator("document_ids", mode="before")
    @classmethod
    def coerce_document_ids_enqueue(cls, v):
        if v is None:
            return None
        if not isinstance(v, list):
            raise ValueError("document_ids must be an array")
        out = []
        for item in v:
            if item is None or (isinstance(item, str) and not str(item).strip()):
                continue
            out.append(normalize_document_id(item))
        return out or None


class ProcessTemplateMetadataRequest(BaseModel):
    template_layout_id: str

    @field_validator("template_layout_id", mode="before")
    @classmethod
    def coerce_template_layout_id(cls, v):
        if v is None:
            raise ValueError("template_layout_id is required")
        s = str(v).strip()
        if not s:
            raise ValueError("template_layout_id is required")
        return s


class GenerateReportFromDbRequest(BaseModel):
    report_pipeline_id: str

    @field_validator("report_pipeline_id", mode="before")
    @classmethod
    def coerce_report_pipeline_id(cls, v):
        if v is None:
            raise ValueError("report_pipeline_id is required")
        s = str(v).strip()
        if not s:
            raise ValueError("report_pipeline_id is required")
        return s


@app.get("/")
def read_root():
    return {
        "message": "Donor Report API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/health",
        "endpoints": {
            "generate_from_db": "POST /api/reports/generate-from-db",
            "document_metadata": "POST /api/documents/process-metadata",
            "enqueue_metadata": "POST /api/documents/enqueue-metadata",
            "template_metadata": "POST /api/templates/process-metadata",
        },
    }


@app.get("/api/health")
def health_check():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/api/documents/process-metadata")
def process_metadata(request: ProcessDocumentMetadataRequest):
    try:
        os.makedirs("logs", exist_ok=True)
        log_dir = os.path.join("logs", f"document_{request.document_id}")
        os.makedirs(log_dir, exist_ok=True)

        result = process_document_metadata(
            document_id=request.document_id,
            log_dir=log_dir,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise_http_if_database_unreachable(e)
        raise_http_if_provider_error(e)
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


def _enqueue_id_list(request: EnqueueDocumentMetadataRequest) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    if request.document_id:
        if request.document_id not in seen:
            seen.add(request.document_id)
            out.append(request.document_id)
    if request.document_ids:
        for did in request.document_ids:
            if did not in seen:
                seen.add(did)
                out.append(did)
    if not out:
        raise HTTPException(
            status_code=400,
            detail="Provide document_id or a non-empty document_ids array (tbl_document.document_id values).",
        )
    return out


def _background_process_document_metadata(document_id: str) -> None:
    """Generate caption/description + embedding; mark Redis done key if present."""
    try:
        os.makedirs("logs", exist_ok=True)
        log_dir = os.path.join("logs", f"document_{document_id}")
        os.makedirs(log_dir, exist_ok=True)
        print(f"[enqueue-metadata] inline_process start document_id={document_id!r}")
        process_document_metadata(document_id=document_id, log_dir=log_dir)
        try:
            mark_done(document_id)
        except Exception:
            pass
        print(f"[enqueue-metadata] inline_process done document_id={document_id!r}")
    except Exception as e:
        print(f"[enqueue-metadata] inline_process failed document_id={document_id!r}: {e}")
        print(traceback.format_exc())


@app.post("/api/documents/enqueue-metadata")
def enqueue_metadata(
    request: EnqueueDocumentMetadataRequest,
    background_tasks: BackgroundTasks,
):
    try:
        ids = _enqueue_id_list(request)
        print(
            f"[enqueue-metadata] request count={len(ids)} inline_process={request.inline_process!r}"
        )
        results: List[dict] = []

        if request.inline_process:
            for did in ids:
                background_tasks.add_task(_background_process_document_metadata, did)
                results.append(
                    {
                        "document_id": did,
                        "enqueued": False,
                        "reason": "inline_process_scheduled",
                    }
                )
            qlen = queue_length()
            data: dict = {
                "results": results,
                "queue_length": qlen,
                "count": len(ids),
                "inline_process": True,
                "note": "Caption/description run in API background after this response; check logs/ and tbl_document_metadata.",
            }
        else:
            qlen_before = queue_length()
            results = enqueue_documents_bulk(ids)
            qlen = queue_length()
            pushed = sum(1 for r in results if r.get("enqueued"))
            print(
                f"[enqueue-metadata] done ids_in_request={len(ids)} "
                f"lpush_ok_count={pushed} queue_length_before={qlen_before} "
                f"queue_length_after={qlen} (after=items still waiting in Redis, not batch size)"
            )
            data = {
                "results": results,
                "queue_length": qlen,
                "queue_length_before": qlen_before,
                "lpush_ok_count": pushed,
                "count": len(ids),
                "inline_process": False,
                "note": "All accepted ids are on Redis; run workers/document_metadata_worker.py for sequential caption/description. Set inline_process=true to process inside the API instead.",
            }

        if len(ids) == 1:
            data["document_id"] = ids[0]
            data.update(results[0])
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        raise_http_if_database_unreachable(e)
        raise_http_if_provider_error(e)
        print(f"[enqueue-metadata] error: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


def _mark_report_pipeline_failed(report_pipeline_id: Optional[str]) -> None:
    """Best-effort: set tbl_report_pipeline.status to failed when generation did not succeed."""
    if not report_pipeline_id:
        return
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tbl_report_pipeline
                    SET status = 'failed', updated_at = now()
                    WHERE report_pipeline_id::text = %s
                    """,
                    (str(report_pipeline_id),),
                )
            conn.commit()
    except Exception as inner:
        print(
            f"[generate-from-db] warning: failed to set pipeline status=failed "
            f"report_pipeline_id={report_pipeline_id!r}: {inner}"
        )


@app.post("/api/reports/generate-from-db")
def generate_report_from_db(request: GenerateReportFromDbRequest):
    pipeline_id = request.report_pipeline_id
    try:
        print(
            f"[generate-from-db] request report_pipeline_id={pipeline_id!r}"
        )
        result = run_db_report_pipeline(report_pipeline_id=pipeline_id)
        print(
            "[generate-from-db] success "
            f"report_pipeline_id={pipeline_id!r} "
            f"report_id={result.get('report_id')} s3_uri={result.get('s3_uri')}"
        )
        return {"success": True, "data": result}
    except ValueError as e:
        _mark_report_pipeline_failed(pipeline_id)
        emsg = str(e)
        print(
            f"[generate-from-db] value_error report_pipeline_id={pipeline_id!r}: {e}"
        )
        if "permission denied" in emsg.lower() or "temporary files" in emsg.lower():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "chart_tmp_not_writable",
                    "message": emsg,
                    "hint": (
                        "chown/chmod /var/www/donor_report/python/tmp for the API user, "
                        "or set KALEIDO_TMP_DIR=/var/www/donor_report/python/tmp in .env"
                    ),
                },
            )
        if "kaleido" in emsg.lower() or (
            "chrome" in emsg.lower() and "chart" in emsg.lower()
        ):
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "chart_dependency_missing",
                    "message": emsg,
                    "hint": (
                        "Install Chrome for Kaleido on the API server: "
                        "source .venv/bin/activate && plotly_get_chrome"
                    ),
                },
            )
        raise HTTPException(status_code=400, detail=emsg)
    except FileNotFoundError as e:
        _mark_report_pipeline_failed(pipeline_id)
        print(
            f"[generate-from-db] file_not_found report_pipeline_id={pipeline_id!r}: {e}"
        )
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        _mark_report_pipeline_failed(pipeline_id)
        raise
    except Exception as e:
        _mark_report_pipeline_failed(pipeline_id)
        raise_http_if_database_unreachable(e)
        raise_http_if_provider_error(e)

        emsg = str(e)
        if "operator does not exist" in emsg and "uuid = numeric" in emsg:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid report_pipeline_id or related IDs not found/mismatched in DB. "
                    "Verify the UUID values exist in pipeline, prompt, and model mappings."
                ),
            )
        print(
            f"[generate-from-db] error report_pipeline_id={pipeline_id!r}: {e}"
        )
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/templates/process-metadata")
def process_template_layout_metadata(request: ProcessTemplateMetadataRequest):
    try:
        os.makedirs("logs", exist_ok=True)
        log_dir = os.path.join("logs", f"template_layout_{request.template_layout_id}")
        os.makedirs(log_dir, exist_ok=True)

        result = process_template_metadata(
            template_layout_id=request.template_layout_id, log_dir=log_dir
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise_http_if_database_unreachable(e)
        raise_http_if_provider_error(e)
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
