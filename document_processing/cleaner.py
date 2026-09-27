"""Text cleaning for extracted/OCR document text."""

from __future__ import annotations

import re

from document_processing.medical_parser import normalize_text

PAGE_FOOTER_PATTERNS = (
    re.compile(r"^\s*page\s+\d+(\s*(of|/)\s*\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*(of|/)\s*\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*-\s*\d+\s*-\s*$"),
)

NOISE_LINE_PATTERNS = (
    re.compile(r"^\s*[\W_]{2,}\s*$"),  # ruler-like lines: -----, ====, ****
    re.compile(r"^\s*confidential(ity)?\s+(note|statement)?.*$", re.IGNORECASE),
)

BLANK_RUN = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Normalise whitespace/unicode and drop obvious page furniture."""
    if not text:
        return ""
    text = normalize_text(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\t", "    ")

    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if any(pattern.match(stripped) for pattern in PAGE_FOOTER_PATTERNS):
            continue
        if any(pattern.match(stripped) for pattern in NOISE_LINE_PATTERNS):
            continue
        # Collapse long inner whitespace runs but keep column separation (2 spaces).
        line = re.sub(r" {6,}", "    ", line)
        line = re.sub(r" {3}", "  ", line)
        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = BLANK_RUN.sub("\n\n", cleaned)
    return cleaned.strip()
