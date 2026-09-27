"""User profile schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.common import SUPPORTED_LANGUAGES, normalize_phone
from database.models.user import EXPLANATION_LEVELS


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str
    phone: str | None = None
    role: str
    locale: str
    explanation_level: str = "simple"
    notifications_enabled: bool = True
    created_at: datetime
    last_login_at: datetime | None = None


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    locale: str | None = None
    phone: str | None = Field(default=None, max_length=20)
    explanation_level: str | None = None
    notifications_enabled: bool | None = None

    @field_validator("locale")
    @classmethod
    def _valid_locale(cls, value: str | None) -> str | None:
        if value is not None and value not in SUPPORTED_LANGUAGES:
            raise ValueError(f"locale must be one of {SUPPORTED_LANGUAGES}")
        return value

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalised = normalize_phone(value)
        if normalised is None:
            raise ValueError("Please enter a valid 10-digit Indian mobile number.")
        return normalised

    @field_validator("explanation_level")
    @classmethod
    def _valid_level(cls, value: str | None) -> str | None:
        if value is not None and value not in EXPLANATION_LEVELS:
            raise ValueError(f"explanation_level must be one of {EXPLANATION_LEVELS}")
        return value


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class DeleteAccountRequest(BaseModel):
    """Destructive action: the current password must be re-entered."""

    password: str = Field(min_length=1, max_length=128)


class MessageResponse(BaseModel):
    message: str


class PrivacySettings(BaseModel):
    """Data-retention preferences surfaced in Settings."""

    retention_days: int = Field(ge=0, le=3650)
    analytics_opt_out: bool = False
