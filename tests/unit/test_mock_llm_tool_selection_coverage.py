"""Closes a real coverage gap found during the final pre-deadline audit:
MockLLMClient's Agentic Research branch previously never populated
`relevant_tools`, so resolve_tool_selection()'s primary (LLM-authoritative)
branch -- the actual merged feature -- was never exercised by any of the
offline, MOCK_LLM=true tests, only by live paid calls. These tests prove the
mock now feeds that branch real data, and that LanguageTool (the one tool
missing a fake) is actually reachable end-to-end under MOCK_LLM=true.
"""

import json

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.core.module_names import AGENTIC_RESEARCH
from app.llm.mock import MockLLMClient
from app.tools.fakes import build_fake_tool_registry_dict


@pytest.mark.asyncio
async def test_mock_agentic_research_populates_relevant_tools():
    profile = PlaceRequestProfile(purpose="remote_work", relevant_criteria=["safety"])
    client = MockLLMClient()

    raw = await client.complete(
        [
            {"role": "system", "content": "irrelevant"},
            {"role": "user", "content": json.dumps({"profile": profile.model_dump(mode="json")})},
        ],
        max_output_tokens=1000,
        metadata={"module": AGENTIC_RESEARCH},
    )

    payload = json.loads(raw.text)
    assert "relevant_tools" in payload
    assert "AmenitiesTool" in payload["relevant_tools"]
    assert "SafetyTool" in payload["relevant_tools"]


@pytest.mark.asyncio
async def test_mock_agentic_research_survives_a_malformed_profile():
    client = MockLLMClient()

    raw = await client.complete(
        [{"role": "user", "content": json.dumps({"profile": {}})}],
        max_output_tokens=1000,
        metadata={"module": AGENTIC_RESEARCH},
    )

    payload = json.loads(raw.text)
    assert payload["relevant_tools"] == []


def test_fake_tool_registry_includes_language_tool():
    registry = build_fake_tool_registry_dict()
    assert "LanguageTool" in registry


@pytest.mark.asyncio
async def test_fake_language_tool_returns_usable_evidence():
    registry = build_fake_tool_registry_dict()
    tool = registry["LanguageTool"]

    result = await tool.run(
        CandidatePlace(place_name="Lisbon", country="Portugal", reason_for_inclusion="test"),
        PlaceRequestProfile(purpose="remote_work", preferred_languages=["English"]),
    )

    assert result.error is None
    assert result.normalized_data["english_reach"]
    assert "English" in result.normalized_data["matched_languages"]
