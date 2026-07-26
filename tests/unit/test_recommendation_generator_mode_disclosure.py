import json

import pytest

from app.agent.models import PlaceRequestProfile, ValidationResult
from app.agent.recommendation_generator import generate_recommendation, render_recommendation_fallback
from app.llm.base import BaseLLMClient, LLMRawResponse
from app.llm.mock import MockLLMClient


class _FakeBudget:
    async def check_before_call(self, request_id, module, est_input, est_output):
        return None

    async def record_call(self, request_id, module, model, input_tokens, output_tokens, cost, success):
        return None


class _RealLikeClient(BaseLLMClient):
    async def complete(self, messages, *, max_output_tokens, metadata=None):
        return LLMRawResponse(
            text=json.dumps({"markdown": "## Best matches\n\nSomewhere"}),
            input_tokens=10,
            output_tokens=10,
            provider_cost_usd=0.01,
        )


def _profile_and_validation():
    profile = PlaceRequestProfile(purpose="vacation")
    validation = ValidationResult(approved=True, evidence_coverage=1.0)
    return profile, validation


def test_fallback_rendering_discloses_deterministic_template():
    profile, validation = _profile_and_validation()
    markdown = render_recommendation_fallback(profile, [], validation, [])
    assert "Generated using: a deterministic fallback template" in markdown


@pytest.mark.asyncio
async def test_generate_recommendation_discloses_mock_mode():
    profile, validation = _profile_and_validation()
    markdown = await generate_recommendation(
        profile,
        [],
        validation,
        [],
        client=MockLLMClient(),
        budget=_FakeBudget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
    )
    assert "Generated using: mock deterministic mode" in markdown


@pytest.mark.asyncio
async def test_generate_recommendation_discloses_real_llm_mode():
    profile, validation = _profile_and_validation()
    markdown = await generate_recommendation(
        profile,
        [],
        validation,
        [],
        client=_RealLikeClient(),
        budget=_FakeBudget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
    )
    assert "Generated using: a real LLM provider" in markdown
