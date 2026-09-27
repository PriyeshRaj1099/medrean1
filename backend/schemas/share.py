"""Secure report sharing schemas.

Shared views are strictly sanitized: they never include the original
filename, private notes, chat history, account details or the source file.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from backend.schemas.analysis import AnalysisOut
from backend.schemas.report import ExtractedValueOut


class ShareLinkCreateRequest(BaseModel):
    expires_in_days: int = Field(default=7, ge=1, le=30)


class ShareLinkOut(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    last_viewed_at: datetime | None = None
    view_count: int = 0
    active: bool = True


class ShareLinkCreatedOut(ShareLinkOut):
    """Returned once, at creation time: the raw token exists only here."""

    token: str
    url: str


class SharedReportOut(BaseModel):
    """Sanitized read-only view of a report, safe for anonymous visitors."""

    report_type: str | None = None
    report_date: date | None = None
    language: str = "en"
    page_count: int = 0
    detected_test_count: int = 0
    created_at: datetime
    values: list[ExtractedValueOut] = []
    analysis: AnalysisOut | None = None


class SharedReportView(BaseModel):
    share: ShareLinkOut
    report: SharedReportOut
    notice: str
    disclaimer: str
