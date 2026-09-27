"""PaddleOCR provider (optional, higher-accuracy local engine).

Install with:  pip install paddleocr paddlepaddle
Then set OCR_PROVIDER=paddle. PaddleOCR is heavy; Tesseract remains the default.
"""

from __future__ import annotations

import io
import logging

from ocr.base import OCRResult, OCRProvider, OCRUnavailableError

logger = logging.getLogger("medilens.ocr.paddle")


class PaddleOCRProvider(OCRProvider):
    name = "paddle"
    _engine = None

    def is_available(self) -> bool:
        try:
            import paddleocr  # noqa: F401

            return True
        except ImportError:
            return False

    def _get_engine(self):
        if PaddleOCRProvider._engine is None:
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]

            PaddleOCRProvider._engine = PaddleOCR(use_angle_cls=True, show_log=False)
        return PaddleOCRProvider._engine

    def extract_text(self, image_bytes: bytes, languages: str) -> OCRResult:
        if not self.is_available():
            raise OCRUnavailableError(
                "PaddleOCR is not installed. Install paddleocr or switch OCR_PROVIDER "
                "back to 'tesseract'."
            )
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            array = np.array(image.convert("RGB"))

        engine = self._get_engine()
        result = engine.ocr(array, cls=True) or []
        lines: list[str] = []
        confidences: list[float] = []
        for page in result:
            for entry in page or []:
                try:
                    _box, (text, conf) = entry
                    if text:
                        lines.append(text)
                    confidences.append(float(conf))
                except (TypeError, ValueError):
                    continue
        avg = sum(confidences) / len(confidences) if confidences else None
        return OCRResult(text="\n".join(lines), confidence=avg, provider=self.name)
