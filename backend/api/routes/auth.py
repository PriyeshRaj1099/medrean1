"""Authentication routes: register, login, refresh, logout, profile, reset."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from backend.api.deps import (
    CurrentUser,
    DbSession,
    client_ip,
    ensure_not_demo,
    user_agent,
    verify_request_origin,
)
from backend.core.config import get_settings
from backend.core.errors import AuthError, NotFoundError, ValidationAppError
from backend.core.rate_limit import AUTH_RULE, limiter
from backend.core.security import hash_password, verify_password
from backend.schemas.auth import (
    AccessTokenResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from backend.schemas.common import looks_like_phone, normalize_phone
from backend.schemas.user import (
    DeleteAccountRequest,
    MessageResponse,
    PasswordChange,
    UserOut,
    UserUpdate,
)
from backend.services import audit_service, auth_service, google_oauth, privacy_service
from database.base import utcnow
from database.models import UserSession

logger = logging.getLogger("medilens.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_RESET_MESSAGE = (
    "If an account exists for that email address, a password reset link has been sent."
)


def _set_refresh_cookie(response: Response, token: str, *, remember: bool = False) -> None:
    """Set the refresh cookie.

    Without "Remember me" the cookie is a browser-session cookie (no Max-Age)
    and the server-side session expires quickly.
    """
    settings = get_settings()
    max_age = settings.refresh_token_expire_days * 24 * 3600 if remember else None
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=settings.refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=settings.refresh_cookie_path,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, request: Request, response: Response, db: DbSession
) -> TokenResponse:
    limiter.check(f"register:{client_ip(request) or 'unknown'}", AUTH_RULE)
    user = await auth_service.register_user(
        db,
        name=payload.name,
        email=str(payload.email),
        password=payload.password,
        locale=payload.locale,
        phone=payload.phone,
    )
    refresh_token = await auth_service.create_session(
        db, user, ip_address=client_ip(request), user_agent=user_agent(request)
    )
    _set_refresh_cookie(response, refresh_token)
    access_token, expires_at = auth_service.issue_access_token(user)
    await audit_service.record(
        db, "auth.register", user_id=user.id, entity_type="user", entity_id=user.id,
        ip_address=client_ip(request), user_agent=user_agent(request),
    )
    return TokenResponse(
        access_token=access_token, expires_at=expires_at, user=UserOut.model_validate(user)
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, request: Request, response: Response, db: DbSession
) -> TokenResponse:
    identifier = payload.email.strip()
    is_phone = looks_like_phone(identifier)
    if is_phone:
        rate_key = normalize_phone(identifier) or "phone"
    else:
        rate_key = identifier.lower()
    limiter.check(f"login:{client_ip(request) or 'unknown'}:{rate_key}", AUTH_RULE)

    try:
        user = await auth_service.authenticate(db, identifier, payload.password)
    except AuthError:
        # Never store the raw identifier (PII) in audit logs.
        detail = {"identifier_type": "phone" if is_phone else "email"}
        if not is_phone:
            detail["email_domain"] = identifier.split("@")[-1].lower()[:80]
        await audit_service.record(
            db, "auth.login_failed", entity_type="user", ip_address=client_ip(request),
            user_agent=user_agent(request), detail=detail,
        )
        raise

    refresh_token = await auth_service.create_session(
        db,
        user,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        remember=payload.remember,
    )
    _set_refresh_cookie(response, refresh_token, remember=payload.remember)
    access_token, expires_at = auth_service.issue_access_token(user)
    await audit_service.record(
        db, "auth.login", user_id=user.id, entity_type="user", entity_id=user.id,
        ip_address=client_ip(request), user_agent=user_agent(request),
        detail={"remember": payload.remember},
    )
    return TokenResponse(
        access_token=access_token, expires_at=expires_at, user=UserOut.model_validate(user)
    )


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(request: Request, response: Response, db: DbSession) -> AccessTokenResponse:
    verify_request_origin(request)  # CSRF guard (cookie-based endpoint)
    settings = get_settings()
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise AuthError("Your session has ended. Please sign in again.")

    user, new_token = await auth_service.rotate_session(
        db, token, ip_address=client_ip(request), user_agent=user_agent(request)
    )
    _set_refresh_cookie(response, new_token)
    access_token, expires_at = auth_service.issue_access_token(user)
    return AccessTokenResponse(access_token=access_token, expires_at=expires_at)


@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request, response: Response, db: DbSession) -> MessageResponse:
    verify_request_origin(request)
    settings = get_settings()
    token = request.cookies.get(settings.refresh_cookie_name)
    if token:
        await auth_service.revoke_session_by_token(db, token)
        await audit_service.record(
            db, "auth.logout", ip_address=client_ip(request), user_agent=user_agent(request),
        )
    _clear_refresh_cookie(response)
    return MessageResponse(message="You have been signed out.")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: UserUpdate, user: CurrentUser, request: Request, db: DbSession
) -> UserOut:
    changed = False
    if payload.name is not None:
        user.name = payload.name.strip()
        changed = True
    if payload.locale is not None:
        user.locale = payload.locale
        changed = True
    if "phone" in payload.model_fields_set and payload.phone != user.phone:
        if payload.phone:
            existing = await auth_service.get_user_by_phone(db, payload.phone)
            if existing is not None and existing.id != user.id:
                raise ValidationAppError(
                    "This mobile number is already registered to another account.",
                    code="phone_taken",
                )
        user.phone = payload.phone
        changed = True
    if payload.explanation_level is not None and payload.explanation_level != user.explanation_level:
        user.explanation_level = payload.explanation_level
        changed = True
    if (
        payload.notifications_enabled is not None
        and payload.notifications_enabled != user.notifications_enabled
    ):
        user.notifications_enabled = payload.notifications_enabled
        changed = True
    if changed:
        await db.commit()
        await db.refresh(user)
        await audit_service.record(
            db, "auth.profile_updated", user_id=user.id, entity_type="user", entity_id=user.id,
            ip_address=client_ip(request),
        )
    return UserOut.model_validate(user)


@router.get("/providers")
async def auth_providers() -> dict:
    """Which sign-in methods this deployment offers (drives the login UI)."""
    settings = get_settings()
    return {"google_signin": settings.google_signin_enabled, "phone_signin": True}


@router.get("/google/start")
async def google_start(request: Request) -> RedirectResponse:
    """Begin the Google OAuth flow (302 to Google)."""
    settings = get_settings()
    if not settings.google_signin_enabled:
        raise NotFoundError("Google sign-in is not enabled on this server.")
    limiter.check(f"google:{client_ip(request) or 'unknown'}", AUTH_RULE)
    state = google_oauth.create_oauth_state()
    return RedirectResponse(google_oauth.build_authorize_url(state), status_code=302)


@router.get("/google/callback")
async def google_callback(
    request: Request,
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Google's redirect: verify, sign in, then bounce to the frontend."""
    settings = get_settings()
    frontend = settings.frontend_url.rstrip("/")
    callback_url = f"{frontend}/auth/callback"

    if not settings.google_signin_enabled:
        return RedirectResponse(f"{callback_url}?status=disabled", status_code=302)
    if error or not code or not state or google_oauth.verify_oauth_state(state) is None:
        return RedirectResponse(f"{callback_url}?status=error&reason=state", status_code=302)

    try:
        profile = await google_oauth.exchange_code_for_profile(code)
    except Exception:
        logger.exception("google_exchange_failed", extra={"event": "google_exchange_failed"})
        profile = None
    if profile is None:
        return RedirectResponse(f"{callback_url}?status=error&reason=exchange", status_code=302)

    try:
        user = await google_oauth.get_or_create_google_user(db, profile)
    except PermissionError as exc:
        reason = exc.args[0] if exc.args else "account"
        return RedirectResponse(f"{callback_url}?status=error&reason={reason}", status_code=302)

    refresh_token = await auth_service.create_session(
        db, user, ip_address=client_ip(request), user_agent=user_agent(request), remember=True
    )
    response = RedirectResponse(f"{callback_url}?status=ok", status_code=302)
    _set_refresh_cookie(response, refresh_token, remember=True)
    await audit_service.record(
        db, "auth.login_google", user_id=user.id, entity_type="user", entity_id=user.id,
        ip_address=client_ip(request), user_agent=user_agent(request),
    )
    return response


