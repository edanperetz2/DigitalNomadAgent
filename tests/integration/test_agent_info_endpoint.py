from app.core.module_names import ALL_MODULES, LLM_CALLING_MODULES


def test_agent_info_status_and_top_level_shape(client):
    response = client.get("/api/agent_info")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"description", "purpose", "prompt_template", "prompt_examples"}
    assert set(data["prompt_template"].keys()) == {"template"}
    assert "under 300 seconds" in data["description"]


def test_agent_info_shows_at_least_three_distinct_requests(client):
    """Three or more examples, each a different request with a real answer.

    This used to match hard-coded words ("study", "beach") against the three
    mock-built examples. The endpoint now serves answers captured from live
    runs, so the assertion is on substance instead: distinct prompts, and an
    answer long enough to be an actual recommendation rather than a stub.
    """
    examples = client.get("/api/agent_info").json()["prompt_examples"]
    assert len(examples) >= 3

    prompts = [e["prompt"].strip() for e in examples]
    assert all(prompts), "every example must carry the prompt it answers"
    assert len(set(prompts)) == len(prompts), "examples must be different requests"
    for example in examples:
        assert len(example["full_response"]) > 500


def test_agent_info_examples_exercise_every_llm_module(client):
    """Between them the examples must show all four traced modules at work."""
    examples = client.get("/api/agent_info").json()["prompt_examples"]
    seen = {step["module"] for example in examples for step in example["steps"]}
    assert set(LLM_CALLING_MODULES) <= seen


def test_agent_info_examples_have_exact_step_shape(client):
    data = client.get("/api/agent_info").json()
    for example in data["prompt_examples"]:
        assert set(example.keys()) == {"prompt", "full_response", "steps"}
        assert len(example["steps"]) > 0
        for step in example["steps"]:
            assert set(step.keys()) == {"module", "prompt", "response"}
            assert step["module"] in ALL_MODULES


def test_agent_info_makes_no_llm_calls(monkeypatch, client):
    from app.llm.mock import MockLLMClient

    async def _boom(*args, **kwargs):
        raise AssertionError("agent_info must not call the LLM")

    monkeypatch.setattr(MockLLMClient, "complete", _boom)
    response = client.get("/api/agent_info")
    assert response.status_code == 200
