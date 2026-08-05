
REQUIRED_KEYS = {"status", "error", "response", "steps"}


def _assert_strict_error_envelope(data: dict):
    assert set(data.keys()) == REQUIRED_KEYS
    assert data["status"] == "error"
    assert data["response"] is None
    assert isinstance(data["error"], str) and data["error"]
    assert data["steps"] == []
    assert "detail" not in data


def test_missing_prompt_field(client):
    response = client.post("/api/execute", json={})
    data = response.json()
    _assert_strict_error_envelope(data)


def test_blank_prompt(client):
    response = client.post("/api/execute", json={"prompt": ""})
    data = response.json()
    _assert_strict_error_envelope(data)


def test_whitespace_only_prompt(client):
    response = client.post("/api/execute", json={"prompt": "   \n\t  "})
    data = response.json()
    _assert_strict_error_envelope(data)


def test_overlong_prompt(client):
    response = client.post("/api/execute", json={"prompt": "x" * 5000})
    data = response.json()
    _assert_strict_error_envelope(data)


def test_extra_field_rejected(client):
    response = client.post("/api/execute", json={"prompt": "hello", "extra": "field"})
    data = response.json()
    _assert_strict_error_envelope(data)


def test_wrong_type_prompt_rejected(client):
    response = client.post("/api/execute", json={"prompt": 12345})
    data = response.json()
    _assert_strict_error_envelope(data)


def test_invalid_json_body(client):
    response = client.post(
        "/api/execute", content=b"{not valid json", headers={"Content-Type": "application/json"}
    )
    data = response.json()
    _assert_strict_error_envelope(data)


def test_successful_response_has_exact_four_fields(client):
    response = client.post("/api/execute", json={"prompt": "Find a quiet beach destination for two weeks in October."})
    data = response.json()
    assert set(data.keys()) == REQUIRED_KEYS
    assert data["status"] in ("ok", "error")
    if data["status"] == "ok":
        assert data["error"] is None
        assert isinstance(data["response"], str)
    for step in data["steps"]:
        assert set(step.keys()) == {"module", "prompt", "response"}


def test_execution_deadline_returns_best_effort_recommendation(client, monkeypatch):
    async def slow_complete(*args, **kwargs):
        import asyncio

        await asyncio.sleep(10)

    orchestrator = client.app.state.orchestrator
    orchestrator._execution_timeout_seconds = 0.02
    monkeypatch.setattr(orchestrator._llm, "complete", slow_complete)

    response = client.post(
        "/api/execute",
        json={"prompt": "Find a quiet beach destination for two weeks in October."},
    )
    data = response.json()

    assert set(data) == REQUIRED_KEYS
    assert data["status"] == "ok"
    assert data["error"] is None
    assert "## Best matches" in data["response"]
    assert "incomplete research was cancelled" in data["response"]
    assert data["steps"] == []


def test_llm_timeout_or_failure_uses_deterministic_pipeline_fallbacks(client, monkeypatch):
    async def unavailable_complete(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    orchestrator = client.app.state.orchestrator
    monkeypatch.setattr(orchestrator._llm, "complete", unavailable_complete)

    response = client.post(
        "/api/execute",
        json={"prompt": "Find a quiet beach destination for two weeks in October."},
    )
    data = response.json()

    assert data["status"] == "ok"
    assert data["error"] is None
    assert "## Best matches" in data["response"]
    # D32: these are appended after the generator runs, not routed through it.
    # As profile.assumptions they were the model's to rewrite, and it dropped
    # them -- P10's interpreter failed outright and the answer read like an
    # ordinary complete run.
    assert "**Reduced-capability run:**" in data["response"]
    assert "simpler rule-based reader" in data["response"]
    assert "fixed seed set" in data["response"]
