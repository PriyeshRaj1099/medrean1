"""Persistent demo account: idempotent seed, standard login, safety rails.

The shared demo account lives in the database (seeded — never hard-coded into
the frontend), authenticates through exactly the same pipeline as normal
users, and carries the non-admin role ``demo`` with deliberate restrictions:
no admin access, no uploads, no password changes, no deletion, no session
management and no password-reset tokens.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import func, select

from backend.core.config import get_settings
from backend.core.security import verify_password
from backend.services import demo_account_service
from backend.services.demo_reports import DEMO_REPORTS
from database.models import Analysis, Report, User


async def _seed(db) -> dict:
    return await demo_account_service.seed_demo_account(db)


async def _demo_login(client: AsyncClient) -> dict[str, str]:
    settings = get_settings()
    response = await client.post(
        "/api/auth/login",
        json={
            "email": settings.demo_account_email,
            "password": settings.demo_account_password,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_seed_creates_demo_account_with_analyzed_reports(
    db_session, seeded_kb
) -> None:
    result = await _seed(db_session)
    assert result["enabled"] is True
    assert result["created_user"] is True
    assert result["created_reports"] == len(DEMO_REPORTS)
    assert result["password_matches"] is True

    user = await demo_account_service.get_demo_user(db_session)
    assert user is not None
    assert user.role == "demo"
    assert user.is_active is True
    # The password is stored hashed, never in plaintext.
    assert user.password_hash != get_settings().demo_account_password
    assert verify_password(get_settings().demo_account_password, user.password_hash)

    reports = (
        (await db_session.execute(select(Report).where(Report.user_id == user.id)))
        .scalars()
        .all()
    )
    assert len(reports) == len(DEMO_REPORTS)
    assert all(report.is_demo for report in reports)

    for report in reports:
        analysis = (
            await db_session.execute(
                select(Analysis).where(
                    Analysis.report_id == report.id, Analysis.status == "completed"
                )
            )
        ).scalar_one_or_none()
        assert analysis is not None, f"{report.storage_key} has no completed analysis"


async def test_seed_is_idempotent_and_never_overwrites_the_password(
    db_session, seeded_kb
) -> None:
    await _seed(db_session)
    original = await demo_account_service.get_demo_user(db_session)
    assert original is not None
    original_hash = original.password_hash

    second = await _seed(db_session)
    assert second["created_user"] is False
    assert second["created_reports"] == 0
    assert second["password_matches"] is True

    users = (
        (await db_session.execute(select(User).where(User.email == original.email)))
        .scalars()
        .all()
    )
    assert len(users) == 1, "the seed must never create duplicate demo users"

    refreshed = await demo_account_service.get_demo_user(db_session)
    assert refreshed is not None
    assert refreshed.password_hash == original_hash, "password must not be overwritten"

    reports = (
        (await db_session.execute(select(Report).where(Report.user_id == refreshed.id)))
        .scalars()
        .all()
    )
    assert len(reports) == len(DEMO_REPORTS)

    analyses = (await db_session.execute(select(func.count(Analysis.id)))).scalar_one()
    assert analyses == len(DEMO_REPORTS), "analyses must not be duplicated by re-seeding"


async def test_public_credentials_endpoint_matches_settings_and_login_works(
    client: AsyncClient, db_session, seeded_kb
) -> None:
    await _seed(db_session)
    settings = get_settings()

    info_response = await client.get("/api/demo/account")
    assert info_response.status_code == 200
    info = info_response.json()
    assert info["enabled"] is True
    assert info["email"] == settings.demo_account_email
    assert info["password"] == settings.demo_account_password

    # The credentials still authenticate through the normal login request.
    login = await client.post(
        "/api/auth/login",
        json={"email": info["email"], "password": info["password"]},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["user"]["role"] == "demo"

    # ...and the normal refresh-cookie session pipeline works for demo too.
    refresh = await client.post("/api/auth/refresh")
    assert refresh.status_code == 200


async def test_demo_account_has_no_admin_access(
    client: AsyncClient, db_session, seeded_kb
) -> None:
    await _seed(db_session)
    headers = await _demo_login(client)

    response = await client.get("/api/admin/stats", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_demo_account_cannot_upload_change_password_or_delete(
    client: AsyncClient, db_session, seeded_kb
) -> None:
    await _seed(db_session)
    headers = await _demo_login(client)

    upload = await client.post(
        "/api/reports/upload",
        files={"file": ("mine.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
        headers=headers,
    )
    assert upload.status_code == 403
    assert "demo" in upload.json()["error"]["message"].lower()

    change = await client.post(
        "/api/auth/change-password",
        json={"current_password": "irrelevant", "new_password": "Str0ngPass1"},
        headers=headers,
    )
    assert change.status_code == 403

    delete = await client.request(
        "DELETE",
        "/api/auth/account",
        json={"password": "irrelevant"},
        headers=headers,
    )
    assert delete.status_code == 403

    sessions = await client.get("/api/auth/sessions", headers=headers)
    assert sessions.status_code == 403


async def test_demo_account_never_issues_password_reset_tokens(
    client: AsyncClient, db_session, seeded_kb
) -> None:
    await _seed(db_session)
    response = await client.post(
        "/api/auth/forgot-password",
        json={"email": get_settings().demo_account_email},
    )
    assert response.status_code == 200
    # AUTH_DEV_RETURN_RESET_TOKEN=true in tests: a normal user would get the
    # token here; the demo account must never issue one.
    assert response.json().get("dev_reset_token") is None


async def test_demo_reports_are_listed_with_their_analyses(
    client: AsyncClient, db_session, seeded_kb
) -> None:
    await _seed(db_session)
    headers = await _demo_login(client)

    listing = (await client.get("/api/reports", headers=headers)).json()
    assert listing["total"] == len(DEMO_REPORTS)
    assert all(item["is_demo"] for item in listing["items"])

    report_id = listing["items"][0]["id"]
    detail = (await client.get(f"/api/reports/{report_id}", headers=headers)).json()
    assert detail["is_demo"] is True
    assert len(detail["values"]) > 0

    analysis = await client.get(f"/api/reports/{report_id}/analysis", headers=headers)
    assert analysis.status_code == 200
    assert analysis.json()["status"] == "completed"


async def test_disabled_demo_account_hides_credentials_and_skips_seed(
    client: AsyncClient, db_session, monkeypatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "demo_account_enabled", False)

    response = await client.get("/api/demo/account")
    assert response.status_code == 404

    result = await demo_account_service.seed_demo_account(db_session)
    assert result["enabled"] is False
    assert result["created_user"] is False
    assert await demo_account_service.get_demo_user(db_session) is None
