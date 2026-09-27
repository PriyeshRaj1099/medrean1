"""Authentication request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from backend.schemas.common import SUPPORTED_LANGUAGES, normalize_phone
from backend.schemas.user import UserOut


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    phone: str | None = None
    locale: str = "en"

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("Password must not start or end with whitespace.")
        if value.isdigit() or value.isalpha():
            raise ValueError("Password must contain at least one letter and one number.")
        return value

    @field_validator("phone")
    @classmethod
    def _normalize_phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalised = normalize_phone(value)
        if normalised is None:
            raise ValueError(
                "Please enter a valid 10-digit Indian mobile number (for example 9876543210)."
            )
        return normalised

    @field_validator("locale")
    @classmethod
    def _valid_locale(cls, value: str) -> str:
        if value not in SUPPORTED_LANGUAGES:
            return "en"
        return value


class LoginRequest(BaseModel):
    """Sign-in accepts either an email address or a registered mobile number."""

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)
    remember: bool = False

    @field_validator("email")
    @classmethod
    def _clean_identifier(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Please enter your email address or mobile number.")
        return cleaned


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str
    # Present only when AUTH_DEV_RETURN_RESET_TOKEN is enabled (development).
    dev_reset_token: str | None = None
    dev_reset_url: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        if value.isdigit() or value.isalpha():
            raise ValueError("Password must contain at least one letter and one number.")
        return value
