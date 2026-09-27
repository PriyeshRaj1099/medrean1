"""Report, extracted value and comparison schemas."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReferenceRangeOut(BaseModel):
    low: float | None = None
    high: float | None = None
    text: str | None = None


class ExtractedValueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    test_name: str
    test_key: str | None = None
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    reference_range: ReferenceRangeOut
    status: str
    report_flag: str | None = None
    source_page: int
    confidence: float
    raw_line: str | None = None
    is_user_edited: bool
    sort_order: int


class ReportSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    content_type: str
    file_size: int
    status: str
    status_message: str | None = None
    error_code: str | None = None
    report_type: str | None = None
    report_date: date | None = None
    page_count: int
    detected_test_count: int
    language: str
    is_demo: bool
    analysis_stale: bool
    is_pinned: bool = False
    has_note: bool = False
    created_at: datetime
    updated_at: datetime


class ReportPageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    extraction_method: str
    ocr_confidence: float | None = None
    text: str


class ReportDetailOut(ReportSummaryOut):
    values: list[ExtractedValueOut] = []
    pages: list[ReportPageOut] = []
    note: str | None = None
    note_include_in_analysis: bool = False


class ReportListResponse(BaseModel):
    items: list[ReportSummaryOut]
    total: int
    limit: int
    offset: int


class ValueUpdateItem(BaseModel):
    id: str
    test_name: str | None = Field(default=None, min_length=1, max_length=150)
    value_numeric: float | None = None
    value_text: str | None = Field(default=None, max_length=120)
    unit: str | None = Field(default=None, max_length=60)
    ref_low: float | None = None
    ref_high: float | None = None
    ref_text: str | None = Field(default=None, max_length=120)


class ResultsUpdateRequest(BaseModel):
    values: list[ValueUpdateItem] = Field(min_length=1, max_length=200)


class PinUpdateRequest(BaseModel):
    is_pinned: bool


class NoteUpdateRequest(BaseModel):
    """Private note attached to a report.

    The note is only sent to the AI provider when ``include_in_analysis`` is
    explicitly set to true by the user.
    """

    note: str | None = Field(default=None, max_length=2000)
    include_in_analysis: bool = False

    @field_validator("note")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class CompareRequest(BaseModel):
    report_ids: list[str] = Field(min_length=2, max_length=6)


class CompareEntry(BaseModel):
    report_id: str
    report_name: str
    report_date: date | None = None
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    reference_range: ReferenceRangeOut
    status: str


class CompareRow(BaseModel):
    test_key: str
    test_name: str
    unit: str | None = None
    entries: list[CompareEntry]
    change: str  # descriptive, never "improved"/"worsened"
    delta: float | None = None


class CompareReportMeta(BaseModel):
    id: str
    original_filename: str
    report_date: date | None = None
    created_at: datetime


class CompareResponse(BaseModel):
    reports: list[CompareReportMeta]
    rows: list[CompareRow]
    notes: list[str]
