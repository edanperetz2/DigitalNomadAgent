from app.core.module_names import ALL_MODULES


def test_agent_info_status_and_top_level_shape(client):
    response = client.get("/api/agent_info")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"description", "purpose", "prompt_template", "prompt_examples"}
    assert set(data["prompt_template"].keys()) == {"template"}
    assert "under 300 seconds" in data["description"]


def test_agent_info_has_at_least_three_examples_for_each_purpose(client):
    data = client.get("/api/agent_info").json()
    examples = data["prompt_examples"]
    assert len(examples) >= 3

    combined_prompts = " ".join(e["prompt"].lower() for e in examples)
    assert "remote" in combined_prompts
    assert ("study" in combined_prompts) or ("exchange" in combined_prompts)
    assert "beach" in combined_prompts or "vacation" in combined_prompts


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
