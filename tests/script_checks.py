"""Shared Unicode helpers for validating localized content in tests.

Every supported language has one expected script. Localized strings must not
mix scripts (a classic translation bug: a Devanagari letter inside a Bengali
word still looks "almost right" but is a real defect).
"""

from __future__ import annotations

import re

# One script per language that uses a non-Latin script. en/hinglish are Latin.
LANGUAGE_SCRIPTS = {
    "hi": "devanagari",
    "mr": "devanagari",
    "bn": "bengali",
    "as": "bengali",
    "pa": "gurmukhi",
    "gu": "gujarati",
    "or": "odia",
    "ta": "tamil",
    "te": "telugu",
    "kn": "kannada",
    "ml": "malayalam",
    "ur": "arabic",
}

SCRIPT_PATTERNS = {
    "devanagari": r"[\u0900-\u097F]",
    "bengali": r"[\u0980-\u09FF]",
    "gurmukhi": r"[\u0A00-\u0A7F]",
    "gujarati": r"[\u0A80-\u0AFF]",
    "odia": r"[\u0B00-\u0B7F]",
    "tamil": r"[\u0B80-\u0BFF]",
    "telugu": r"[\u0C00-\u0C7F]",
    "kannada": r"[\u0C80-\u0CFF]",
    "malayalam": r"[\u0D00-\u0D7F]",
    "arabic": r"[\u0600-\u06FF]",
}

# Danda is shared by several Indic scripts and is never a script error.
SHARED_CHARS = "।॥"


def present_scripts(text: str) -> set[str]:
    """Names of the non-Latin scripts detected in the text.

    Shared punctuation (danda) is stripped first so it never counts as a
    foreign script.
    """
    probe = text
    for char in SHARED_CHARS:
        probe = probe.replace(char, "")
    return {
        name
        for name, pattern in SCRIPT_PATTERNS.items()
        if re.search(pattern, probe)
    }
