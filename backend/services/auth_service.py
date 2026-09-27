"""Authentication service: registration, login, refresh rotation, reset flow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.errors import AuthError, ValidationAppError
from backend.core.security import (
    create_access_token,
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from database.base import utcnow
from database.models import PasswordResetToken, User, UserSession
from backend.schemas.common import looks_like_phone, normalize_phone

INVALID_CREDENTIALS_MESSAGE = "Incorrect email/mobile number or password. Please check and try again."


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    normalized = email.strip().lower()
    return (await db.execute(select(User).where(User.email == normalized))).scalar_one_or_none()


async def get_user_by_phone(db: AsyncSession, phone: str) -> User | None:
    normalized = normalize_phone(phone)
    if normalized is None:
        return None
    return (await db.execute(select(User).where(User.phone == normalized))).scalar_one_or_none()


async def register_user(
    db: AsyncSession,
    *,
    name: str,
    email: str,
    password: str,
    locale: str = "en",
    phone: str | None = None,
) -> User:
    normalized = email.strip().lower()
    if await get_user_by_email(db, normalized):
        raise ValidationAppError(
            "An account with this email already exists. Try signing in instead.",
            code="email_taken",
        )
    if phone:
        existing_phone = await get_user_by_phone(db, phone)
        if existing_phone is not None:
            raise ValidationAppError(
                "This mobile number is already registered. Try signing in instead.",
                code="phone_taken",
            )
    user = User(
        name=name.strip(),
        email=normalized,
        password_hash=hash_password(password),
        role="user",
        locale=locale,
        phone=normalize_phone(phone) if phone else None,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate(db: AsyncSession, identifier: str, password: str) -> User:
    """Sign in with an email address OR a registered mobile number."""
    if looks_like_phone(identifier):
        user = await get_user_by_phone(db, identifier)
    else:
        user = await get_user_by_email(db, identifier)
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError(INVALID_CREDENTIALS_MESSAGE)
    if not user.is_active:
        raise AuthError("This account has been deactivated.")
    user.last_login_at = utcnow()
    await db.commit()
    return user


# ----------------------------------------------------------------------
# Refresh-token sessions
# ----------------------------------------------------------------------
async def create_session(
    db: AsyncSession,
    user: User,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
    remember: bool = False,
) -> str:
    """Create a refresh session and return the opaque refresh token.

    `remember=False` issues a short-lived session (and a browser-session
    cookie) so shared devices are not left signed in.
    """
    settings = get_settings()
    if remember:
        lifetime = timedelta(days=settings.refresh_token_expire_days)
    else:
        lifetime = timedelta(hours=settings.session_refresh_expire_hours)
    token = generate_opaque_token()
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_opaque_token(token),
        expires_at=utcnow() + lifetime,
        remember=remember,
        user_agent=user_agent,
        ip_address=ip_address,
        created_at=utcnow(),
        last_used_at=utcnow(),
    )
    db.add(session)
    await db.commit()
    return token


async def rotate_session(
    db: AsyncSession,
    refresh_token: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str]:
    """Validate a refresh token, revoke it and issue a fresh one (rotation)."""
    token_hash = hash_opaque_token(refresh_token)
    session = (
        await db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        raise AuthError("Your session has ended. Please sign in again.")

    now = utcnow()
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        session.revoked_at = now
        await db.commit()
        raise AuthError("Your session has expired. Please sign in again.")

    user = (
        await db.execute(select(User).where(User.id == session.user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthError("Your account is not active. Please sign in again.")

    # Rotate: revoke the old row, create a new one.
    session.revoked_at = now
    await db.commit()
    new_token = await create_session(
        db,
        user,
        ip_address=ip_address,
        user_agent=user_agent,
        remember=bool(session.remember),
    )
    return user, new_token


async def revoke_session_by_token(db: AsyncSession, refresh_token: str) -> None:
    token_hash = hash_opaque_token(refresh_token)
    session = (
        await db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if session and session.revoked_at is None:
        session.revoked_at = utcnow()
        await db.commit()


async def revoke_all_sessions(db: AsyncSession, user_id: str) -> int:
    result = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()
    return result.rowcount or 0


# ----------------------------------------------------------------------
# Password reset
# ----------------------------------------------------------------------
async def create_password_reset_token(db: AsyncSession, user: User) -> str:
    settings = get_settings()
    token = generate_opaque_token()
    entry = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_opaque_token(token),
        expires_at=utcnow()
        + timedelta(minutes=settings.password_reset_token_expire_minutes),
        created_at=utcnow(),
    )
    db.add(entry)
    await db.commit()
    return token


async def consume_password_reset_token(db: AsyncSession, token: str, new_password: str) -> User:
    token_hash = hash_opaque_token(token)
    entry = (
        await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    now = utcnow()
    if entry is None or entry.used_at is not None:
        raise ValidationAppError(
            "This reset link is invalid or has already been used. Please request a new one.",
            code="reset_token_invalid",
        )
    expires_at = entry.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        raise ValidationAppError(
            "This reset link has expired. Please request a new one.",
            code="reset_token_expired",
        )
    user = (await db.execute(select(User).where(User.id == entry.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise ValidationAppError("This account is not available.", code="account_unavailable")

    entry.used_at = now
    user.password_hash = hash_password(new_password)
    await db.commit()
    # A password change ends every existing session.
    await revoke_all_sessions(db, user.id)
    return user


def issue_access_token(user: User) -> tuple[str, datetime]:
    return create_access_token(user.id, user.role)
