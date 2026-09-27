"""Public share-link route: anonymous, sanitized, read-only report views.

No authentication is required, but the response never contains filenames,
notes, chat history, account data or the original file — only the extracted
values, the analysis and its sources.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.api.deps import DbSession
from backend.schemas.share import SharedReportView
from backend.services import share_service

router = APIRouter(prefix="/shared", tags=["sharing"])


@router.get("/{token}", response_model=SharedReportView)
async def view_shared_report(token: str, db: DbSession) -> SharedReportView:
    return await share_service.resolve_shared_report(db, token)
