"""Text extraction from documents: PyMuPDF for text PDFs, OCR for scans/images.

Scan detection: if a PDF page yields less than OCR_MIN_TEXT_CHARS characters of
embedded text, the page is rasterised and passed to the configured OCR provider
(if available). Image uploads go straight to OCR.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import pymupdf  # PyMuPDF

from backend.core.config import get_settings
from backend.core.errors import ProcessingError
from ocr.base import OCRProvider, OCRUnavailableError
from ocr.factory import get_ocr_provider

logger = logging.getLogger("medilens.document.pdf")


@dataclass
class ExtractedPage:
    page_number: int
    text: str
    method: str  # pdf_text | ocr
    ocr_confidence: float | None = None


@dataclass
class ExtractionOutcome:
    pages: list[ExtractedPage]
    page_count: int
    used_ocr: bool
    ocr_note: str | None = None


def _extract_sync(data: bytes, ocr_provider: OCRProvider | None) -> ExtractionOutcome:
    settings = get_settings()
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ProcessingError(
            "This file could not be opened as a PDF. It may be corrupted or "
            "password-protected."
        ) from exc

    pages: list[ExtractedPage] = []
    used_ocr = False
    ocr_note: str | None = None
    ocr_failed_once = False

    try:
        for index, page in enumerate(document):
            page_number = index + 1
            text = page.get_text("text") or ""
            if len(text.strip()) >= settings.ocr_min_text_chars:
                pages.append(ExtractedPage(page_number=page_number, text=text, method="pdf_text"))
                continue

            # Likely a scanned page: rasterise and OCR it when possible.
            if ocr_provider is None or not ocr_provider.is_available():
                ocr_note = (
                    "Some pages contain no embedded text and OCR is not available on this "
                    "server, so they could not be read."
                )
                pages.append(ExtractedPage(page_number=page_number, text=text, method="pdf_text"))
                continue

            try:
                pixmap = page.get_pixmap(dpi=settings.ocr_dpi)
                image_bytes = pixmap.tobytes("png")
                result = ocr_provider.extract_text(image_bytes, settings.ocr_languages)
                used_ocr = True
                pages.append(
                    ExtractedPage(
                        page_number=page_number,
                        text=result.text,
                        method="ocr",
                        ocr_confidence=result.confidence,
                    )
                )
            except OCRUnavailableError as exc:
                ocr_note = str(exc)
                pages.append(ExtractedPage(page_number=page_number, text=text, method="pdf_text"))
            except Exception:
                if not ocr_failed_once:
                    logger.exception("ocr_page_failed", extra={"event": "ocr_page_failed"})
                    ocr_failed_once = True
                pages.append(ExtractedPage(page_number=page_number, text=text, method="pdf_text"))
    finally:
        document.close()

    return ExtractionOutcome(
        pages=pages, page_count=len(pages), used_ocr=used_ocr, ocr_note=ocr_note
    )


async def extract_pdf(data: bytes, ocr_provider: OCRProvider | None = None) -> ExtractionOutcome:
    provider = ocr_provider if ocr_provider is not None else get_ocr_provider()
    return await asyncio.to_thread(_extract_sync, data, provider)


def _extract_image_sync(data: bytes, ocr_provider: OCRProvider | None) -> ExtractionOutcome:
    settings = get_settings()
    if ocr_provider is None or not ocr_provider.is_available():
        raise ProcessingError(
            "This looks like a scanned image, and OCR is not available on this server. "
            "Please upload a text-based PDF or ask the administrator to enable OCR."
        )
    try:
        result = ocr_provider.extract_text(data, settings.ocr_languages)
    except OCRUnavailableError as exc:
        raise ProcessingError(str(exc)) from exc
    except Exception as exc:
        logger.exception("ocr_image_failed", extra={"event": "ocr_image_failed"})
        raise ProcessingError(
            "The image could not be processed by OCR. Please try a clearer scan."
        ) from exc

    return ExtractionOutcome(
        pages=[
            ExtractedPage(
                page_number=1,
                text=result.text,
                method="ocr",
                ocr_confidence=result.confidence,
            )
        ],
        page_count=1,
        used_ocr=True,
    )


async def extract_image(data: bytes, ocr_provider: OCRProvider | None = None) -> ExtractionOutcome:
    provider = ocr_provider if ocr_provider is not None else get_ocr_provider()
    return await asyncio.to_thread(_extract_image_sync, data, provider)
