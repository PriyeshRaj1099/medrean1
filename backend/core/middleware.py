"""HTTP middlewares: request context/logging, security headers, body size guard."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.core.config import get_settings
from backend.core.logging_config import set_request_id

logger = logging.getLogger("medilens.http")

REQUEST_ID_HEADER = "X-Request-ID"

# Paths that stream user files (PDF/image viewers) keep a slightly relaxed CSP.
_FILE_PATHS = ("/file", "/export")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, logs method/path/status/duration (no query strings)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        set_request_id(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.exception(
                "%s %s failed after %dms",
                request.method,
                request.url.path,
                duration_ms,
                extra={"event": "http_request_failed"},
            )
            raise
        duration_ms = int((time.perf_counter() - start) * 1000)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "%s %s -> %d (%dms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={"event": "http_request", "duration_ms": duration_ms},
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth response headers."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        settings = get_settings()
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-site",
        }
        is_file_response = any(path in request.url.path for path in _FILE_PATHS)
        headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'"
            if not is_file_response
            else "default-src 'none'; object-src 'self'; frame-ancestors 'none'"
        )
        if settings.is_production:
            headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        for key, value in headers.items():
            response.headers.setdefault(key, value)
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects requests whose declared body exceeds the upload limit early."""

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        content_length = request.headers.get("content-length")
        # Allow multipart overhead on top of the file limit.
        hard_limit = settings.max_upload_size_bytes + (1024 * 1024)
        if content_length and content_length.isdigit() and int(content_length) > hard_limit:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "payload_too_large",
                        "message": (
                            f"The request is too large. The maximum file size is "
                            f"{settings.max_upload_size_mb} MB."
                        ),
                    }
                },
            )
        return await call_next(request)
