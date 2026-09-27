"""Text chunking for the knowledge base (paragraph-aware with overlap)."""

from __future__ import annotations

import re

PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    """Split text into ~chunk_size character chunks on paragraph boundaries.

    Falls back to sentence-level splitting for very long paragraphs.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    overlap = max(0, min(overlap, chunk_size // 2))

    normalized = re.sub(r"[ \t]+", " ", text).strip()
    if not normalized:
        return []

    paragraphs = [p.strip() for p in PARAGRAPH_SPLIT.split(normalized) if p.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= chunk_size:
            units.append(paragraph)
            continue
        # Split long paragraphs into sentences, packing them greedily.
        buffer = ""
        for sentence in SENTENCE_SPLIT.split(paragraph):
            candidate = f"{buffer} {sentence}".strip()
            if len(candidate) > chunk_size and buffer:
                units.append(buffer)
                buffer = sentence
            else:
                buffer = candidate
        if buffer:
            units.append(buffer)

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if len(candidate) > chunk_size and current:
            chunks.append(current)
            # Overlap: start the next chunk with the tail of the previous one.
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{unit}".strip() if tail else unit
        else:
            current = candidate
    if current:
        chunks.append(current)

    # Final guard: hard-split anything still oversized.
    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= chunk_size * 1.5:
            final.append(chunk)
            continue
        for start in range(0, len(chunk), chunk_size):
            final.append(chunk[start : start + chunk_size])
    return [chunk.strip() for chunk in final if chunk.strip()]
