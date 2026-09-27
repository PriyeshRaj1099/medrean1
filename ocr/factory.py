"""OCR provider factory."""

from __future__ import annotations

from functools import lru_cache

from backend.core.config import get_settings
from ocr.base import OCRProvider


@lru_cache
def get_ocr_provider() -> OCRProvider | None:
    """Return the configured OCR provider, or None when OCR is disabled."""
    settings = get_settings()
    if settings.ocr_provider == "none":
        return None
    if settings.ocr_provider == "paddle":
        from ocr.paddle_provider import PaddleOCRProvider

        return PaddleOCRProvider()
    from ocr.tesseract_provider import TesseractOCRProvider

    return TesseractOCRProvider()


def is_ocr_available() -> bool:
    provider = get_ocr_provider()
    return bool(provider and provider.is_available())
