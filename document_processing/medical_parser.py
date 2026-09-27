"""Deterministic parser that turns laboratory report text into structured values.

Design rules:
* The reference range printed on the report always takes precedence; when a
  report provides no range, the value is marked ``unknown`` — never invented.
* Every extracted value carries a confidence score, source page and the raw
  line it came from, so users can verify or correct it.
* OCR text is penalised proportionally to the OCR engine's confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from document_processing.lab_tests import ALIAS_INDEX, LabTest

# ----------------------------------------------------------------------
# Normalisation helpers
# ----------------------------------------------------------------------
_DASHES = {"\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-"}
_MICRO = {"\u00b5": "u", "\u03bc": "u", "\u03a0": "u"}
_ODD_SPACES = {"\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u2009": " ", "\u200b": ""}


def normalize_text(text: str) -> str:
    for source, target in {**_DASHES, **_MICRO, **_ODD_SPACES}.items():
        text = text.replace(source, target)
    return text


NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?"
QUALIFIER = r"<|<=|>|>=|\u2264|\u2265"
_NUM_WORD = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
RANGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 13.0 - 17.0 / 13-17 / 4 to 11 / 13.0–17.0 (normalised to '-')
    re.compile(rf"(?P<low>{_NUM_WORD})\s*(?:-|to)\s*(?P<high>{_NUM_WORD})", re.IGNORECASE),
    # < 200 / <= 200 / up to 200 / upto 200 / max 200 / not more than 200
    re.compile(
        rf"(?:<|<=|\u2264|up\s*to|upto|not\s+more\s+than|max(?:imum)?|less\s+than)\s*(?P<high>{_NUM_WORD})",
        re.IGNORECASE,
    ),
    # > 40 / >= 40 / more than 40 / min 40 / at least 40
    re.compile(
        rf"(?:>|>=|\u2265|more\s+than|greater\s+than|min(?:imum)?|at\s+least)\s*(?P<low>{_NUM_WORD})",
        re.IGNORECASE,
    ),
)

NO_RANGE_MARKERS = ("not provided", "not available", "n/a", "na", "nil", "-", "not established")

FLAG_PATTERN = re.compile(r"(?:^|\s|\()(?P<flag>HH|LL|H|L|HIGH|LOW|ABNORMAL)(?:\s|\)|$)", re.IGNORECASE)
ARROW_FLAGS = {"\u2191": "H", "\u2193": "L"}

TEXT_VALUE_PATTERN = re.compile(
    r"\b(negative|non[\s-]?reactive|positive|reactive|trace|normal|abnormal|nil|absent|present|"
    r"not\s+detected|detected|clear|yellow|pale\s+yellow)\b",
    re.IGNORECASE,
)

# A token counts as a unit if it contains letters/percent/slashes and is short,
# e.g. g/dL, mg/dl, 10^3/uL, mmol/L, %, fL, IU/mL, mIU/L, x10^9/l, lakh/cumm
UNIT_TOKEN_PATTERN = re.compile(
    r"^(?:[%]|[a-zA-Z][a-zA-Z0-9\.^/*%]{0,14})$"
)
UNIT_KEYWORDS = re.compile(
    r"(g/dl|g/l|mg/dl|mg/l|mg%|mmol/l|umol/l|meq/l|miu/l|uiu/ml|ng/ml|ng/dl|pg/ml|pmol/l|"
    r"nmol/l|iu/l|u/l|ui/l|fl|pg|femtoliters|10\^?\d|xd\^?\d|x10\^?\d|cumm|cmm|/ul|/l|"
    r"mm/hr|mm/hour|cells/ul|lakh|cells|copies/ml|%|seconds|sec|ratio|index)",
    re.IGNORECASE,
)

DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})"), "dmy"),
    (
        re.compile(
            r"(\d{1,2})[\s-]*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s,-]*(\d{2,4})",
            re.IGNORECASE,
        ),
        "dMy",
    ),
    (
        re.compile(
            r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s-]*(\d{1,2})[\s,]*(\d{2,4})",
            re.IGNORECASE,
        ),
        "Mdy",
    ),
)

DATE_LABEL_PATTERN = re.compile(
    r"(report\s*date|date\s*of\s*report|collection\s*date|sample\s*date|collected\s*on|"
    r"date\s*collected|reported\s*on|test\s*date|specimen\s*date)",
    re.IGNORECASE,
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Report-type detection by which tests co-occur.
REPORT_TYPE_RULES: tuple[tuple[str, frozenset[str]], ...] = (
    ("Complete Blood Count (CBC)", frozenset({"hemoglobin", "wbc", "platelets", "rbc", "hematocrit"})),
    ("Lipid Profile", frozenset({"cholesterol_total", "triglycerides", "hdl", "ldl"})),
    ("Thyroid Profile", frozenset({"tsh", "t3_total", "t4_total", "ft3", "ft4"})),
    ("Liver Function Panel", frozenset({"alt", "ast", "bilirubin_total", "alp", "albumin"})),
    ("Kidney Function Panel", frozenset({"creatinine", "urea", "uric_acid"})),
    ("Glucose / Diabetes Panel", frozenset({"glucose_fasting", "glucose_pp", "glucose_random", "hba1c"})),
    ("Electrolyte Panel", frozenset({"sodium", "potassium", "chloride", "calcium"})),
    ("Vitamin Panel", frozenset({"vitamin_d", "vitamin_b12", "vitamin_b9"})),
)

SECTION_TYPE_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"complete\s+blood\s+count|cbc|haemogram|hemogram", re.I), "Complete Blood Count (CBC)"),
    (re.compile(r"lipid\s+profile|lipid\s+panel", re.I), "Lipid Profile"),
    (re.compile(r"thyroid", re.I), "Thyroid Profile"),
    (re.compile(r"liver\s+function|hepatic\s+panel", re.I), "Liver Function Panel"),
    (re.compile(r"kidney\s+function|renal\s+panel|renal\s+function", re.I), "Kidney Function Panel"),
    (re.compile(r"glucose|diabetes|sugar", re.I), "Glucose / Diabetes Panel"),
    (re.compile(r"electrolyte", re.I), "Electrolyte Panel"),
    (re.compile(r"vitamin", re.I), "Vitamin Panel"),
)


@dataclass
class ParsedValue:
    test_name: str
    test_key: str | None
    value_numeric: float | None
    value_text: str | None
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    ref_text: str | None
    status: str  # within | below | above | unknown
    report_flag: str | None
    source_page: int
    confidence: float
    raw_line: str
    sort_order: int = 0


@dataclass
class ParseResult:
    values: list[ParsedValue] = field(default_factory=list)
    report_date: date | None = None
    report_type: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class PageInput:
    page_number: int
    text: str
    method: str = "pdf_text"  # pdf_text | ocr
    ocr_confidence: float | None = None


# ----------------------------------------------------------------------
# Low-level helpers
# ----------------------------------------------------------------------
def _to_float(token: str) -> float | None:
    token = token.strip().rstrip(".")
    if not token:
        return None
    # Thousands separators: 1,000 / 1,000,000
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", token):
        token = token.replace(",", "")
    # Decimal comma: 10,2
    elif re.fullmatch(r"\d{1,3},\d{1,2}", token):
        token = token.replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


def compute_status(
    value: float | None, low: float | None, high: float | None
) -> str:
    if value is None or (low is None and high is None):
        return "unknown"
    if low is not None and value < low:
        return "below"
    if high is not None and value > high:
        return "above"
    return "within"


def format_range(low: float | None, high: float | None, original: str | None) -> str | None:
    if original:
        return original
    if low is not None and high is not None:
        return f"{low:g} - {high:g}"
    if high is not None:
        return f"< {high:g}"
    if low is not None:
        return f"> {low:g}"
    return None


def _strip_bullets(line: str) -> str:
    return re.sub(r"^[\u2022\u00b7\*\-\u2013]\s+|^\d{1,3}[\.\)]\s+", "", line).strip()


def _find_range(text: str, start_at: int = 0) -> tuple[float | None, float | None, str, tuple[int, int]] | None:
    """Return (low, high, original_text, span) for the first range found."""
    best: tuple[int, re.Match[str], str] | None = None
    for pattern in RANGE_PATTERNS:
        for match in pattern.finditer(text, start_at):
            if best is None or match.start() < best[0]:
                best = (match.start(), match, pattern.pattern)
    if best is None:
        return None
    _, match, _ = best
    groups = match.groupdict()
    low = _to_float(groups.get("low") or "") if groups.get("low") else None
    high = _to_float(groups.get("high") or "") if groups.get("high") else None
    original = match.group(0).strip()
    return low, high, original, match.span()


def _looks_like_reference_only_line(line: str) -> tuple[float | None, float | None, str] | None:
    """A line containing only a range (wrapped table layouts)."""
    stripped = _strip_bullets(line)
    if not stripped or len(stripped) > 40:
        return None
    lowered = stripped.lower()
    if lowered in NO_RANGE_MARKERS or "not provided" in lowered:
        return None
    # Must contain a digit but no test-name-like word running into text.
    if not re.search(r"\d", stripped):
        return None
    match = re.fullmatch(
        rf"\(?\s*({_NUM_WORD})\s*(?:-|to)\s*({_NUM_WORD})\s*\)?", stripped, re.IGNORECASE
    )
    if match:
        low, high = _to_float(match.group(1)), _to_float(match.group(2))
        if low is not None and high is not None:
            return (min(low, high), max(low, high), stripped)
    match = re.fullmatch(
        rf"(?:<|<=|\u2264|up\s*to|upto)\s*({_NUM_WORD})", stripped, re.IGNORECASE
    )
    if match:
        return (None, _to_float(match.group(1)), stripped)
    match = re.fullmatch(
        rf"(?:>|>=|\u2265|more\s+than)\s*({_NUM_WORD})", stripped, re.IGNORECASE
    )
    if match:
        return (_to_float(match.group(1)), None, stripped)
    return None


def _match_test_name(line: str) -> tuple[LabTest | None, str] | None:
    """Match a dictionary test at the start of a line; returns (test, remainder)."""
    lowered = line.lower().lstrip()
    for alias, test in ALIAS_INDEX:
        if not lowered.startswith(alias):
            continue
        remainder = line.lstrip()[len(alias):]
        if remainder and remainder[0] not in " \t:(-,./*":
            continue
        return test, remainder.lstrip(" \t:-")
    return None


def _extract_unit_and_flag(core: str) -> tuple[str | None, str | None]:
    """Extract a unit token and an H/L flag from the tail of the line."""
    flag: str | None = None

    for arrow, mapped in ARROW_FLAGS.items():
        if arrow in core:
            flag = mapped
    flag_match = FLAG_PATTERN.search(core)
    if flag_match:
        raw = flag_match.group("flag").upper()
        flag = {"HH": "H", "HIGH": "H", "ABNORMAL": "H", "LL": "L", "LOW": "L"}.get(raw, raw)

    # Remove flag occurrences so they don't leak into units.
    core = FLAG_PATTERN.sub(" ", core)
    for arrow in ARROW_FLAGS:
        core = core.replace(arrow, " ")

    unit: str | None = None
    tokens = [tok for tok in re.split(r"[\s,]+", core.strip()) if tok]
    unit_tokens: list[str] = []
    for token in tokens:
        cleaned = token.strip("()[]:;,.")
        if not cleaned:
            continue
        if unit_tokens and not UNIT_TOKEN_PATTERN.match(cleaned):
            break
        if UNIT_KEYWORDS.search(cleaned) or (
            not unit_tokens and UNIT_TOKEN_PATTERN.match(cleaned) and not cleaned.isdigit()
        ):
            if cleaned.lower() in ("h", "l"):
                continue
            unit_tokens.append(cleaned)
        elif unit_tokens:
            break
    if unit_tokens:
        unit = "/".join(unit_tokens) if len(unit_tokens) > 1 and all(
            "/" not in t for t in unit_tokens
        ) else " ".join(unit_tokens)
        unit = unit.strip(" ,;")
    return unit, flag


def _measurement_from_remainder(
    remainder: str, next_lines: list[str]
) -> tuple[float | None, str | None, float | None, float | None, str | None, str | None] | None:
    """Parse value/unit/range from the text after the test name.

    Returns (value_numeric, value_text, low, high, range_text, unit) or None.
    """
    text = remainder.strip()
    if not text:
        return None

    # Locate the value first: number with optional qualifier, or a text value.
    value_numeric: float | None = None
    value_text: str | None = None
    value_span: tuple[int, int] | None = None

    num_match = re.search(rf"(?P<q>{QUALIFIER})?\s*(?P<num>{NUMBER})", text)
    text_match = TEXT_VALUE_PATTERN.search(text)
    if num_match and (text_match is None or num_match.start() <= text_match.start()):
        value_numeric = _to_float(num_match.group("num"))
        value_span = num_match.span()
    elif text_match:
        value_text = text_match.group(0).strip()

    # Range: search only after the value to avoid re-matching it.
    range_result = _find_range(text, value_span[1] if value_span else 0)
    if range_result is None:
        # Wrapped layouts: "<name> <value>" followed by "<unit> <range>" or "<range>".
        for candidate in next_lines[:2]:
            stripped = _strip_bullets(candidate)
            if not stripped or len(stripped) > 60:
                break
            if _match_test_name(stripped):
                break
            ref_only = _looks_like_reference_only_line(stripped)
            if ref_only:
                low, high, original = ref_only
                unit, flag = _extract_unit_and_flag(
                    text[value_span[1]:] if value_span else text
                )
                return value_numeric, value_text, low, high, original, unit
            # Unit + range on the following line (e.g. "g/dL 13.0 - 17.0")
            inline = _find_range(stripped)
            if inline:
                low, high, original, _ = inline
                unit, _ = _extract_unit_and_flag(stripped[: inline[3][0]])
                if unit is None and value_span:
                    unit, _ = _extract_unit_and_flag(text[value_span[1]:])
                return value_numeric, value_text, low, high, original, unit

    low = high = None
    range_text: str | None = None
    if range_result:
        low, high, range_text, _span = range_result
        if low is not None and high is not None and low > high:
            low, high = high, low  # tolerate reversed printing

    # Unit: text after the value, excluding the range part.
    unit_source = text[value_span[1]:] if value_span else ""
    if range_text:
        idx = unit_source.find(range_text)
        if idx >= 0:
            unit_source = unit_source[:idx]
    unit, _ = _extract_unit_and_flag(unit_source)

    if value_numeric is None and value_text is None:
        return None
    return value_numeric, value_text, low, high, range_text, unit


def _detect_flag(line: str) -> str | None:
    for arrow, mapped in ARROW_FLAGS.items():
        if arrow in line:
            return mapped
    match = FLAG_PATTERN.search(line)
    if match:
        raw = match.group("flag").upper()
        return {"HH": "H", "HIGH": "H", "ABNORMAL": "H", "LL": "L", "LOW": "L"}.get(raw, raw)
    return None


def _confidence(
    *,
    dict_match: bool,
    has_unit: bool,
    has_range: bool,
    range_from_next_line: bool,
    method: str,
    ocr_confidence: float | None,
    numeric: bool,
) -> float:
    score = 0.92 if dict_match else 0.62
    if dict_match and numeric:
        score += 0.03
    if not has_unit:
        score -= 0.12
    if not has_range:
        score -= 0.18
    if range_from_next_line:
        score -= 0.06
    if method == "ocr":
        ocr_penalty = 0.18 * (1.0 - (ocr_confidence if ocr_confidence is not None else 0.75))
        score -= ocr_penalty
    return max(0.30, min(0.98, round(score, 3)))


# ----------------------------------------------------------------------
# Report metadata detection
# ----------------------------------------------------------------------
def detect_report_date(text: str) -> date | None:
    lines = text.splitlines()
    candidates: list[str] = []
    for line in lines:
        if DATE_LABEL_PATTERN.search(line):
            candidates.append(line)
    if not candidates:
        return None
    for line in candidates:
        for pattern, kind in DATE_PATTERNS:
            for match in pattern.finditer(line):
                parsed = _parse_date_match(match, kind)
                if parsed:
                    return parsed
    return None


def _parse_date_match(match: re.Match[str], kind: str) -> date | None:
    try:
        if kind == "ymd":
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        elif kind == "dmy":
            day, month, raw_year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            year = _expand_year(raw_year)
        elif kind == "dMy":
            day = int(match.group(1))
            month = MONTHS[match.group(2).lower()[:3]]
            year = _expand_year(int(match.group(3)))
        else:  # Mdy
            month = MONTHS[match.group(1).lower()[:3]]
            day = int(match.group(2))
            year = _expand_year(int(match.group(3)))
        if month == 0 or day == 0:
            return None
        # Tolerate DD/MM vs MM/DD ambiguity: swap if clearly out of range.
        if month > 12 and day <= 12:
            day, month = month, day
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        value = date(year, month, day)
        if 2000 <= value.year <= date.today().year + 1:
            return value
    except (ValueError, KeyError):
        return None
    return None


def _expand_year(raw: int) -> int:
    if raw < 100:
        return 2000 + raw if raw <= 60 else 1900 + raw
    return raw


def detect_report_type(values: list[ParsedValue], section_titles: list[str]) -> str | None:
    keys = {value.test_key for value in values if value.test_key}
    for title in section_titles:
        for pattern, type_name in SECTION_TYPE_HINTS:
            if pattern.search(title):
                return type_name
    best: tuple[int, str] | None = None
    for type_name, required in REPORT_TYPE_RULES:
        overlap = len(keys & required)
        if overlap >= 2 and (best is None or overlap > best[0]):
            best = (overlap, type_name)
    if best:
        return best[1]
    if len(keys) >= 6:
        return "Comprehensive Laboratory Panel"
    return None


# ----------------------------------------------------------------------
# Main entry points
# ----------------------------------------------------------------------
def parse_line(
    line: str,
    next_lines: list[str],
    *,
    page_number: int,
    method: str,
    ocr_confidence: float | None,
    defer_status: bool = False,
) -> ParsedValue | None:
    stripped = _strip_bullets(normalize_text(line))
    if len(stripped) < 3:
        return None

    match = _match_test_name(stripped)
    test: LabTest | None = None
    remainder = ""
    if match:
        test, remainder = match
    else:
        # Generic name:value layout, e.g. "Beta HCG    1250  mIU/mL  < 5"
        generic = re.match(
            r"^(?P<name>[A-Za-z][A-Za-z0-9\-/()\s\.]{1,45}?)\s{2,}(?P<rest>[\d<>=].*)$",
            stripped,
        )
        if not generic:
            return None
        remainder = generic.group("rest")

    parsed = _measurement_from_remainder(remainder, next_lines)
    if parsed is None:
        return None
    value_numeric, value_text, low, high, range_text, unit = parsed

    if value_numeric is None and value_text is None:
        return None

    status = "unknown" if defer_status else compute_status(value_numeric, low, high)
    report_flag = _detect_flag(stripped)

    # Ranges explicitly declared "not provided" are kept as text, no numbers.
    if not defer_status:
        lowered_range = (range_text or "").lower()
        if any(marker == lowered_range for marker in NO_RANGE_MARKERS):
            low = high = None
            status = "unknown"

    confidence = _confidence(
        dict_match=test is not None,
        has_unit=unit is not None,
        has_range=range_text is not None,
        range_from_next_line=False,
        method=method,
        ocr_confidence=ocr_confidence,
        numeric=value_numeric is not None,
    )

    return ParsedValue(
        test_name=test.name if test else stripped.split("  ")[0].strip(),
        test_key=test.key if test else None,
        value_numeric=value_numeric,
        value_text=value_text,
        unit=unit,
        ref_low=low,
        ref_high=high,
        ref_text=range_text,
        status=status,
        report_flag=report_flag,
        source_page=page_number,
        confidence=confidence,
        raw_line=stripped,
    )


def classify_values(values: list[ParsedValue]) -> None:
    """Check parsed values against their printed reference ranges, in place.

    ``parse_line`` normally does this while parsing. The processing pipeline
    defers it (``defer_status=True``) so it can announce "Finding laboratory
    values..." and "Checking the reference ranges..." as two honest phases.
    """
    for value in values:
        lowered_range = (value.ref_text or "").lower()
        if any(marker == lowered_range for marker in NO_RANGE_MARKERS):
            value.ref_low = None
            value.ref_high = None
            value.status = "unknown"
            continue
        value.status = compute_status(value.value_numeric, value.ref_low, value.ref_high)


def parse_pages(
    pages: list[PageInput],
    *,
    section_titles: list[str] | None = None,
    defer_status: bool = False,
) -> ParseResult:
    """Parse page text into structured values (deduplicated, ordered).

    With ``defer_status=True`` the reference-range comparison is skipped and
    every value status stays "unknown" until ``classify_values`` is called.
    """
    result = ParseResult()
    seen: set[tuple[str, str, str | None]] = set()
    combined_text = "\n".join(page.text for page in pages)
    result.report_date = detect_report_date(combined_text)

    sort_order = 0
    for page in pages:
        text = normalize_text(page.text)
        lines = text.splitlines()
        for index, raw_line in enumerate(lines):
            next_lines = lines[index + 1 : index + 3]
            parsed = parse_line(
                raw_line,
                next_lines,
                page_number=page.page_number,
                method=page.method,
                ocr_confidence=page.ocr_confidence,
                defer_status=defer_status,
            )
            if parsed is None:
                continue
            dedupe_key = (
                parsed.test_key or parsed.test_name.lower(),
                str(parsed.value_numeric if parsed.value_numeric is not None else parsed.value_text),
                parsed.unit,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            parsed.sort_order = sort_order
            sort_order += 1
            result.values.append(parsed)

    if not result.values:
        result.warnings.append(
            "No laboratory values could be detected in this document. "
            "It may not be a laboratory report, or the scan quality may be too low."
        )

    result.report_type = detect_report_type(result.values, section_titles or [])
    return result
