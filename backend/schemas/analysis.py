"""Analysis (AI output) schemas — the structured JSON contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import SupportedLanguage
from backend.schemas.report import ReferenceRangeOut


class SourceReference(BaseModel):
    """A citation. `id` links explanations to entries in the sources list."""

    id: int
    title: str
    source_org: str | None = None
    url: str | None = None
    publication_date: str | None = None


class AbnormalResultItem(BaseModel):
    test_name: str
    value: str
    unit: str | None = None
    reference_range: ReferenceRangeOut
    status: Literal["below", "above"]
    explanation: str
    source_ids: list[int] = []
    confidence: float | None = None
    low_confidence: bool = False


class NormalResultItem(BaseModel):
    test_name: str
    value: str
    unit: str | None = None
    reference_range: ReferenceRangeOut


class EducationalExplanation(BaseModel):
    topic: str
    text: str
    source_ids: list[int] = []


class TestExplanationItem(BaseModel):
    """Point-by-point explanation for one detected test.

    Every detected value is expected to carry one of these entries: what the
    test measures, what the reported value means relative to the printed
    range, general factors, and what to discuss with a professional. The UI
    uses it for the "What does this test mean?" and "Why might this be
    outside the range?" actions.
    """

    test_name: str
    test_key: str | None = None
    status: str = "unknown"  # within | below | above | unknown
    what_it_measures: str
    meaning: str
    general_factors: str | None = None
    discuss: str | None = None
    source_ids: list[int] = []


class StructuredAnalysis(BaseModel):
    """Output contract the AI provider must satisfy (validated before storage)."""

    summary: str
    report_type: str | None = None
    explanation_level: str = "simple"  # simple | detailed (mirrored from the request)
    abnormal_results: list[AbnormalResultItem] = []
    normal_results: list[NormalResultItem] = []
    test_explanations: list[TestExplanationItem] = []
    educational_explanations: list[EducationalExplanation] = []
    questions_for_doctor: list[str] = []
    limitations: list[str] = []
    sources: list[SourceReference] = []
    retrieval_note: str | None = None


class AnalysisCounts(BaseModel):
    within: int = 0
    below: int = 0
    above: int = 0
    unknown: int = 0
    low_confidence: int = 0


class AnalysisSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    source_org: str | None = None
    url: str | None = None
    publication_date: str | None = None
    topic: str | None = None
    snippet: str | None = None
    relevance_score: float | None = None


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    report_id: str
    status: str
    provider: str
    model: str | None = None
    language: str
    explanation_level: str = "simple"
    report_type: str | None = None
    summary: str | None = None
    counts: AnalysisCounts
    structured: StructuredAnalysis | None = None
    sources: list[AnalysisSourceOut] = []
    duration_ms: int | None = None
    created_at: datetime


class AnalyzeRequest(BaseModel):
    language: SupportedLanguage = "en"
    explanation_level: Literal["simple", "detailed"] = "simple"
    force: bool = False  # re-run even if a fresh analysis exists


class ProcessingStatusOut(BaseModel):
    report_id: str
    status: str
    status_message: str | None = None
    error_code: str | None = None
    detected_test_count: int = 0
