"""Demo mode schemas: synthetic reports for exploring the product.

The guest endpoints are public: they never expose account data, filenames
or anything beyond the synthetic report, its values and its analysis.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.chat import ChatSourceOut
from backend.schemas.common import SupportedLanguage
from backend.schemas.report import ReportSummaryOut
from backend.schemas.share import SharedReportOut

DemoReportKind = Literal["cbc", "lipid", "thyroid", "metabolic"]


class DemoKindInfo(BaseModel):
    kind: DemoReportKind
    title: str
    description: str
    synthetic: bool = True


class DemoLoadResponse(BaseModel):
    report: ReportSummaryOut
    notice: str


class DemoAccountInfo(BaseModel):
    """Public demo-account credentials, displayed on the sign-in screen.

    The shared demo account is a database-backed account with a ``demo`` role:
    signing in still goes through the normal authentication pipeline.
    """

    enabled: bool = True
    email: str
    password: str
    name: str
    notice: str


class DemoGuestSampleOut(BaseModel):
    """A fully analyzed synthetic report for anonymous visitors."""

    kind: DemoReportKind
    title: str
    description: str
    synthetic: bool = True
    notice: str
    disclaimer: str
    report: SharedReportOut


class DemoGuestChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class DemoGuestChatRequest(BaseModel):
    kind: DemoReportKind
    message: str = Field(min_length=1, max_length=2000)
    language: SupportedLanguage | None = None
    history: list[DemoGuestChatTurn] = Field(default_factory=list, max_length=8)


class DemoGuestChatReply(BaseModel):
    reply: str
    sources: list[ChatSourceOut] = []
    safety_blocked: bool = False
    disclaimer: str
