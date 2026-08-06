"""D56: the reader is told when the comparison collapsed, and the model cannot lose it.

P06 proposed 30 places, researched 8, eliminated 7 of those against its hard
constraints and printed a one-row "Best matches" table without ever saying so.
The wording existed -- it travelled as a caveat for the generator to paraphrase,
and the generator dropped it. These tests pin the disclosure to the deterministic
side of the LLM call, where D32 established that anything the reader must see
belongs.
"""

import json

import pytest

from app.agent.models import CandidateEvaluation, PlaceRequestProfile, ValidationResult
from app.agent.recommendation_generator import (
    generate_recommendation,
    render_recommendation_fallback,
)
from app.llm.base import BaseLLMClient, LLMRawResponse


class _FakeBudget:
    async def check_before_call(self, request_id, module, est_input, est_output):
        return None

    async def record_call(self, request_id, module, model, input_tokens, output_tokens, cost, success):
        return None


class _ForgetfulClient(BaseLLMClient):
    """Returns prose that mentions none of the caveats it was handed.

    This is not a strawman: it is what the real provider did on 2026-08-06.
    """

    async def complete(self, messages, *, max_output_tokens, metadata=None):
        return LLMRawResponse(
            text=json.dumps({"markdown": "## Best matches\n\n| Rank | Place |\n|---|---|\n| 1 | Valletta |"}),
            input_tokens=10,
            output_tokens=10,
            provider_cost_usd=0.01,
        )


def _evaluation(place: str, *, eliminated: bool = False) -> CandidateEvaluation:
    return CandidateEvaluation(
        place=place,
        country="Country",
        total_score=0.5,
        criterion_scores={},
        advantages=[],
        drawbacks=["something"],
        confidence_score=0.5,
        eliminated=eliminated,
    )


def _p06_shape() -> list[CandidateEvaluation]:
    """8 researched, 7 eliminated -- P06 exactly."""
    return [_evaluation("Valletta")] + [
        _evaluation(name, eliminated=True)
        for name in ("Limassol", "Paphos", "Larnaca", "Nicosia", "Malaga", "Benidorm", "Cartagena")
    ]


def _profile_and_validation():
    return PlaceRequestProfile(purpose="vacation"), ValidationResult(approved=True, evidence_coverage=1.0)


@pytest.mark.asyncio
async def test_a_one_row_answer_says_so_even_when_the_model_ignores_it():
    profile, validation = _profile_and_validation()

    markdown = await generate_recommendation(
        profile,
        _p06_shape(),
        validation,
        [],
        client=_ForgetfulClient(),
        budget=_FakeBudget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
        candidates_proposed=30,
    )

    assert "much narrower comparison than usual" in markdown
    assert "30 places considered" in markdown
    assert "8 could be researched" in markdown
    assert "1 of those met the requirements" in markdown
    # A single place is not a comparison, and the reader is told what to do.
    assert "not really a comparison" in markdown


def test_the_fallback_template_discloses_it_too():
    profile, validation = _profile_and_validation()
    markdown = render_recommendation_fallback(
        profile, _p06_shape(), validation, [], candidates_proposed=30
    )
    assert "much narrower comparison than usual" in markdown


@pytest.mark.asyncio
async def test_a_healthy_field_is_not_told_it_collapsed():
    """Silent unless it fires: P02 delivered seven and was told its list was short."""
    profile, validation = _profile_and_validation()
    evaluations = [_evaluation(str(i)) for i in range(7)] + [_evaluation("gone", eliminated=True)]

    markdown = await generate_recommendation(
        profile,
        evaluations,
        validation,
        [],
        client=_ForgetfulClient(),
        budget=_FakeBudget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
        candidates_proposed=30,
    )

    assert "narrower comparison" not in markdown


@pytest.mark.asyncio
async def test_two_survivors_are_disclosed_without_the_single_place_advice():
    profile, validation = _profile_and_validation()
    evaluations = [_evaluation("A"), _evaluation("B")] + [
        _evaluation(str(i), eliminated=True) for i in range(6)
    ]

    markdown = await generate_recommendation(
        profile,
        evaluations,
        validation,
        [],
        client=_ForgetfulClient(),
        budget=_FakeBudget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
        candidates_proposed=30,
    )

    assert "much narrower comparison than usual" in markdown
    assert "not really a comparison" not in markdown


@pytest.mark.asyncio
async def test_nothing_eliminated_is_never_called_a_collapse():
    """Two candidates, both viable, is a small request -- not a narrowed one."""
    profile, validation = _profile_and_validation()
    evaluations = [_evaluation("A"), _evaluation("B")]

    markdown = await generate_recommendation(
        profile,
        evaluations,
        validation,
        [],
        client=_ForgetfulClient(),
        budget=_FakeBudget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
        candidates_proposed=2,
    )

    assert "narrower comparison" not in markdown
