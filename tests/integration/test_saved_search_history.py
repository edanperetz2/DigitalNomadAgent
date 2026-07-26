import json
import re


def _saved_sessions(html: str) -> list[dict]:
    match = re.search(
        r'<script id="saved-search-sessions" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_history_starts_empty(client):
    sessions = _saved_sessions(client.get("/").text)
    assert sessions == []


def test_successful_search_is_persisted_and_reloaded(client):
    prompt_a = "Find a walkable city in Europe for remote work under 1800 EUR."
    response_a = client.post("/api/execute", json={"prompt": prompt_a})
    data_a = response_a.json()

    assert set(data_a) == {"status", "error", "response", "steps"}
    assert data_a["status"] == "ok"
    session_id_a = response_a.headers["X-DigitalNomadAgent-Session-Id"]

    refreshed_sessions = _saved_sessions(client.get("/").text)
    assert len(refreshed_sessions) == 1
    assert refreshed_sessions[0]["id"] == session_id_a
    assert refreshed_sessions[0]["original_request"] == prompt_a
    assert refreshed_sessions[0]["response"] == data_a["response"]
    assert refreshed_sessions[0]["result"]["steps"] == data_a["steps"]


def test_multiple_searches_survive_refresh_without_duplicates(client):
    prompt_a = "Find a walkable city in Europe for remote work under 1800 EUR."
    prompt_b = "Find a warm coastal city with coworking for one month."

    response_a = client.post("/api/execute", json={"prompt": prompt_a})
    session_id_a = response_a.headers["X-DigitalNomadAgent-Session-Id"]
    response_b = client.post("/api/execute", json={"prompt": prompt_b})
    session_id_b = response_b.headers["X-DigitalNomadAgent-Session-Id"]

    sessions = _saved_sessions(client.get("/").text)
    assert [session["id"] for session in sessions] == [session_id_b, session_id_a]

    repeat_response_a = client.post("/api/execute", json={"prompt": prompt_a})
    assert repeat_response_a.headers["X-DigitalNomadAgent-Session-Id"] == session_id_a

    sessions_after_repeat = _saved_sessions(client.get("/").text)
    assert len(sessions_after_repeat) == 2
    assert [session["id"] for session in sessions_after_repeat] == [session_id_a, session_id_b]
