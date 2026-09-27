"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from backend.api.deps import DbSession
from backend.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(db: DbSession) -> dict:
    """Liveness + dependency check (database, provider configuration)."""
    settings = get_settings()
    database_ok = True
    database_error: str | None = None
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on environment
        database_ok = False
        database_error = type(exc).__name__

    ocr_available: bool | None = None
    if settings.ocr_provider == "tesseract":
        from ocr.factory import is_ocr_available

        ocr_available = is_ocr_available()

    status = "ok" if database_ok else "degraded"
    return {
        "status": status,
        "version": "0.1.0",
        "environment": settings.environment,
        "checks": {
            "database": {"ok": database_ok, "error": database_error},
            "ai_provider": settings.ai_provider,
            "embedding_provider": settings.embedding_provider,
            "ocr_provider": settings.ocr_provider,
            "ocr_available": ocr_available,
            "storage_backend": settings.storage_backend,
        },
    }
