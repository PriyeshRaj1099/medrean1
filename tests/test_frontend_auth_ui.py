"""Frontend auth UX: demo-account wiring, password toggle, friendly errors.

Source-level checks (no browser or Node needed): the sign-in page must fetch
the public demo credentials from the API — never hardcode them — prefill the
form (via the button and via ``/login?demo=1``), offer a show/hide password
toggle, and still submit through the normal authentication pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
LOGIN_PAGE = SRC / "app" / "(auth)" / "login" / "page.tsx"
LANDING_PAGE = SRC / "app" / "page.tsx"
API_TS = SRC / "lib" / "api.ts"
TYPES_TS = SRC / "lib" / "types.ts"
EN_CATALOG = SRC / "lib" / "i18n" / "en.ts"
DEMO_SCHEMA = ROOT / "backend" / "schemas" / "demo.py"

DEMO_PASSWORD = "MediLens@Demo2026!"
DEMO_EMAIL = "demo@medilens.ai"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_login_page_fetches_demo_credentials_from_api() -> None:
    login = _read(LOGIN_PAGE)
    assert '"/api/demo/account"' in login
    assert "auth: false" in login, "the public demo endpoint needs no token"
    assert "info.enabled" in login, "hide the demo section when the demo is disabled"
    assert "DemoAccountInfo" in login


def test_login_page_never_hardcodes_demo_credentials() -> None:
    for path in SRC.rglob("*"):
        if not path.is_file() or path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".json"}:
            continue
        text = _read(path)
        assert DEMO_PASSWORD not in text, f"{path} hardcodes the demo password"
        assert DEMO_EMAIL not in text, f"{path} hardcodes the demo email"


def test_login_page_prefills_demo_via_button_and_query_param() -> None:
    login = _read(LOGIN_PAGE)
    assert 'searchParams.get("demo") === "1"' in login, "support /login?demo=1 prefill"
    assert "messages.auth.tryDemo" in login
    assert "messages.auth.demoNote" in login
    assert "messages.auth.demoFilled" in login
    assert "setEmail(info.email)" in login and "setPassword(info.password)" in login


def test_login_page_has_show_hide_password_toggle() -> None:
    login = _read(LOGIN_PAGE)
    assert 'showPassword ? "text" : "password"' in login
    assert "messages.auth.showPassword" in login
    assert "messages.auth.hidePassword" in login
    assert "EyeOff" in login and "Eye" in login
    assert "aria-label=" in login


def test_login_submits_through_normal_auth_pipeline() -> None:
    login = _read(LOGIN_PAGE)
    assert "await login(email.trim(), password)" in login, (
        "auto-filled demo credentials must still go through the normal login call"
    )


def test_landing_demo_ctas_open_login_with_demo_prefill() -> None:
    landing = _read(LANDING_PAGE)
    assert landing.count('"/login?demo=1"') == 2, (
        "both anonymous demo CTAs (hero + demo card) must open /login?demo=1"
    )


def test_api_client_maps_common_statuses_to_friendly_text() -> None:
    api_ts = _read(API_TS)
    block = api_ts[
        api_ts.index("function fallbackErrorMessage") : api_ts.index("async function parseError")
    ]
    for fragment in [
        "status === 401",
        "status === 403",
        "status === 404",
        "status === 429",
        "status >= 500",
    ]:
        assert fragment in block, f"fallbackErrorMessage missing {fragment}"
    assert "fallbackErrorMessage(response.status)" in api_ts


def test_typescript_demo_account_info_mirrors_backend_schema() -> None:
    schema = _read(DEMO_SCHEMA)
    block = schema[schema.index("class DemoAccountInfo") : schema.index("class DemoGuestSampleOut")]
    fields = re.findall(r"^    ([a-z_]+):", block, re.M)
    assert fields == ["enabled", "email", "password", "name", "notice"]
    types = _read(TYPES_TS)
    start = types.index("interface DemoAccountInfo")
    ts_block = types[start : types.index("}", start)]
    for field in fields:
        assert f"{field}:" in ts_block, f"DemoAccountInfo missing field {field}"


def test_auth_demo_i18n_keys_exist_in_en_catalog() -> None:
    en = _read(EN_CATALOG)
    for key in ["showPassword", "hidePassword", "tryDemo", "demoNote", "demoFilled"]:
        assert f"{key}:" in en, f"en.ts missing auth key {key}"
