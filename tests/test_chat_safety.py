"""Chat assistant: educational answers, guardrails and session handling."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import upload_and_process
from tests.fixtures.sample_reports import CBC_TEXT


async def test_chat_answers_educational_questions(client: AsyncClient, auth_headers) -> None:
    response = await client.post(
        "/api/chat/messages",
        json={"message": "What does hemoglobin measure?", "language": "en"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"]
    assert body["reply"]["safety_blocked"] is False
    assert body["reply"]["content"]
    assert body["disclaimer"]


async def test_chat_blocks_prescription_requests(client: AsyncClient, auth_headers) -> None:
    response = await client.post(
        "/api/chat/messages",
        json={
            "message": "Should I start taking iron tablets? What dosage should I take daily?",
            "language": "en",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert reply["safety_blocked"] is True
    assert "healthcare professional" in reply["content"].lower()


async def test_chat_blocks_diagnosis_requests(client: AsyncClient, auth_headers) -> None:
    response = await client.post(
        "/api/chat/messages",
        json={"message": "Do I have anemia? Tell me my diagnosis.", "language": "en"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["reply"]["safety_blocked"] is True


async def test_chat_uses_report_context_when_provided(
    client: AsyncClient, auth_headers, seeded_kb
) -> None:
    report_id = await upload_and_process(client, auth_headers, CBC_TEXT, "cbc.pdf")

    first = await client.post(
        "/api/chat/messages",
        json={"message": "What does my hemoglobin result mean?", "report_id": report_id},
        headers=auth_headers,
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = await client.post(
        "/api/chat/messages",
        json={"message": "And what about my white cells?", "session_id": session_id},
        headers=auth_headers,
    )
    assert second.status_code == 200
    assert second.json()["session_id"] == session_id

    detail = await client.get(f"/api/chat/sessions/{session_id}", headers=auth_headers)
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert len(messages) == 4  # two questions, two replies
    assert messages[0]["role"] == "user"

    sessions = (await client.get("/api/chat/sessions", headers=auth_headers)).json()
    assert any(item["id"] == session_id for item in sessions)


async def test_chat_sessions_are_private_and_deletable(
    client: AsyncClient, auth_headers
) -> None:
    created = await client.post(
        "/api/chat/messages", json={"message": "What is a CBC test?"}, headers=auth_headers
    )
    session_id = created.json()["session_id"]

    other = await client.post(
        "/api/auth/register",
        json={"name": "Other", "email": "other@example.com", "password": "Passw0rd123"},
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    foreign = await client.get(f"/api/chat/sessions/{session_id}", headers=other_headers)
    assert foreign.status_code == 404

    deleted = await client.delete(f"/api/chat/sessions/{session_id}", headers=auth_headers)
    assert deleted.status_code == 200

    gone = await client.get(f"/api/chat/sessions/{session_id}", headers=auth_headers)
    assert gone.status_code == 404
