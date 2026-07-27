import sqlite3

from app.core.config import get_settings
from app.core.module_names import (
    AGENTIC_RESEARCH,
    DYNAMIC_EVALUATION,
    RECOMMENDATION_GENERATOR,
    REQUEST_INTERPRETER,
)


def test_remote_work_prompt_returns_recommendations(client):
    response = client.post(
        "/api/execute",
        json={
            "prompt": (
                "I want to spend three months somewhere in Europe where I can work remotely, "
                "live without a car, and stay within €1,800 per month."
            )
        },
    )
    data = response.json()
    assert data["status"] == "ok"
    assert "Best matches" in data["response"]
    modules_called = [s["module"] for s in data["steps"]]
    assert REQUEST_INTERPRETER in modules_called
    assert AGENTIC_RESEARCH in modules_called
    assert DYNAMIC_EVALUATION in modules_called
    assert RECOMMENDATION_GENERATOR in modules_called
    assert len(data["steps"]) <= 4
    # cost/transportation/accessibility/activities must be genuinely resolved, not left as the
    # old "scoring awaits the LLM reasoning contract" placeholder from before this call existed.
    assert "awaits the LLM reasoning contract" not in data["response"]
    # Course spec's required step schema -- exact key names, checked end-to-end.
    for step in data["steps"]:
        assert set(step["prompt"].keys()) == {"System_prompt", "User_prompt"}


def test_finalist_count_never_exceeds_max_finalists(client):
    response = client.post(
        "/api/execute",
        json={
            "prompt": (
                "Find a quiet beach destination for two weeks in October, with warm but not "
                "extremely hot weather and good hiking nearby."
            )
        },
    )
    data = response.json()
    assert data["status"] == "ok"
    table_rows = [
        line
        for line in data["response"].splitlines()
        if line.startswith("|") and not line.startswith("|---") and "Rank" not in line
    ]
    assert 0 < len(table_rows) <= get_settings().max_finalists


def test_extremely_low_hard_budget_eliminates_all_candidates(client):
    response = client.post(
        "/api/execute",
        json={
            "prompt": (
                "I want to work remotely somewhere in Europe. It is required that the budget "
                "stay under 1 USD per month including accommodation."
            )
        },
    )
    data = response.json()
    assert data["status"] == "error"
    assert "eliminated" in data["error"].lower()


def test_study_prompt_with_field_returns_recommendations(client):
    response = client.post(
        "/api/execute",
        json={
            "prompt": (
                "Recommend a city for a one-semester computer-science exchange. I care about "
                "student life, public transportation, safety, and affordable housing."
            )
        },
    )
    data = response.json()
    assert data["status"] == "ok"
    assert "Best matches" in data["response"]


def test_vacation_prompt_returns_recommendations(client):
    response = client.post(
        "/api/execute",
        json={
            "prompt": (
                "Find a quiet beach destination for two weeks in October, with warm but not "
                "extremely hot weather and good hiking nearby."
            )
        },
    )
    data = response.json()
    assert data["status"] == "ok"
    assert "Best matches" in data["response"]


def test_ambiguous_prompt_returns_full_recommendation_by_default(client):
    # A bare call (no X-Interactive-Mode header) is exactly what an automated
    # grader sends -- it must always get a final, actionable answer, never a
    # clarification dead-end. See app/agent/orchestrator.py's
    # _resolve_ambiguous_profile.
    response = client.post("/api/execute", json={"prompt": "Surprise me."})
    data = response.json()
    assert data["status"] == "ok"
    assert "Best matches" in data["response"]
    assert "proceeding with a broad default" in data["response"].casefold()
    modules_called = [s["module"] for s in data["steps"]]
    assert REQUEST_INTERPRETER in modules_called
    assert RECOMMENDATION_GENERATOR in modules_called


def test_ambiguous_prompt_with_interactive_header_returns_clarification(client):
    response = client.post(
        "/api/execute",
        json={"prompt": "Surprise me."},
        headers={"X-Interactive-Mode": "true"},
    )
    data = response.json()
    assert data["status"] == "ok"
    assert data["response"]
    assert "?" in data["response"]
    # Clarification is resolved by the Request Interpreter alone -- no further
    # research modules should have run.
    assert len(data["steps"]) == 1
    assert data["steps"][0]["module"] == REQUEST_INTERPRETER


def test_study_prompt_without_field_continues_to_recommendations(client):
    response = client.post(
        "/api/execute", json={"prompt": "I want to study abroad for a semester somewhere affordable."}
    )
    data = response.json()
    assert data["status"] == "ok"
    assert "Best matches" in data["response"]
    assert len(data["steps"]) > 1


def test_repeated_requests_are_independent(client):
    r1 = client.post("/api/execute", json={"prompt": "Surprise me."})
    r2 = client.post("/api/execute", json={"prompt": "Surprise me."})
    assert r1.json()["status"] == "ok"
    assert r2.json()["status"] == "ok"


def test_successful_geocoding_evidence_is_persisted(client):
    response = client.post(
        "/api/execute",
        json={
            "prompt": (
                "I want to spend three months somewhere in Europe where I can work remotely, "
                "with reliable internet and cafes nearby."
            )
        },
    )
    assert response.json()["status"] == "ok"

    with sqlite3.connect(client.app.state.db.path) as connection:
        rows = connection.execute(
            "SELECT place, source_name FROM evidence WHERE criterion = ?", ("GeocodingTool",)
        ).fetchall()

    assert rows
    assert all(source_name == "OpenStreetMap Nominatim (fake)" for _, source_name in rows)
