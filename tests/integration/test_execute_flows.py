import sqlite3

from app.core.module_names import AGENTIC_RESEARCH, RECOMMENDATION_GENERATOR, REQUEST_INTERPRETER


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
    assert RECOMMENDATION_GENERATOR in modules_called
    assert len(data["steps"]) <= 4


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


def test_ambiguous_prompt_returns_clarification(client):
    response = client.post("/api/execute", json={"prompt": "Surprise me."})
    data = response.json()
    assert data["status"] == "ok"
    assert data["response"]
    assert "?" in data["response"]
    # Clarification is resolved by the Request Interpreter alone -- no further
    # research modules should have run.
    assert len(data["steps"]) == 1
    assert data["steps"][0]["module"] == REQUEST_INTERPRETER


def test_study_prompt_without_field_returns_clarification(client):
    response = client.post(
        "/api/execute", json={"prompt": "I want to study abroad for a semester somewhere affordable."}
    )
    data = response.json()
    assert data["status"] == "ok"
    assert "field" in data["response"].lower()


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
