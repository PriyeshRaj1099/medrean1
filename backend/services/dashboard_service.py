"""Dashboard statistics and trend series."""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.schemas.common import MEDICAL_DISCLAIMER
from backend.schemas.dashboard import (
    DashboardStats,
    TrendPoint,
    TrendSeries,
    TrendsResponse,
    TrendTestOption,
)
from backend.services import report_service
from database.models import Analysis, ExtractedValue, Report, User

logger = logging.getLogger("medilens.dashboard")

TREND_RANGE_NOTE = (
    "Trends show only the values as printed on each report, plotted in report order. "
    "A rising or falling line does not by itself indicate that a change is good or bad."
)


async def get_stats(db: AsyncSession, user: User, recent_limit: int = 5) -> DashboardStats:
    report_conditions = (Report.user_id == user.id,)

    reports_total = (
        await db.execute(select(func.count()).select_from(Report).where(*report_conditions))
    ).scalar_one()
    reports_analyzed = (
        await db.execute(
            select(func.count())
            .select_from(Report)
            .where(*report_conditions, Report.status == "completed")
        )
    ).scalar_one()
    reports_processing = (
        await db.execute(
            select(func.count())
            .select_from(Report)
            .where(
                *report_conditions,
                Report.status.in_(("uploaded", "processing", "extracting", "analyzing")),
            )
        )
    ).scalar_one()

    values_outside = (
        await db.execute(
            select(func.count())
            .select_from(ExtractedValue)
            .join(Report, ExtractedValue.report_id == Report.id)
            .where(Report.user_id == user.id, ExtractedValue.status.in_(("below", "above")))
        )
    ).scalar_one()

    low_confidence = (
        await db.execute(
            select(func.count())
            .select_from(ExtractedValue)
            .join(Report, ExtractedValue.report_id == Report.id)
            .where(
                Report.user_id == user.id,
                ExtractedValue.confidence < get_settings().low_confidence_threshold,
            )
        )
    ).scalar_one()

    last_row = (
        await db.execute(
            select(Analysis.created_at, Analysis.report_id)
            .join(Report, Analysis.report_id == Report.id)
            .where(Report.user_id == user.id, Analysis.status == "completed")
            .order_by(Analysis.created_at.desc())
            .limit(1)
        )
    ).first()

    recent, _ = await report_service.list_reports(db, user, limit=recent_limit, offset=0)

    return DashboardStats(
        reports_analyzed=int(reports_analyzed or 0),
        reports_total=int(reports_total or 0),
        reports_processing=int(reports_processing or 0),
        values_outside_range=int(values_outside or 0),
        values_low_confidence=int(low_confidence or 0),
        last_analysis_at=last_row[0] if last_row else None,
        last_analysis_report_id=last_row[1] if last_row else None,
        recent_reports=[report_service.report_to_summary(report) for report in recent],
    )


async def get_trends(
    db: AsyncSession,
    user: User,
    *,
    test_keys: list[str] | None = None,
    max_series: int = 6,
    max_reports: int = 12,
) -> TrendsResponse:
    rows = (
        (
            await db.execute(
                select(ExtractedValue, Report)
                .join(Report, ExtractedValue.report_id == Report.id)
                .where(
                    Report.user_id == user.id,
                    ExtractedValue.test_key.is_not(None),
                    ExtractedValue.value_numeric.is_not(None),
                    Report.status.in_(("extracted", "completed")),
                )
                .order_by(Report.created_at.asc())
            )
        )
        .all()
    )

    by_key: dict[str, list[tuple[ExtractedValue, Report]]] = defaultdict(list)
    for value, report in rows:
        assert value.test_key is not None
        by_key[value.test_key].append((value, report))

    # Available tests: keys present in at least two reports are trendable,
    # but single-report tests are still listed as options.
    options: list[TrendTestOption] = []
    for key, entries in by_key.items():
        reports_seen = {report.id for _, report in entries}
        latest = entries[-1][0]
        options.append(
            TrendTestOption(
                test_key=key,
                test_name=latest.test_name,
                unit=latest.unit,
                report_count=len(reports_seen),
            )
        )
    options.sort(key=lambda option: (-option.report_count, option.test_name))

    if test_keys:
        selected = [key for key in test_keys if key in by_key][:max_series]
    else:
        selected = [option.test_key for option in options if option.report_count >= 2][:3]
        if not selected:
            selected = [option.test_key for option in options[:3]]

    series: list[TrendSeries] = []
    for key in selected:
        entries = by_key[key]
        # Keep the latest value per report; order chronologically.
        per_report: dict[str, tuple[ExtractedValue, Report]] = {}
        for value, report in entries:
            per_report[report.id] = (value, report)
        ordered = sorted(
            per_report.values(),
            key=lambda pair: (
                pair[1].report_date or pair[1].created_at.date(),
                pair[1].created_at,
            ),
        )[-max_reports:]
        points = [
            TrendPoint(
                report_id=report.id,
                report_name=report.original_filename,
                report_date=report.report_date,
                value_numeric=value.value_numeric or 0.0,
                unit=value.unit,
                ref_low=value.ref_low,
                ref_high=value.ref_high,
                status=value.status,
            )
            for value, report in ordered
            if value.value_numeric is not None
        ]
        if len(points) < 2:
            continue
        latest_value = ordered[-1][0]
        series.append(
            TrendSeries(
                test_key=key,
                test_name=latest_value.test_name,
                unit=latest_value.unit,
                points=points,
            )
        )

    notes = [TREND_RANGE_NOTE, MEDICAL_DISCLAIMER]
    if test_keys and not series:
        notes.append(
            "No trend could be drawn for the selected test yet — "
            "at least two reports containing that test are needed."
        )
    return TrendsResponse(available_tests=options, series=series, notes=notes)
