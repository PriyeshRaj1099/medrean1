"""OCR provider interface. Providers are pluggable via OCR_PROVIDER."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OCRResult:
    text: str
    confidence: float | None  # 0..1 when the engine reports it
    provider: str


class OCRUnavailableError(Exception):
    """Raised when the configured OCR engine is not usable on this host."""


class OCRProvider(ABC):
    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap check: are the binary/dependencies present?"""

    @abstractmethod
    def extract_text(self, image_bytes: bytes, languages: str) -> OCRResult:
        """Run OCR on a single image (PNG/JPEG bytes)."""
