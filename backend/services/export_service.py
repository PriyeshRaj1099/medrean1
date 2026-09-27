"""PDF summary export (fpdf2).

Notes:
* The built-in PDF core fonts cover Latin-1 only. Text in Devanagari (Hindi)
  that cannot be encoded is replaced and a visible note is added, instead of
  silently dropping content. Hinglish and English export fully.
* The exported document repeats a short "not a diagnosis" reminder in the
  footer of every page and prints the full disclaimer and emergency notice in
  the body.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from backend.schemas.analysis import AnalysisOut
from backend.schemas.common import EMERGENCY_NOTICE, MEDICAL_DISCLAIMER
from backend.schemas.report import ReportDetailOut

logger = logging.getLogger("medilens.export")

PAGE_MARGIN = 12.0

STATUS_LABELS = {
    "within": "Within range",
    "below": "Below range",
    "above": "Above range",
    "unknown": "Range not provided",
}


class _Encoder:
    """Tracks whether any non-Latin-1 characters had to be replaced."""

    def __init__(self) -> None:
        self.replaced = False

    def __call__(self, text: str | None) -> str:
        if not text:
            return ""
        try:
            text.encode("latin-1")
            return text
        except UnicodeEncodeError:
            self.replaced = True
            return text.encode("latin-1", "replace").decode("latin-1")


class _ReportPDF(FPDF):
    """FPDF subclass with a disclaimer footer on every page."""

    def __init__(self, safe: _Encoder) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._safe = safe

    def footer(self) -> None:
        self.set_y(-13)
        self.set_font("Helvetica", "I", 7.5)
        self.set_text_color(120, 120, 120)
        self.cell(
            0,
            5,
            self._safe(
                "MediLens AI - educational information about lab results, "
                f"not a diagnosis. Page {self.page_no()}"
            ),
            align="C",
        )
        self.set_text_color(0, 0, 0)


def _fit(text: str, max_chars: int) -> str:
    text = text or ""
    return text if len(text) <= max_chars else text[: max_chars - 1] + "\u2026"


def _mc(pdf: FPDF, height: float, text: str, **kwargs: object) -> None:
    """multi_cell that always returns the cursor to the left margin.

    fpdf2's multi_cell defaults to new_x=XPos.RIGHT, which parks the cursor at
    the right margin and makes the next full-width multi_cell fail with
    "Not enough horizontal space to render a single character".
    """
    pdf.multi_cell(0, height, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT, **kwargs)  # type: ignore[arg-type]


def _heading(pdf: FPDF, text: str, safe: _Encoder) -> None:
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    _mc(pdf, 6.5, safe(text))
    pdf.set_font("Helvetica", "", 10)


def _paragraph(pdf: FPDF, text: str, safe: _Encoder, style: str = "", size: int = 10) -> None:
    pdf.set_font("Helvetica", style, size)
    _mc(pdf, 5.4, safe(text))
    pdf.ln(1)


def generate_report_pdf(
    detail: ReportDetailOut,
    analysis: AnalysisOut | None,
    *,
    language: str,
    generated_at: datetime,
) -> bytes:
    safe = _Encoder()
    pdf = _ReportPDF(safe)
    pdf.set_margins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # ---- Header -----------------------------------------------------
    pdf.set_font("Helvetica", "B", 16)
    _mc(pdf, 8, safe("MediLens AI - Report Summary"))
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(90, 90, 90)
    _mc(pdf, 5, safe("Educational information about laboratory results - not a diagnosis."))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    meta_lines = [
        f"Source file: {_fit(detail.original_filename, 70)}",
        f"Report type: {detail.report_type or 'Not detected'}",
        f"Report date: {detail.report_date.isoformat() if detail.report_date else 'Not detected'}",
        f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M UTC')} | Language: {language}",
    ]
    pdf.set_font("Helvetica", "", 9)
    for line in meta_lines:
        _mc(pdf, 5, safe(line))
    if detail.is_demo:
        pdf.set_text_color(150, 60, 20)
        pdf.set_font("Helvetica", "B", 9)
        _mc(pdf, 5, safe("SYNTHETIC DEMO REPORT - created for testing, not a real patient."))
        pdf.set_text_color(0, 0, 0)
    pdf.ln(1)

    # ---- Disclaimer -------------------------------------------------
    pdf.set_fill_color(245, 245, 245)
    pdf.set_font("Helvetica", "I", 9)
    _mc(pdf, 5, safe(MEDICAL_DISCLAIMER), fill=True)
    pdf.ln(2)

    if safe.replaced:
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(150, 60, 20)
        _mc(
            pdf,
            4.5,
            safe(
                "Note: this export uses Latin-script PDF fonts, so some characters "
                "(for example Devanagari) are shown as '?'. Open the report in the app "
                "to see the full original text."
            ),
        )
        pdf.set_text_color(0, 0, 0)

    # ---- Summary ----------------------------------------------------
    _heading(pdf, "1. Summary", safe)
    if analysis is not None and analysis.summary:
        _paragraph(pdf, analysis.summary, safe)
    else:
        _paragraph(
            pdf,
            "No AI-generated summary is available for this report yet. "
            "The values below are exactly as extracted from the document.",
            safe,
        )

    # ---- Values table ----------------------------------------------
    _heading(pdf, "2. Reported values", safe)
    widths = (60.0, 22.0, 24.0, 42.0, 30.0)
    headers = ("Test", "Result", "Unit", "Reference range", "Status")
    pdf.set_font("Helvetica", "B", 9)
    for width, header in zip(widths, headers):
        pdf.cell(width, 6.5, safe(header), border=1)
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    for value in detail.values:
        result = (
            f"{value.value_numeric:g}" if value.value_numeric is not None else (value.value_text or "-")
        )
        range_text = (
            value.reference_range.text
            or (
                f"{value.reference_range.low:g} - {value.reference_range.high:g}"
                if value.reference_range.low is not None and value.reference_range.high is not None
                else ("< " + f"{value.reference_range.high:g}")
                if value.reference_range.high is not None
                else ("> " + f"{value.reference_range.low:g}")
                if value.reference_range.low is not None
                else "Not provided"
            )
        )
        cells = (
            _fit(value.test_name, 34),
            _fit(result, 10),
            _fit(value.unit or "-", 12),
            _fit(range_text, 24),
            STATUS_LABELS.get(value.status, value.status),
        )
        for width, cell in zip(widths, cells):
            pdf.cell(width, 6, safe(cell), border=1)
        pdf.ln()
    pdf.ln(2)

    # ---- Analysis sections -----------------------------------------
    if analysis is not None and analysis.structured is not None:
        structured = analysis.structured

        if structured.abnormal_results:
            _heading(pdf, "3. Values outside the report's reference range", safe)
            for item in structured.abnormal_results:
                range_text = (
                    item.reference_range.text
                    or (
                        f"{item.reference_range.low:g} - {item.reference_range.high:g}"
                        if item.reference_range.low is not None and item.reference_range.high is not None
                        else "range not provided"
                    )
                )
                label = "below" if item.status == "below" else "above"
                pdf.set_font("Helvetica", "B", 10)
                _mc(
                    pdf,
                    5.4,
                    safe(
                        f"{item.test_name}: {item.value} {item.unit or ''} "
                        f"(reported range {range_text}) - {label} the printed range"
                    ),
                )
                pdf.set_font("Helvetica", "", 9)
                _mc(pdf, 5.2, safe(item.explanation))
                pdf.ln(1)

        if structured.educational_explanations:
            _heading(pdf, "4. Educational context", safe)
            for explanation in structured.educational_explanations:
                _paragraph(pdf, f"{explanation.topic}: {explanation.text}", safe, size=9)

        if structured.questions_for_doctor:
            _heading(pdf, "5. Questions you may want to discuss with a healthcare professional", safe)
            pdf.set_font("Helvetica", "", 9)
            for question in structured.questions_for_doctor:
                _mc(pdf, 5.2, safe(f"- {question}"))
            pdf.ln(1)

        if structured.limitations:
            _heading(pdf, "6. Limitations", safe)
            pdf.set_font("Helvetica", "", 9)
            for limitation in structured.limitations:
                _mc(pdf, 5.2, safe(f"- {limitation}"))
            pdf.ln(1)

        if structured.sources:
            _heading(pdf, "7. Sources", safe)
            pdf.set_font("Helvetica", "", 8.5)
            for source in structured.sources:
                line = f"[{source.id}] {source.title}"
                if source.source_org:
                    line += f" - {source.source_org}"
                _mc(pdf, 4.8, safe(line))
                if source.url:
                    pdf.set_text_color(30, 60, 160)
                    _mc(pdf, 4.8, safe(source.url))
                    pdf.set_text_color(0, 0, 0)
            pdf.ln(1)
        elif structured.retrieval_note:
            _paragraph(pdf, structured.retrieval_note, safe, size=9)

    # ---- Footer disclaimers -----------------------------------------
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 8.5)
    _mc(pdf, 4.8, safe(EMERGENCY_NOTICE))

    output = pdf.output()
    return bytes(output)
