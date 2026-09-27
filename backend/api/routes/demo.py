"""Demo mode routes: synthetic reports for exploring the product.

Public (no account needed): /demo/kinds, /demo/samples/{kind}, /demo/chat.
The "Try Demo" flow serves clearly-labelled synthetic data only.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel

from backend.api.deps import CurrentUser, DbSession, client_ip
from backend.core.errors import NotFoundError
from backend.core.rate_limit import GUEST_RULE, limiter
from backend.schemas.common import SupportedLanguage
from backend.schemas.demo import (
    DemoAccountInfo,
    DemoGuestChatReply,
    DemoGuestChatRequest,
    DemoGuestSampleOut,
    DemoKindInfo,
    DemoLoadResponse,
)
from backend.services import demo_account_service, demo_service

logger = logging.getLogger("medilens.demo.api")

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoLoadRequest(BaseModel):
    kind: str


@router.get("/kinds", response_model=list[DemoKindInfo])
async def demo_kinds() -> list[DemoKindInfo]:
    return demo_service.list_demo_kinds()


@router.get("/account", response_model=DemoAccountInfo)
async def demo_account(request: Request) -> DemoAccountInfo:
    """Public credentials of the shared demo account (shown on sign-in).

    These are intentionally public demo credentials; signing in with them
    still goes through the normal authentication pipeline.
    """
    limiter.check(f"demo-account:{client_ip(request) or 'unknown'}", GUEST_RULE)
    info = demo_account_service.get_public_demo_info()
    if not info.enabled:
        raise NotFoundError("The demo account is not available.")
    return info


@router.get("/samples/{kind}", response_model=DemoGuestSampleOut)
async def demo_guest_sample(
    kind: str,
    request: Request,
    db: DbSession,
    language: SupportedLanguage = Query(default="en"),
) -> DemoGuestSampleOut:
    """Public, fully analyzed synthetic sample (the 'Try Demo' flow)."""
    limiter.check(f"demo-sample:{client_ip(request) or 'unknown'}", GUEST_RULE)
    return await demo_service.get_guest_sample(db, kind.strip().lower(), language)


@router.post("/chat", response_model=DemoGuestChatReply)
async def demo_guest_chat(
    payload: DemoGuestChatRequest, request: Request, db: DbSession
) -> DemoGuestChatReply:
    """Public, stateless Q&A about a synthetic sample. Nothing is stored."""
    limiter.check(f"demo-chat:{client_ip(request) or 'unknown'}", GUEST_RULE)
    return await demo_service.guest_chat(db, payload)


@router.post("/load", response_model=DemoLoadResponse, status_code=status.HTTP_201_CREATED)
async def load_demo(payload: DemoLoadRequest, user: CurrentUser, db: DbSession) -> DemoLoadResponse:
    return await demo_service.load_demo_report(db, user, payload.kind.strip().lower())
