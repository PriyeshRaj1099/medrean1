"""Shared FastAPI dependencies: DB session, current user, authorization helpers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.errors import AuthError, ForbiddenError, NotFoundError
from backend.core.security import TokenError, decode_access_token
from database.models import Report, User
from database.session import get_db

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT access token (Authorization: Bearer <token>)",
)

DbSession = Annotated[AsyncSession, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    agent = request.headers.get("user-agent")
    return agent[:255] if agent else None


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthError("Please sign in to continue.")
    try:
        payload = decode_access_token(credentials.credentials)
    except TokenError as exc:
        message = (
            "Your session has expired. Please sign in again."
            if str(exc) == "token_expired"
            else "Your session is invalid. Please sign in again."
        )
        raise AuthError(message) from exc

    user = (
        await db.execute(select(User).where(User.id == payload.get("sub")))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthError("Your account is not active. Please sign in again.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise ForbiddenError("This area is restricted to administrators.")
    return user


CurrentAdmin = Annotated[User, Depends(get_current_admin)]


def ensure_not_demo(user: User, action: str) -> None:
    """Guard an action that is unavailable to the shared demo account.

    The demo account (`role="demo"`) demonstrates read/analysis features only:
    it is never an administrator and must not mutate shared account state
    (uploads, password, deletion, sessions).
    """
    if user.role == "demo":
        raise ForbiddenError(
            f"The shared demo account {action}. Create your own free account to continue."
        )


async def get_owned_report(report_id: str, user: CurrentUser, db: DbSession) -> Report:
    """Fetch a report only if it belongs to the user (no existence leak)."""
    report = (
        await db.execute(
            select(Report).where(Report.id == report_id, Report.user_id == user.id)
        )
    ).scalar_one_or_none()
    if report is None:
        raise NotFoundError("Report not found.")
    return report


def verify_request_origin(request: Request) -> None:
    """CSRF guard for cookie-authenticated endpoints (refresh/logout).

    Bearer-token endpoints are not CSRF-susceptible, so this is only applied
    where the browser attaches the refresh cookie automatically.
    """
    settings = get_settings()
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if referer:
            from urllib.parse import urlparse

            origin = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}"
    if origin is None:
        # No browser context (e.g. curl / tests) — cookie CSRF requires a browser.
        return
    allowed = set(settings.cors_origins) | {settings.frontend_url.rstrip("/")}
    if origin.rstrip("/") not in {a.rstrip("/") for a in allowed}:
        raise ForbiddenError("Request origin is not allowed.")
