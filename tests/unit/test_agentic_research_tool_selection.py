"""Verifies the LLM-driven tool-selection path added to Agentic Research:
generate_candidates() parses and sanitizes `relevant_tools`, and
resolve_tool_selection() prefers it over the deterministic rules only when
it produced something usable.
"""

import json

import pytest

from app.agent.agentic_research import generate_candidates, resolve_tool_selection, select_tools
from app.agent.models import PlaceRequestProfile
from app.llm.base import LLMRawResponse


class _Budget:
    async def check_before_call(self, *args, **kwargs):
        return None

    async def record_call(self, *args, **kwargs):
        return None


class _ScriptedClient:
    def __init__(self, payload: dict):
        self.payload = payload

    async def complete(self, messages, *, max_output_tokens, metadata=None):
        return LLMRawResponse(
            text=json.dumps(self.payload),
            input_tokens=10,
            output_tokens=50,
        )


async def _generate(payload: dict, profile: PlaceRequestProfile | None = None):
    return await generate_candidates(
        profile or PlaceRequestProfile(purpose="remote_work"),
        client=_ScriptedClient(payload),
        budget=_Budget(),
        request_id="test",
        execution_trace=[],
        max_output_tokens=1000,
    )


@pytest.mark.asyncio
async def test_relevant_tools_are_parsed_from_the_llm_response():
    payload = {
        "candidates": [{"place_name": "Lisbon", "country": "Portugal", "reason_for_inclusion": "test"}],
        "relevant_tools": ["WeatherTool", "SafetyTool"],
    }

    _, llm_tools = await _generate(payload)

    assert llm_tools == {"WeatherTool", "SafetyTool"}


@pytest.mark.asyncio
async def test_hallucinated_tool_names_are_dropped():
    payload = {
        "candidates": [{"place_name": "Lisbon", "country": "Portugal", "reason_for_inclusion": "test"}],
        "relevant_tools": ["WeatherTool", "NotARealTool", "AlsoFake"],
    }

    _, llm_tools = await _generate(payload)

    assert llm_tools == {"WeatherTool"}


@pytest.mark.asyncio
async def test_geocoding_and_budget_tools_are_never_expected_from_the_llm():
    """The prompt tells the model not to list these -- if it does anyway,
    sanitization still lets them through since they are real tool names; this
    only confirms an absent relevant_tools list sanitizes to empty, not an
    error."""
    payload = {
        "candidates": [{"place_name": "Lisbon", "country": "Portugal", "reason_for_inclusion": "test"}],
    }

    _, llm_tools = await _generate(payload)

    assert llm_tools == set()


def test_resolve_tool_selection_prefers_a_nonempty_llm_set():
    profile = PlaceRequestProfile(purpose="remote_work")

    resolved = resolve_tool_selection(profile, {"SafetyTool"})

    assert resolved == {"SafetyTool", "GeocodingTool"}


def test_resolve_tool_selection_falls_back_to_deterministic_rules_when_empty():
    profile = PlaceRequestProfile(purpose="remote_work")

    resolved = resolve_tool_selection(profile, set())

    assert resolved == select_tools(profile)
