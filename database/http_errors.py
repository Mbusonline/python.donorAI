"""
Map PostgreSQL / psycopg connectivity failures to clear HTTP errors.

Route handlers that catch broad ``Exception`` should call
``raise_http_if_database_unreachable(exc)`` before returning a generic 500,
so clients get 503 + structured detail instead of a raw driver traceback.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

# User-facing text (avoid leaking credentials from DATABASE_URL).
_DB_UNAVAILABLE_MESSAGE = (
    "The database could not be reached from this server. "
    "Verify DATABASE_URL (host, port, database name, and credentials), that PostgreSQL "
    "is running and reachable on the network, and that firewalls or security groups "
    "allow the connection. For local development, ensure Postgres is listening or "
    "point DATABASE_URL at your cloud database pooler URL."
)


def _detail_payload(exc: BaseException) -> Dict[str, Any]:
    return {
        "error": "database_unavailable",
        "message": _DB_UNAVAILABLE_MESSAGE,
        "driver_detail": str(exc),
    }


def is_database_connectivity_error(exc: BaseException) -> bool:
    """True when *exc* indicates we could not reach Postgres (connect phase)."""
    try:
        from psycopg import OperationalError
        from psycopg import errors as psycopg_errors
    except ImportError:
        return False

    if isinstance(exc, psycopg_errors.ConnectionTimeout):
        return True
    if not isinstance(exc, OperationalError):
        return False

    msg = str(exc).lower()
    markers = (
        "connection refused",
        "could not connect to server",
        "connection timeout",
        "timeout expired",
        "connection timed out",
        "network is unreachable",
        "no route to host",
    )
    return any(m in msg for m in markers)


def http_exception_database_unavailable(exc: BaseException) -> HTTPException:
    """503 with structured ``detail`` for API clients."""
    return HTTPException(status_code=503, detail=_detail_payload(exc))


def raise_http_if_database_unreachable(exc: BaseException) -> None:
    """
    If *exc* is a DB connectivity error, raise ``HTTPException`` (503).
    Otherwise return without raising.
    """
    if is_database_connectivity_error(exc):
        raise http_exception_database_unavailable(exc)


def register_database_exception_handlers(app: Any) -> None:
    """
    Register FastAPI handlers for psycopg connect-phase errors that are not
    caught by a route (e.g. future routes without a broad ``except``).

    Routes that catch ``Exception`` should still call
    ``raise_http_if_database_unreachable`` first, because those catch blocks
    run before these handlers.
    """
    try:
        from psycopg import OperationalError
        from psycopg import errors as psycopg_errors
    except ImportError:
        return

    import logging

    from fastapi import Request
    from fastapi.responses import JSONResponse

    log = logging.getLogger(__name__)

    def _body(exc: BaseException) -> Dict[str, Any]:
        return {"success": False, **_detail_payload(exc)}

    @app.exception_handler(psycopg_errors.ConnectionTimeout)
    async def _connection_timeout_handler(
        request: Request, exc: psycopg_errors.ConnectionTimeout
    ) -> JSONResponse:
        log.warning("Database connection timeout: %s", exc)
        return JSONResponse(status_code=503, content=_body(exc))

    @app.exception_handler(OperationalError)
    async def _operational_error_handler(
        request: Request, exc: OperationalError
    ) -> JSONResponse:
        if isinstance(exc, psycopg_errors.ConnectionTimeout):
            log.warning("Database connection timeout: %s", exc)
            return JSONResponse(status_code=503, content=_body(exc))
        if is_database_connectivity_error(exc):
            log.warning("Database unreachable (OperationalError): %s", exc)
            return JSONResponse(status_code=503, content=_body(exc))
        log.exception("Database OperationalError (non-connect): %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "database_error",
                "message": "A database error occurred.",
                "driver_detail": str(exc),
            },
        )
