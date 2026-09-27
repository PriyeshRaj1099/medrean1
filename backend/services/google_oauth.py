"""Google sign-in (OAuth 2.0 code flow), gated by configuration.

Security notes:
* Disabled entirely while GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are empty.
* The OAuth `state` parameter is a short-lived HMAC-signed token (CSRF guard).
* The ID token is verified against Google's tokeninfo endpoint and the
  audience is checked against our client id.
* The email returned by Google must be verified before any account is
  looked up, linked or created.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.security import hash_password
from database.base import utcnow
from database.models import User

logger = logging.getLogger("medilens.google")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

STATE_MAX_AGE_SECONDS = 600
HTTP_TIMEOUT = 15.0


def _sign(raw: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.jwt_secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


def create_oauth_state(next_path: str | None = None) -> str:
    payload = {"ts": int(time.time()), "nonce": secrets.token_hex(8), "next": next_path or ""}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    return f"{raw}.{_sign(raw)}"


def verify_oauth_state(state: str) -> dict | None:
    try:
        raw, signature = state.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(raw), signature):
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None
    if int(time.time()) - int(payload.get("ts", 0)) > STATE_MAX_AGE_SECONDS:
        return None
    return payload


def google_redirect_uri() -> str:
    settings = get_settings()
    return f"{settings.frontend_url.rstrip('/')}{settings.api_prefix}/auth/google/callback"


def build_authorize_url(state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "online",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_profile(code: str) -> dict | None:
    """Exchange the code and return {sub, email} when the email is verified."""
    settings = get_settings()
    if not settings.google_signin_enabled:
        return None
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": google_redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
        if token_response.status_code != 200:
            logger.warning(
                "google_token_exchange_failed",
                extra={"event": "google_token_exchange_failed", "status": token_response.status_code},
            )
            return None
        id_token = token_response.json().get("id_token")
        if not id_token:
            return None

        info_response = await client.get(GOOGLE_TOKENINFO_URL, params={"id_token": id_token})
        if info_response.status_code != 200:
            return None
        info = info_response.json()

    if str(info.get("aud", "")) != settings.google_client_id:
        logger.warning("google_token_audience_mismatch", extra={"event": "google_aud_mismatch"})
        return None
    email_verified = info.get("email_verified")
    verified = email_verified is True or str(email_verified).lower() == "true"
    email = (info.get("email") or "").strip().lower()
    subject = str(info.get("sub") or "")
    if not verified or not email or not subject:
        return None
    return {"sub": subject, "email": email, "name": (info.get("name") or "").strip()}


async def get_or_create_google_user(db: AsyncSession, profile: dict) -> User:
    """Find, link or create the local account for a Google-verified email."""
    subject = profile["sub"]
    email = profile["email"]

    user = (
        await db.execute(select(User).where(User.google_id == subject))
    ).scalar_one_or_none()
    if user is not None:
        if not user.is_active:
            raise PermissionError("inactive")
        return user

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is not None:
        if not user.is_active:
            raise PermissionError("inactive")
        if user.google_id and user.google_id != subject:
            raise PermissionError("linked_elsewhere")
        user.google_id = subject
        await db.commit()
        await db.refresh(user)
        return user

    # New account: an unusable random password; the user signs in via Google.
    user = User(
        name=profile.get("name") or email.split("@")[0][:120],
        email=email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role="user",
        locale="en",
        google_id=subject,
        is_active=True,
        last_login_at=utcnow(),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
