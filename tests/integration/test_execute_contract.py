
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
