"""Shared schema helpers, constants and the medical disclaimer text."""

from __future__ import annotations

import re
from typing import Literal

# Languages supported for the interface AND for AI-generated prose.
# Hinglish (Hindi written in Latin script) is kept as a separate pseudo-locale.
SupportedLanguage = Literal[
    "en",
    "hi",
    "hinglish",
    "bn",
    "ta",
    "te",
    "mr",
    "gu",
    "kn",
    "ml",
    "pa",
    "or",
    "as",
    "ur",
]
SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "en",
    "hi",
    "hinglish",
    "bn",
    "ta",
    "te",
    "mr",
    "gu",
    "kn",
    "ml",
    "pa",
    "or",
    "as",
    "ur",
)

# Human-readable language names used inside prompts and provider adapters.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi (Devanagari script)",
    "hinglish": "Hinglish (Hindi written in Latin script, everyday Indian English mix)",
    "bn": "Bengali (Bangla script)",
    "ta": "Tamil script",
    "te": "Telugu script",
    "mr": "Marathi (Devanagari script)",
    "gu": "Gujarati script",
    "kn": "Kannada script",
    "ml": "Malayalam script",
    "pa": "Punjabi (Gurmukhi script)",
    "or": "Odia script",
    "as": "Assamese (Bengali-Assamese script)",
    "ur": "Urdu (Nastaliq script)",
}


def language_name(code: str) -> str:
    """English name of a supported language code (falls back to English)."""
    return LANGUAGE_NAMES.get(code, LANGUAGE_NAMES["en"])


# Mobile sign-in is India-focused: 10 digits starting 6-9, optional +91 or 0
# prefix. Numbers are stored normalised as +91XXXXXXXXXX.
_INDIAN_MOBILE_RE = re.compile(r"^(?:\+?91|0)?([6-9]\d{9})$")


def normalize_phone(raw: str) -> str | None:
    """Normalise an Indian mobile number to +91XXXXXXXXXX; None when invalid."""
    cleaned = re.sub(r"[\s\-().]", "", (raw or "").strip())
    match = _INDIAN_MOBILE_RE.match(cleaned)
    return f"+91{match.group(1)}" if match else None


def looks_like_phone(value: str) -> bool:
    return normalize_phone(value) is not None

MEDICAL_DISCLAIMER = (
    "This tool provides general educational information about laboratory results and "
    "is not a medical diagnosis or a substitute for professional medical advice. "
    "Laboratory results should be interpreted in the context of a person's symptoms, "
    "history, examination, and the reference ranges provided by the laboratory. "
    "Consult a qualified healthcare professional for personalized interpretation."
)

EMERGENCY_NOTICE = (
    "For urgent or emergency situations, contact your local emergency services "
    "immediately instead of relying on this tool."
)

DEMO_DATA_NOTICE = (
    "This is completely synthetic demo data created for testing purposes. "
    "It does not belong to any real person."
)
