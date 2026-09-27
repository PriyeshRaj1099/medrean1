"""Demo mode service: create fully-parsed synthetic reports.

Two flavours:
* `load_demo_report` — authenticated users get a private copy of a synthetic
  report that flows through the normal analysis pipeline.
* `get_guest_sample` / `guest_chat` — public endpoints for the "Try Demo"
  flow. They serve a single cached copy per report kind owned by a system
  demo account, with every response fully sanitized.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai.base import ChatRequest as AIChatRequest
from ai.base import ChatTurn
from ai.registry import get_ai_provider, get_embedding_provider
from backend.core.errors import ValidationAppError
from backend.core.security import hash_password
from backend.schemas.chat import ChatSourceOut
from backend.schemas.common import MEDICAL_DISCLAIMER, SUPPORTED_LANGUAGES
from backend.schemas.demo import (
    DemoGuestChatReply,
    DemoGuestChatRequest,
    DemoGuestSampleOut,
    DemoKindInfo,
    DemoLoadResponse,
)
from backend.schemas.share import SharedReportOut
from backend.services import analysis_service, audit_service, chat_service, report_service, share_service
from backend.services.demo_reports import DEMO_NOTICE, DEMO_REPORTS
from database.base import new_id, utcnow
from database.models import ExtractedValue, Report, ReportPage, User
from document_processing.cleaner import clean_text
from document_processing.medical_parser import PageInput, parse_pages
from document_processing.segmenter import collect_section_titles, segment_page_text
from rag.retriever import Retriever

logger = logging.getLogger("medilens.demo")

DEMO_USER_EMAIL = "guest-demo@medilens.local"

# The guest chat keeps at most this many prior turns as context.
GUEST_CHAT_HISTORY_LIMIT = 8


def list_demo_kinds() -> list[DemoKindInfo]:
    return [
        DemoKindInfo(kind=spec.kind, title=spec.title, description=spec.description)
        for spec in DEMO_REPORTS.values()
    ]


def _require_spec(kind: str):
    spec = DEMO_REPORTS.get(kind)
    if spec is None:
        raise ValidationAppError(
            "Unknown demo report type. Available types: "
            + ", ".join(sorted(DEMO_REPORTS)),
            code="unknown_demo_kind",
        )
    return spec


async def build_demo_report(db: AsyncSession, user: User, kind: str) -> Report:
    """Build a demo report by running the real parser over synthetic text."""
    spec = _require_spec(kind)

    cleaned = clean_text(spec.text)
    sections = segment_page_text(cleaned, 1)
    page_inputs = [PageInput(page_number=1, text=cleaned, method="pdf_text", ocr_confidence=None)]
    parse_result = parse_pages(page_inputs, section_titles=collect_section_titles(sections))

    now = utcnow()
    report = Report(
        id=new_id(),
        user_id=user.id,
        original_filename=spec.filename,
        storage_key=f"demo/{kind}",
        content_type="application/pdf",
        file_size=len(spec.text.encode("utf-8")),
        file_sha256=hashlib.sha256(spec.text.encode("utf-8")).hexdigest(),
        status="extracted",
        report_type=parse_result.report_type,
        report_date=parse_result.report_date,
        page_count=1,
        detected_test_count=len(parse_result.values),
        language="en",
        is_demo=True,
        processing_started_at=now,
        processing_finished_at=now,
        analysis_stale=False,
    )
    db.add(report)
    db.add(
        ReportPage(
            report_id=report.id,
            page_number=1,
            text=cleaned,
            extraction_method="pdf_text",
            ocr_confidence=None,
            created_at=now,
        )
    )
    for parsed in parse_result.values:
        db.add(
            ExtractedValue(
                report_id=report.id,
                sort_order=parsed.sort_order,
                test_name=parsed.test_name,
                test_key=parsed.test_key,
                value_numeric=parsed.value_numeric,
                value_text=parsed.value_text,
                unit=parsed.unit,
                ref_low=parsed.ref_low,
                ref_high=parsed.ref_high,
                ref_text=parsed.ref_text,
                status=parsed.status,
                report_flag=parsed.report_flag,
                source_page=parsed.source_page,
                confidence=parsed.confidence,
                raw_line=parsed.raw_line,
                is_user_edited=False,
            )
        )
    await db.commit()
    await db.refresh(report)
    return report


async def load_demo_report(db: AsyncSession, user: User, kind: str) -> DemoLoadResponse:
    """Give an authenticated user their own copy of a synthetic report."""
    _require_spec(kind)
    report = await build_demo_report(db, user, kind)

    await audit_service.record(
        db,
        "demo.loaded",
        user_id=user.id,
        entity_type="report",
        entity_id=report.id,
        detail={"kind": kind, "values": report.detected_test_count},
    )

    # Run the same analysis pipeline as a real report (retrieval + provider).
    await report_service.enqueue_analysis(report.id, language="en")
    return DemoLoadResponse(report=report_service.report_to_summary(report), notice=DEMO_NOTICE)


# ----------------------------------------------------------------------
# Guest demo ("Try Demo" without an account)
# ----------------------------------------------------------------------
async def _ensure_demo_user(db: AsyncSession) -> User:
    """The hidden system account that owns the cached guest demo reports."""
    user = (
        await db.execute(select(User).where(User.email == DEMO_USER_EMAIL))
    ).scalar_one_or_none()
    if user is not None:
        return user
    user = User(
        name="Guest Demo",
        email=DEMO_USER_EMAIL,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role="user",
        locale="en",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _guest_report(db: AsyncSession, kind: str) -> Report:
    """Cached synthetic report for one kind (created and analyzed on demand)."""
    _require_spec(kind)
    user = await _ensure_demo_user(db)
    report = (
        await db.execute(
            select(Report)
            .options(selectinload(Report.values))
            .where(
                Report.user_id == user.id,
                Report.is_demo.is_(True),
                Report.storage_key == f"demo/{kind}",
            )
            .order_by(Report.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if report is None:
        report = await build_demo_report(db, user, kind)
        await db.refresh(report, attribute_names=["values"])
        await audit_service.record(
            db,
            "demo.guest_sample_created",
            user_id=None,
            entity_type="report",
            entity_id=report.id,
            detail={"kind": kind},
        )
    return report


async def get_guest_sample(
    db: AsyncSession, kind: str, language: str = "en"
) -> DemoGuestSampleOut:
    """Fully analyzed synthetic sample for anonymous visitors."""
    spec = _require_spec(kind)
    lang = language if language in SUPPORTED_LANGUAGES else "en"
    report = await _guest_report(db, kind)
    analysis = await analysis_service.run_analysis(
        db, report, language=lang, explanation_level="simple"
    )
    return DemoGuestSampleOut(
        kind=spec.kind,
        title=spec.title,
        description=spec.description,
        notice=DEMO_NOTICE,
        disclaimer=MEDICAL_DISCLAIMER,
        report=SharedReportOut(
            report_type=report.report_type,
            report_date=report.report_date,
            language=lang,
            page_count=report.page_count,
            detected_test_count=report.detected_test_count,
            created_at=report.created_at,
            values=share_service.sanitized_values(report),
            analysis=analysis_service.analysis_to_out(analysis),
        ),
    )


async def guest_chat(db: AsyncSession, payload: DemoGuestChatRequest) -> DemoGuestChatReply:
    """Stateless Q&A about a synthetic sample. Nothing is persisted."""
    report = await _guest_report(db, payload.kind)
    language = payload.language or "en"
    if language not in SUPPORTED_LANGUAGES:
        language = "en"

    context = await chat_service.build_report_context(db, report)
    history = [
        ChatTurn(role=turn.role, content=turn.content)
        for turn in payload.history[-GUEST_CHAT_HISTORY_LIMIT:]
    ]

    query_parts = [payload.message]
    if context.get("values"):
        names = [item["test_name"] for item in context["values"][:12]]
        query_parts.append("laboratory tests: " + ", ".join(names) + ".")
    retriever = Retriever(db, get_embedding_provider())
    retrieved = await retriever.retrieve(" ".join(query_parts))

    provider = get_ai_provider()
    answer = await chat_service.generate_chat_answer(
        provider,
        AIChatRequest(
            message=payload.message,
            history=history,
            language=language,
            report_context=context,
            retrieved=retrieved,
        ),
    )
    sources = [ChatSourceOut(**source) for source in answer.sources if source.get("title")]
    return DemoGuestChatReply(
        reply=answer.content,
        sources=sources,
        safety_blocked=answer.safety_blocked,
        disclaimer=MEDICAL_DISCLAIMER,
    )
