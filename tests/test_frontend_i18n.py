"""Frontend i18n catalogs: structural parity, placeholders, scripts, wiring.

These tests inspect the TypeScript sources directly — no browser or Node is
needed. Every catalog must mirror ``en.ts`` key-for-key, keep identical
``{placeholder}`` sets, stay within its own writing system outside the
``language`` autonym section, and stay wired into the locale registry,
fonts, and switcher.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
I18N = SRC / "lib" / "i18n"

LANGUAGES = [
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
ALL_LANGUAGES = ["en", *LANGUAGES]

SECTION_RE = re.compile(r"^  ([A-Za-z0-9]+): \{$")
LEAF_RE = re.compile(r"^    ([A-Za-z0-9]+):(.*)$")
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# Unicode blocks used by the supported scripts. The danda is shared across
# several scripts, so it is treated as punctuation, not a foreign script.
SCRIPT_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "devanagari": ((0x0900, 0x097F),),
    "bengali": ((0x0980, 0x09FF),),
    "gurmukhi": ((0x0A00, 0x0A7F),),
    "gujarati": ((0x0A80, 0x0AFF),),
    "oriya": ((0x0B00, 0x0B7F),),
    "tamil": ((0x0B80, 0x0BFF),),
    "telugu": ((0x0C00, 0x0C7F),),
    "kannada": ((0x0C80, 0x0CFF),),
    "malayalam": ((0x0D00, 0x0D7F),),
    "arabic": ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
}

EXPECTED_SCRIPT: dict[str, str | None] = {
    "en": None,
    "hi": "devanagari",
    "hinglish": None,
    "bn": "bengali",
    "ta": "tamil",
    "te": "telugu",
    "mr": "devanagari",
    "gu": "gujarati",
    "kn": "kannada",
    "ml": "malayalam",
    "pa": "gurmukhi",
    "or": "oriya",
    "as": "bengali",
    "ur": "arabic",
}

AUTONYM_KEYS = [
    "english",
    "hindi",
    "hinglish",
    "bengali",
    "tamil",
    "telugu",
    "marathi",
    "gujarati",
    "kannada",
    "malayalam",
    "punjabi",
    "odia",
    "assamese",
    "urdu",
]

SHARED_PUNCT = {"\u0964", "\u0965"}  # danda / double danda


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _catalog(lang: str) -> str:
    return _read(I18N / f"{lang}.ts")


def _entries(text: str) -> list[tuple[str, str, str]]:
    """Ordered ``(section, key, value)`` leaf entries parsed from a catalog."""
    section = ""
    entries: list[tuple[str, str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        section_match = SECTION_RE.match(line)
        if section_match:
            section = section_match.group(1)
            i += 1
            continue
        leaf = LEAF_RE.match(line)
        if leaf:
            key, fragment = leaf.group(1), leaf.group(2).strip()
            if not fragment.startswith('"') and i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt.startswith('"'):
                    i += 1
                    fragment = nxt
            raw = ""
            if fragment.startswith('"'):
                raw = fragment
                while not raw.endswith('",') and i + 1 < len(lines):
                    i += 1
                    raw += " " + lines[i].strip()
                raw = raw[:-1].strip()  # drop trailing comma
                raw = raw[1:-1] if raw.startswith('"') and raw.endswith('"') else raw
            entries.append((section, key, raw))
        i += 1
    return entries


def _language_section(text: str) -> str:
    return text[text.index("  language: {") : text.index("  theme: {")]


def _outside_language_section(text: str) -> str:
    start = text.index("  language: {")
    end = text.index("  theme: {")
    return text[:start] + text[end:]


def test_every_catalog_mirrors_en_key_order() -> None:
    expected = [(section, key) for section, key, _ in _entries(_catalog("en"))]
    assert len(expected) == 445
    for lang in LANGUAGES:
        actual = [(section, key) for section, key, _ in _entries(_catalog(lang))]
        assert actual == expected, f"{lang}.ts key order diverges from en.ts"


def test_placeholders_are_identical_per_key() -> None:
    en_entries = _entries(_catalog("en"))
    en_placeholders = {
        (section, key): set(PLACEHOLDER_RE.findall(value))
        for section, key, value in en_entries
    }
    for lang in LANGUAGES:
        for section, key, value in _entries(_catalog(lang)):
            assert set(PLACEHOLDER_RE.findall(value)) == en_placeholders[(section, key)], (
                f"{lang}.ts {section}.{key} placeholder mismatch"
            )
    assert PLACEHOLDER_RE.findall(en_entries[0][2]) == []


def test_catalogs_use_only_their_own_script_outside_language_block() -> None:
    all_ranges = [r for ranges in SCRIPT_RANGES.values() for r in ranges]
    for lang in ALL_LANGUAGES:
        text = _outside_language_section(_catalog(lang))
        own = SCRIPT_RANGES.get(EXPECTED_SCRIPT[lang] or "", ())
        for char in text:
            code = ord(char)
            if char in SHARED_PUNCT:
                continue
            in_own = any(lo <= code <= hi for lo, hi in own)
            if in_own:
                continue
            in_any = any(lo <= code <= hi for lo, hi in all_ranges)
            assert not in_any, f"{lang}.ts leaks foreign script char U+{code:04X}"


def test_each_translated_catalog_contains_its_script() -> None:
    for lang, script in EXPECTED_SCRIPT.items():
        if script is None:
            continue
        text = _outside_language_section(_catalog(lang))
        ranges = SCRIPT_RANGES[script]
        count = sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in ranges))
        assert count >= 200, f"{lang}.ts has too few {script} chars ({count})"


def test_autonym_block_is_identical_in_every_catalog() -> None:
    reference = {
        m.group(1): m.group(2)
        for m in re.finditer(r'^\s+([a-z]+): "([^"]*)"', _language_section(_catalog("en")), re.M)
    }
    expected = {key: reference[key] for key in AUTONYM_KEYS}
    assert expected["urdu"] == "اردو"
    assert expected["assamese"] == "অসমীয়া"
    for lang in LANGUAGES:
        actual = {
            m.group(1): m.group(2)
            for m in re.finditer(
                r'^\s+([a-z]+): "([^"]*)"', _language_section(_catalog(lang)), re.M
            )
        }
        assert {key: actual[key] for key in AUTONYM_KEYS} == expected, f"{lang}.ts autonyms differ"


def test_f3_marketing_mentions_14_languages_in_every_catalog() -> None:
    for lang in ALL_LANGUAGES:
        values = {key: value for _, key, value in _entries(_catalog(lang))}
        assert "14" in values["f3Title"], f"{lang}.ts f3Title must mention 14 languages"
        assert "14" in values["f3Text"], f"{lang}.ts f3Text must mention 14 languages"


def test_registry_covers_all_languages() -> None:
    index = _read(I18N / "index.tsx")
    for code in ALL_LANGUAGES:
        assert f'from "./{code}"' in index, f"index.tsx missing import for {code}"
    messages_block = index[index.index("const MESSAGES") : index.index("LANGUAGE_MESSAGE_KEYS")]
    for code in ALL_LANGUAGES:
        assert re.search(rf"\b{code}\b", messages_block), f"MESSAGES missing {code}"
    keys_block = index[index.index("LANGUAGE_MESSAGE_KEYS") : index.index("LOCALE_META")]
    for code, message_key in [
        ("en", "english"),
        ("ur", "urdu"),
        ("as", "assamese"),
        ("or", "odia"),
        ("ml", "malayalam"),
        ("kn", "kannada"),
    ]:
        assert f'{code}: "{message_key}"' in keys_block
    meta_block = index[index.index("LOCALE_META") : index.index("export function")]
    assert 'ur: { htmlLang: "ur", dir: "rtl" }' in meta_block
    assert meta_block.count('dir: "rtl"') == 1


def test_supported_languages_match_catalog_files() -> None:
    types = _read(SRC / "lib" / "types.ts")
    block = types[types.index("SUPPORTED_LANGUAGES") : types.index("/* ----------", types.index("SUPPORTED_LANGUAGES"))]
    for code in ALL_LANGUAGES:
        assert f'"{code}"' in block, f"SUPPORTED_LANGUAGES missing {code}"
        assert (I18N / f"{code}.ts").exists(), f"catalog file {code}.ts missing"


def test_fonts_css_and_switcher_are_wired() -> None:
    layout = _read(SRC / "app" / "layout.tsx")
    for font in [
        "Noto_Sans_Bengali",
        "Noto_Sans_Tamil",
        "Noto_Sans_Telugu",
        "Noto_Sans_Gujarati",
        "Noto_Sans_Kannada",
        "Noto_Sans_Malayalam",
        "Noto_Sans_Gurmukhi",
        "Noto_Sans_Oriya",
        "Noto_Naskh_Arabic",
    ]:
        assert font in layout, f"layout.tsx missing {font}"
    css = _read(SRC / "app" / "globals.css")
    for code in ["hi", "mr", "bn", "as", "ta", "te", "gu", "kn", "ml", "pa", "or", "ur"]:
        assert f'html[lang="{code}"]' in css, f"globals.css missing font stack for {code}"
    switcher = _read(SRC / "components" / "shared" / "language-switcher.tsx")
    assert "SUPPORTED_LANGUAGES.map" in switcher
    assert "LANGUAGE_MESSAGE_KEYS" in switcher
    assert "OPTIONS" not in switcher
