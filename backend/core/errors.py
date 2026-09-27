"""Application error types and safe, human-readable API error responses.

Internal details (stack traces, DB errors, provider payloads) are logged on the
server but never returned to clients.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.core.logging_config import get_request_id

logger = logging.getLogger("medilens.errors")


class AppError(Exception):
    """Base class for expected application errors with safe messages."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "app_error"
    message = "Something went wrong. Please try again."

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        if code:
            self.code = code


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource was not found."


class AuthError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    message = "Authentication required."


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have permission to perform this action."


class ValidationAppError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"
    message = "The submitted data is invalid."


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Please wait a moment and try again."


class UploadError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "upload_error"
    message = "The file could not be accepted."


class ProcessingError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "processing_error"
    message = "The document could not be processed."


class AIProviderError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "ai_provider_error"
    message = "The analysis service is temporarily unavailable. Please try again later."


def _error_body(code: str, message: str, detail: object | None = None) -> dict:
    body: dict = {"error": {"code": code, "message": message}}
    if detail is not None:
        body["error"]["detail"] = detail  # only safe, structured details
    body["error"]["request_id"] = get_request_id()
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_error_body(exc.code, exc.message)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Return field-level messages (no internal paths beyond the request body).
        fields = [
            {"field": ".".join(str(part) for part in err.get("loc", []) if part != "body"),
             "message": err.get("msg", "invalid value")}
            for err in exc.errors()[:20]
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body("validation_error", "The submitted data is invalid.", fields),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(
            exc.status_code, "http_error"
        )
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(status_code=exc.status_code, content=_error_body(code, message))

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database_error", extra={"event": "database_error"})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                "database_error", "A database error occurred. Please try again."
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"event": "unhandled_error"})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("internal_error", "An unexpected error occurred."),
        )
