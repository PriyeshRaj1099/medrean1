"""Password hashing, JWT helpers and opaque-token utilities."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from backend.core.config import get_settings

ACCESS_TOKEN_TYPE = "access"


# ----------------------------------------------------------------------
# Passwords
# ----------------------------------------------------------------------
def _prehash(password: str) -> bytes:
    """SHA-256 pre-hash so bcrypt's 72-byte limit never truncates passwords."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ----------------------------------------------------------------------
# JWT access tokens
# ----------------------------------------------------------------------
def create_access_token(
    user_id: str, role: str, expires_minutes: int | None = None
) -> tuple[str, datetime]:
    settings = get_settings()
    minutes = expires_minutes or settings.access_token_expire_minutes
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=minutes)
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, expires_at


class TokenError(Exception):
    """Raised when a JWT is missing, malformed or expired."""


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("token_invalid") from exc
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise TokenError("token_invalid")
    return payload


# ----------------------------------------------------------------------
# Opaque tokens (refresh tokens, password resets)
# ----------------------------------------------------------------------
def generate_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