@router.get("/export")
async def export_account_data(user: CurrentUser, db: DbSession, request: Request) -> JSONResponse:
    """Download everything stored about this account as a JSON file."""
    limiter.check(f"export:{user.id}", AUTH_RULE)
    data = await privacy_service.build_user_export(db, user)
    await audit_service.record(
        db, "auth.data_exported", user_id=user.id, entity_type="user", entity_id=user.id,
        ip_address=client_ip(request),
    )
    filename = f"medilens-data-export-{utcnow().strftime('%Y-%m-%d')}.json"
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/account", response_model=MessageResponse)
async def delete_account(
    payload: DeleteAccountRequest,
    user: CurrentUser,
    request: Request,
    response: Response,
    db: DbSession,
) -> MessageResponse:
    """Permanently delete the account, its reports and stored files."""
    limiter.check(f"delete-account:{user.id}", AUTH_RULE)
    ensure_not_demo(user, "cannot be deleted")
    await privacy_service.delete_user_account(db, user, payload.password)
    _clear_refresh_cookie(response)
    logger.info("account deleted", extra={"event": "account_deleted"})
    return MessageResponse(
        message="Your account and all stored data have been permanently deleted."
    )


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    payload: PasswordChange, user: CurrentUser, request: Request, db: DbSession
) -> MessageResponse:
    ensure_not_demo(user, "cannot change its password")
    if not verify_password(payload.current_password, user.password_hash):
        raise ValidationAppError("Your current password is incorrect.", code="invalid_password")
    user.password_hash = hash_password(payload.new_password)
    await db.commit()
    await auth_service.revoke_all_sessions(db, user.id)
    await audit_service.record(
        db, "auth.profile_updated", user_id=user.id, entity_type="user", entity_id=user.id,
        ip_address=client_ip(request), detail={"password_changed": True},
    )
    return MessageResponse(
        message="Password updated. Please sign in again with your new password."
    )


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    payload: ForgotPasswordRequest, request: Request, db: DbSession
) -> ForgotPasswordResponse:
    settings = get_settings()
    client = client_ip(request) or "unknown"
    limiter.check(f"forgot:{client}:{payload.email}", AUTH_RULE)

    user = await auth_service.get_user_by_email(db, str(payload.email))
    dev_token: str | None = None
    # The shared demo account never issues reset tokens: its password is a
    # public, fixed credential managed only by the seed script.
    if user is not None and user.is_active and user.role != "demo":
        dev_token = await auth_service.create_password_reset_token(db, user)
        await audit_service.record(
            db, "auth.password_reset_requested", user_id=user.id, entity_type="user",
            entity_id=user.id, ip_address=client_ip(request),
        )
        # No email provider is configured in this deployment, so the token is
        # deliberately NOT included in the response unless explicitly enabled.
        logger.info(
            "password reset token generated (email delivery not configured)",
            extra={"event": "password_reset_token_generated", "user_id": user.id},
        )

    response = ForgotPasswordResponse(message=GENERIC_RESET_MESSAGE)
    if settings.auth_dev_return_reset_token and dev_token:
        response.dev_reset_token = dev_token
        response.dev_reset_url = f"{settings.frontend_url}/reset-password?token={dev_token}"
    return response


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    payload: ResetPasswordRequest, request: Request, db: DbSession
) -> MessageResponse:
    limiter.check(f"reset:{client_ip(request) or 'unknown'}", AUTH_RULE)
    user = await auth_service.consume_password_reset_token(db, payload.token, payload.new_password)
    await audit_service.record(
        db, "auth.password_reset_completed", user_id=user.id, entity_type="user",
        entity_id=user.id, ip_address=client_ip(request),
    )
    return MessageResponse(message="Your password has been reset. Please sign in.")


@router.get("/sessions")
async def list_sessions(user: CurrentUser, db: DbSession) -> list[dict]:
    # Sessions of the shared demo account would expose other visitors' devices.
    ensure_not_demo(user, "does not expose session management")
    rows = (
        await db.execute(
            select(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .order_by(UserSession.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    now = utcnow()
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "user_agent": row.user_agent,
            "active": bool(row.expires_at and row.expires_at.replace(tzinfo=None) > now.replace(tzinfo=None)),
        }
        for row in rows
    ]


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
async def revoke_session(session_id: str, user: CurrentUser, db: DbSession) -> MessageResponse:
    ensure_not_demo(user, "does not expose session management")
    row = (
        await db.execute(
            select(UserSession).where(
                UserSession.id == session_id, UserSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ValidationAppError("Session not found.", code="session_not_found")
    row.revoked_at = utcnow()
    await db.commit()
    return MessageResponse(message="Session revoked.")
