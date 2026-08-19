"""A successful /api/execute run must still return the real answer even if
saving it to history fails -- history is a purely additive convenience
feature and must never turn a valid, already-computed response into a
client-visible 500 (found during the final pre-deadline audit)."""

import pytest

from app.evidence.saved_searches import SavedSearchStore


@pytest.mark.asyncio
async def test_execute_returns_the_real_answer_even_if_save_session_fails(client, monkeypatch):
    async def failing_save_session(self, *, prompt, result_data):
        raise RuntimeError("simulated database write failure")

    monkeypatch.setattr(SavedSearchStore, "save_session", failing_save_session)

    response = client.post(
        "/api/execute",
        json={"prompt": "I want to spend three months in Europe working remotely, car-free, under EUR 1800 a month."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["error"] is None
    assert body["response"]
    assert len(body["steps"]) > 0
    # The history-failure path must not set the session header either.
    assert "X-DigitalNomadAgent-Session-Id" not in response.headers
