"""Dashboard routes: statistics and trends."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query

from backend.api.deps import CurrentUser, DbSession
from backend.schemas.dashboard import DashboardStats, TrendsResponse
from backend.services import dashboard_service

logger = logging.getLogger("medilens.dashboard.api")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def stats(user: CurrentUser, db: DbSession) -> DashboardStats:
    return await dashboard_service.get_stats(db, user)


@router.get("/trends", response_model=TrendsResponse)
async def trends(
    user: CurrentUser,
    db: DbSession,
    test_key: Annotated[list[str] | None, Query(description="Test keys to plot")] = None,
) -> TrendsResponse:
    return await dashboard_service.get_trends(db, user, test_keys=test_key)
