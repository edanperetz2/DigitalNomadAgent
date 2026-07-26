import json

import pytest

from app.core.module_names import AGENTIC_RESEARCH, RECOMMENDATION_GENERATOR, REQUEST_INTERPRETER
from app.llm.mock import MockLLMClient


@pytest.mark.asyncio
async def test_request_interpreter_module_returns_valid_json():
    client = MockLLMClient()
    response = await client.complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "I want to work remotely in Lisbon."}],
        max_output_tokens=500,
        metadata={"module": REQUEST_INTERPRETER},
    )
    parsed = json.loads(response.text)
    assert parsed["purpose"] == "remote_work"


@pytest.mark.asyncio
async def test_agentic_research_module_returns_lean_bulk_candidates():
    client = MockLLMClient()
    payload = json.dumps({"profile": {"purpose": "study"}})
    response = await client.complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": payload}],
        max_output_tokens=500,
        metadata={"module": AGENTIC_RESEARCH},
    )
    parsed = json.loads(response.text)
    assert len(parsed["candidates"]) >= 25
    assert all(
        set(c) == {"place_name", "country", "reason_for_inclusion"} for c in parsed["candidates"]
    )


@pytest.mark.asyncio
async def test_recommendation_generator_module_returns_markdown():
    client = MockLLMClient()
    candidate = {
        "place": "Valencia",
        "country": "Spain",
        "total_score": 0.8,
        "confidence_score": 0.7,
        "advantages": ["nice"],
        "drawbacks": ["crowded"],
    }
    payload = json.dumps(
        {
            "purpose_summary": "a vacation request",
            "assumptions": [],
            "validation_issues": [],
            "candidates": [candidate],
            "sources": [],
        }
    )
    response = await client.complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": payload}],
        max_output_tokens=800,
        metadata={"module": RECOMMENDATION_GENERATOR},
    )
    parsed = json.loads(response.text)
    assert "Best matches" in parsed["markdown"]
    assert "Valencia" in parsed["markdown"]


@pytest.mark.asyncio
async def test_mock_client_is_deterministic():
    client = MockLLMClient()
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "vacation to a beach"}]
    r1 = await client.complete(messages, max_output_tokens=500, metadata={"module": REQUEST_INTERPRETER})
    r2 = await client.complete(messages, max_output_tokens=500, metadata={"module": REQUEST_INTERPRETER})
    assert r1.text == r2.text


@pytest.mark.asyncio
async def test_mock_client_never_reports_nonzero_cost():
    client = MockLLMClient()
    response = await client.complete(
        [{"role": "user", "content": "test"}], max_output_tokens=100, metadata={"module": REQUEST_INTERPRETER}
    )
    assert response.provider_cost_usd == 0.0
