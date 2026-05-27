"""
Map OpenAI / Gemini provider API failures to clear HTTP responses.

Call ``raise_http_if_provider_error(exc)`` in route ``except Exception`` blocks
before returning a generic 500.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException


def _openai_error_code(exc: BaseException) -> Optional[str]:
    code = getattr(exc, "code", None)
    if code:
        return str(code)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("code"):
            return str(err["code"])
    return None


def _openai_user_message(exc: BaseException, *, status_code: int) -> str:
    code = (_openai_error_code(exc) or "").lower()
    if code == "insufficient_quota" or "quota" in str(exc).lower():
        return (
            "OpenAI API quota exceeded. Check billing and plan limits at "
            "https://platform.openai.com/account/billing"
        )
    if status_code == 429:
        return "OpenAI rate limit exceeded. Retry later or reduce request volume."
    if status_code == 401:
        return "OpenAI API key is invalid or unauthorized. Check OPENAI_API_KEY or tbl_model.private_key."
    if status_code == 403:
        return "OpenAI API access denied for this key or model."
    return "OpenAI API request failed."


def http_exception_from_openai(exc: BaseException) -> HTTPException:
    status_code = int(getattr(exc, "status_code", None) or 502)
    code = _openai_error_code(exc)
    error_key = "openai_api_error"
    if code == "insufficient_quota":
        error_key = "openai_quota_exceeded"
    elif status_code == 429:
        error_key = "openai_rate_limited"

    detail: Dict[str, Any] = {
        "error": error_key,
        "message": _openai_user_message(exc, status_code=status_code),
        "provider": "openai",
        "status_code": status_code,
    }
    if code:
        detail["code"] = code
    detail["driver_detail"] = str(exc)
    return HTTPException(status_code=status_code, detail=detail)


def http_exception_from_gemini(exc: BaseException) -> HTTPException:
    msg = str(exc).lower()
    status_code = 429 if "quota" in msg or "resource exhausted" in msg or "429" in msg else 502
    detail: Dict[str, Any] = {
        "error": "gemini_api_error",
        "message": (
            "Google Gemini API request failed. Check GEMINI_API_KEY, quota, and model name."
        ),
        "provider": "google",
        "driver_detail": str(exc),
    }
    if "quota" in msg or "resource exhausted" in msg:
        detail["error"] = "gemini_quota_exceeded"
        detail["message"] = (
            "Gemini API quota or rate limit exceeded. Check Google AI Studio billing and limits."
        )
        status_code = 429
    return HTTPException(status_code=status_code, detail=detail)


def http_exception_from_provider(exc: BaseException) -> Optional[HTTPException]:
    """Return an HTTPException for known provider errors, or None."""
    try:
        from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError
    except ImportError:
        return None

    if isinstance(exc, RateLimitError):
        return http_exception_from_openai(exc)
    if isinstance(exc, AuthenticationError):
        return http_exception_from_openai(exc)
    if isinstance(exc, APIStatusError):
        return http_exception_from_openai(exc)
    if isinstance(exc, APIConnectionError):
        return HTTPException(
            status_code=503,
            detail={
                "error": "openai_unreachable",
                "message": "Could not reach the OpenAI API. Check network connectivity.",
                "provider": "openai",
                "driver_detail": str(exc),
            },
        )

    # google.generativeai / google.api_core
    exc_name = type(exc).__name__
    if exc_name in ("ResourceExhausted", "TooManyRequests"):
        return http_exception_from_gemini(exc)
    mod = type(exc).__module__ or ""
    if mod.startswith("google.") and ("quota" in str(exc).lower() or "429" in str(exc)):
        return http_exception_from_gemini(exc)

    return None


def raise_http_if_provider_error(exc: BaseException) -> None:
    """Raise HTTPException with a clear client-facing payload when *exc* is from an LLM provider."""
    http_exc = http_exception_from_provider(exc)
    if http_exc is not None:
        raise http_exc


def register_provider_exception_handlers(app: Any) -> None:
    """Register handlers for provider errors not caught in routes."""
    try:
        from openai import APIStatusError, RateLimitError
    except ImportError:
        return

    import logging

    from fastapi import Request
    from fastapi.responses import JSONResponse

    log = logging.getLogger(__name__)

    def _json(exc: BaseException, http_exc: HTTPException) -> JSONResponse:
        detail = http_exc.detail
        body: Dict[str, Any] = (
            {"success": False, **detail}
            if isinstance(detail, dict)
            else {"success": False, "message": str(detail)}
        )
        return JSONResponse(status_code=http_exc.status_code, content=body)

    @app.exception_handler(RateLimitError)
    async def _openai_rate_limit(request: Request, exc: RateLimitError) -> JSONResponse:
        log.warning("OpenAI rate limit / quota: %s", exc)
        return _json(exc, http_exception_from_openai(exc))

    @app.exception_handler(APIStatusError)
    async def _openai_api_status(request: Request, exc: APIStatusError) -> JSONResponse:
        log.warning("OpenAI API status error: %s", exc)
        return _json(exc, http_exception_from_openai(exc))
