"""Document segmentation: group lines into titled sections (CBC, Lipid, ...)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from document_processing.medical_parser import normalize_text

SECTION_HEADER = re.compile(
    r"^[A-Z][A-Z0-9 &/\-\(\)\.]{4,70}$"  # mostly caps, short
)

KNOWN_KEYWORDS = re.compile(
    r"(complete\s+blood|haemogram|hemogram|cbc|differential|lipid|thyroid|"
    r"liver|hepatic|kidney|renal|electrolyte|vitamin|metabolic|diabetes|"
    r"glucose|urine|profile|panel|study|investigation|haematology|hematology|"
    r"biochemistry|serology)",
    re.IGNORECASE,
)


@dataclass
class Section:
    title: str | None
    page_number: int
    lines: list[str] = field(default_factory=list)


def _is_section_header(line: str) -> bool:
    stripped = line.strip().strip(":")
    if not (3 <= len(stripped) <= 70):
        return False
    if not SECTION_HEADER.match(stripped):
        return False
    if re.search(r"\d{3,}", stripped):  # avoid rows of numbers
        return False
    # Must look like a title: keywords, or at least two words.
    if KNOWN_KEYWORDS.search(stripped):
        return True
    return len(stripped.split()) >= 2 and stripped.upper() == stripped


def segment_page_text(text: str, page_number: int) -> list[Section]:
    sections: list[Section] = []
    current = Section(title=None, page_number=page_number)
    for raw_line in normalize_text(text).splitlines():
        line = raw_line.strip()
        if not line:
            current.lines.append("")
            continue
        if _is_section_header(line):
            if current.lines or current.title:
                sections.append(current)
            current = Section(title=line.strip(":"), page_number=page_number)
            continue
        current.lines.append(line)
    if current.lines or current.title:
        sections.append(current)
    return sections


def collect_section_titles(sections: list[Section]) -> list[str]:
    return [section.title for section in sections if section.title]
