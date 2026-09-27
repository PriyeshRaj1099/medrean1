"""Dashboard statistics and trends schemas."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from backend.schemas.report import ReportSummaryOut


class DashboardStats(BaseModel):
    reports_analyzed: int
    reports_total: int
    reports_processing: int
    values_outside_range: int
    values_low_confidence: int
    last_analysis_at: datetime | None = None
    last_analysis_report_id: str | None = None
    recent_reports: list[ReportSummaryOut] = []


class TrendPoint(BaseModel):
    report_id: str
    report_name: str
    report_date: date | None = None
    value_numeric: float
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    status: str


class TrendSeries(BaseModel):
    test_key: str
    test_name: str
    unit: str | None = None
    points: list[TrendPoint]


class TrendTestOption(BaseModel):
    test_key: str
    test_name: str
    unit: str | None = None
    report_count: int


class TrendsResponse(BaseModel):
    available_tests: list[TrendTestOption]
    series: list[TrendSeries]
    notes: list[str]
