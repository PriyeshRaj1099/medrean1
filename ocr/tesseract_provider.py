"""Tesseract OCR provider (local fallback engine).

Requires the Tesseract binary to be installed on the host:
  Windows: https://github.com/UB-Mannheim/tesseract/wiki
  macOS:   brew install tesseract
  Linux:   apt install tesseract-ocr
"""

from __future__ import annotations

import io
import logging
import shutil
from pathlib import Path

from ocr.base import OCRResult, OCRProvider, OCRUnavailableError

logger = logging.getLogger("medilens.ocr.tesseract")


class TesseractOCRProvider(OCRProvider):
    name = "tesseract"

    def is_available(self) -> bool:
        try:
            import pytesseract
        except ImportError:
            return False
        command = pytesseract.pytesseract.tesseract_cmd
        if command:
            return bool(shutil.which(command) or Path(command).exists())
        return shutil.which("tesseract") is not None

    def extract_text(self, image_bytes: bytes, languages: str) -> OCRResult:
        if not self.is_available():
            raise OCRUnavailableError(
                "The Tesseract OCR engine is not installed on this server. "
                "Install it to enable scanning of image-based reports."
            )
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            data = pytesseract.image_to_data(
                image,
                lang=languages or "eng",
                output_type=pytesseract.Output.DICT,
            )

        words: list[str] = []
        confidences: list[float] = []
        for i, word in enumerate(data.get("text", [])):
            cleaned = (word or "").strip()
            if cleaned:
                words.append(cleaned)
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError, KeyError, IndexError):
                conf = -1.0
            if conf >= 0:
                confidences.append(conf / 100.0)

        text = reconstruct_lines(data)
        avg_conf = sum(confidences) / len(confidences) if confidences else None
        return OCRResult(text=text, confidence=avg_conf, provider=self.name)


def reconstruct_lines(data: dict) -> str:
    """Rebuild readable lines from Tesseract's word-level output."""
    lines: dict[tuple[int, int, int], list[str]] = {}
    for i, word in enumerate(data.get("text", [])):
        text = (word or "").strip()
        if not text:
            continue
        key = (
            data["block_num"][i],
            data["par_num"][i],
            data["line_num"][i],
        )
        lines.setdefault(key, []).append(text)
    return "\n".join(" ".join(parts) for _, parts in sorted(lines.items()))
