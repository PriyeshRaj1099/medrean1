"""Multi-report comparison with neutral, non-judgemental wording."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.errors import ValidationAppError
from backend.schemas.common import MEDICAL_DISCLAIMER
from backend.schemas.report import (
    CompareEntry,
    CompareReportMeta,
    CompareResponse,
    CompareRow,
    ReferenceRangeOut,
)
from database.models import ExtractedValue, Report, User

logger = logging.getLogger("medilens.compare")

NEUTRAL_NOTE = (
    "This comparison shows only the values as printed on each report. "
    "An increase or decrease does not by itself indicate that a change is good or bad; "
    "interpretation depends on the reference ranges and your clinical context."
)

UNIT_MISMATCH_NOTE = (
    "Some values appear with different units across these reports, so they may not be "
    "directly comparable. Please review them with a healthcare professional."
)


def _row_key(value: ExtractedValue) -> str:
    return value.test_key or value.test_name.strip().lower()


def _format_value(value: ExtractedValue) -> str:
    if value.value_numeric is not None:
        return f"{value.value_numeric:g}"
    return value.value_text or "—"


def _describe_change(first: ExtractedValue, last: ExtractedValue, unit: str | None) -> tuple[str, float | None]:
    """Neutral description of how the reported value changed (or did not)."""
    unit_suffix = f" {unit}" if unit else ""
    if first is last:
        return f"Reported value: {_format_value(last)}{unit_suffix}", None
    if first.value_numeric is None or last.value_numeric is None:
        if _format_value(first) == _format_value(last):
            return f"Reported value stayed at {_format_value(last)}{unit_suffix}", None
        return (
            f"Reported value changed from '{_format_value(first)}' to "
            f"'{_format_value(last)}'",
            None,
        )
    delta = round(last.value_numeric - first.value_numeric, 6)
    if delta == 0:
        return f"Reported value stayed at {_format_value(last)}{unit_suffix}", 0.0
    return (
        f"Reported value changed from {_format_value(first)} to "
        f"{_format_value(last)}{unit_suffix}",
        delta,
    )


async def compare_reports(
    db: AsyncSession, user: User, report_ids: list[str]
) -> CompareResponse:
    unique_ids = list(dict.fromkeys(report_ids))
    if not 2 <= len(unique_ids) <= 6:
        raise ValidationAppError(
            "Please select between 2 and 6 reports to compare.", code="invalid_selection"
        )

    reports = (
        (
            await db.execute(
                select(Report)
                .options(selectinload(Report.values))
                .where(Report.user_id == user.id, Report.id.in_(unique_ids))
            )
        )
        .scalars()
        .all()
    )
    if len(reports) != len(unique_ids):
        raise ValidationAppError(
            "One or more of the selected reports could not be found.", code="report_not_found"
        )

    ordered = sorted(
        reports,
        key=lambda report: (report.report_date or report.created_at.date(), report.created_at),
    )

    metas = [
        CompareReportMeta(
            id=report.id,
            original_filename=report.original_filename,
            report_date=report.report_date,
            created_at=report.created_at,
        )
        for report in ordered
    ]

    # Collect values per report in a stable order.
    per_report: dict[str, dict[str, ExtractedValue]] = {}
    row_order: list[str] = []
    row_names: dict[str, str] = {}
    for report in ordered:
        per_report[report.id] = {}
        for value in sorted(report.values, key=lambda v: v.sort_order):
            key = _row_key(value)
            if key not in per_report[report.id]:
                per_report[report.id][key] = value
            if key not in row_names:
                row_names[key] = value.test_name
                row_order.append(key)

    rows: list[CompareRow] = []
    unit_mismatch = False
    for key in row_order:
        present: list[tuple[Report, ExtractedValue]] = [
            (report, per_report[report.id][key])
            for report in ordered
            if key in per_report[report.id]
        ]
        _first_report, first_value = present[0]
        _last_report, last_value = present[-1]
        units = {value.unit for _, value in present if value.unit}
        if len(units) > 1:
            unit_mismatch = True
        unit = last_value.unit or (next(iter(units)) if units else None)

        change, delta = _describe_change(first_value, last_value, unit)
        entries = [
            CompareEntry(
                report_id=report.id,
                report_name=report.original_filename,
                report_date=report.report_date,
                value_numeric=value.value_numeric,
                value_text=value.value_text,
                unit=value.unit,
                reference_range=ReferenceRangeOut(
                    low=value.ref_low, high=value.ref_high, text=value.ref_text
                ),
                status=value.status,
            )
            for report, value in present
        ]
        rows.append(
            CompareRow(
                test_key=key,
                test_name=row_names[key],
                unit=unit,
                entries=entries,
                change=change,
                delta=delta,
            )
        )

    notes = [NEUTRAL_NOTE]
    if unit_mismatch:
        notes.append(UNIT_MISMATCH_NOTE)
    if any(len(per_report[report.id]) < len(row_order) for report in ordered):
        notes.append(
            "Some tests do not appear in every selected report; "
            "only the reports that contain them are shown for those tests."
        )
    notes.append(MEDICAL_DISCLAIMER)
    return CompareResponse(reports=metas, rows=rows, notes=notes)
