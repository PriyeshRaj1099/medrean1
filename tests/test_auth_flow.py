"""Authentication: registration, login, refresh, reset, sessions."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import TEST_USER


async def test_register_login_and_me(client: AsyncClient) -> None:
    register = await client.post("/api/auth/register", json=TEST_USER)
    assert register.status_code == 201
    body = register.json()
    assert body["user"]["email"] == TEST_USER["email"]
    assert body["user"]["role"] == "user"
    assert body["access_token"]

    me = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == TEST_USER["email"]

    login = await client.post(
        "/api/auth/login",
        json={"email": TEST_USER["email"], "password": TEST_USER["password"]},
    )
    assert login.status_code == 200
    assert login.json()["user"]["email"] == TEST_USER["email"]


async def test_register_rejects_duplicate_email(client: AsyncClient) -> None:
    first = await client.post("/api/auth/register", json=TEST_USER)
    assert first.status_code == 201
    duplicate = await client.post("/api/auth/register", json=TEST_USER)
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["code"] == "email_taken"


async def test_register_rejects_weak_password(client: AsyncClient) -> None:
    weak = dict(TEST_USER, email="weak@example.com", password="password")
    response = await client.post("/api/auth/register", json=weak)
    assert response.status_code == 422


async def test_login_with_wrong_password_is_rejected(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=TEST_USER)
    response = await client.post(
        "/api/auth/login",
        json={"email": TEST_USER["email"], "password": "WrongPass123"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/reports")
    assert response.status_code == 401


async def test_refresh_rotates_the_access_token(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=TEST_USER)
    # The refresh cookie was set by register; exchange it for a new token.
    refresh = await client.post("/api/auth/refresh")
    assert refresh.status_code == 200
    assert refresh.json()["access_token"]

    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 200
    # After logout the refresh cookie is revoked.
    after = await client.post("/api/auth/refresh")
    assert after.status_code == 401


async def test_forgot_password_full_reset_cycle(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=TEST_USER)

    forgot = await client.post(
        "/api/auth/forgot-password", json={"email": TEST_USER["email"]}
    )
    assert forgot.status_code == 200
    token = forgot.json().get("dev_reset_token")
    assert token, "dev reset token expected when AUTH_DEV_RETURN_RESET_TOKEN=true"

    reset = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": "N3wPassw0rd"},
    )
    assert reset.status_code == 200

    old_login = await client.post(
        "/api/auth/login",
        json={"email": TEST_USER["email"], "password": TEST_USER["password"]},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/auth/login",
        json={"email": TEST_USER["email"], "password": "N3wPassw0rd"},
    )
    assert new_login.status_code == 200

    # The same reset token cannot be reused.
    replay = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": "Y3tAn0ther1"},
    )
    assert replay.status_code == 422


async def test_forgot_password_does_not_reveal_unknown_emails(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/forgot-password", json={"email": "nobody@example.com"}
    )
    assert response.status_code == 200
    assert response.json().get("dev_reset_token") is None


async def test_profile_update_and_change_password(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    update = await client.patch(
        "/api/auth/me", json={"name": "Renamed User"}, headers=auth_headers
    )
    assert update.status_code == 200
    assert update.json()["name"] == "Renamed User"

    wrong = await client.post(
        "/api/auth/change-password",
        json={"current_password": "not-the-password1", "new_password": "Wh4tEver99"},
        headers=auth_headers,
    )
    assert wrong.status_code in (401, 422)

    ok = await client.post(
        "/api/auth/change-password",
        json={"current_password": TEST_USER["password"], "new_password": "Wh4tEver99"},
        headers=auth_headers,
    )
    assert ok.status_code == 200
