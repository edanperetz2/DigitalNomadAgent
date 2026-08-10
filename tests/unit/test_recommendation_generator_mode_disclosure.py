import json

import pytest

from app.agent.models import CandidateEvaluation, PlaceRequestProfile, ValidationResult
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
    assert "Generated using:** a deterministic fallback template" in markdown


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
    assert "Generated using:** mock deterministic mode" in markdown


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
    assert "Generated using:** a real LLM provider" in markdown


def test_the_payload_tells_the_generator_a_place_was_named():
    """D30: it never did, so the model could not lead with a verdict even in
    principle -- it was accurately describing the only thing it was given."""
    from app.agent.models import PlaceRequestProfile, ValidationResult
    from app.agent.recommendation_generator import _build_payload

    validation = ValidationResult(approved=True)

    named = _build_payload(
        PlaceRequestProfile(purpose="remote_work", named_destinations=["Lisbon"]),
        [CandidateEvaluation(place="Lisbon", country="Portugal")],
        validation,
        [],
        3,
    )
    assert named["named_destinations"] == ["Lisbon"]

    missing = _build_payload(
        PlaceRequestProfile(
            purpose="remote_work", named_destinations=["Porto", "Valencia", "Split"]
        ),
        [
            CandidateEvaluation(place="Porto", country="Portugal"),
            CandidateEvaluation(place="Lisbon", country="Portugal"),
            CandidateEvaluation(place="Split", country="Croatia"),
        ],
        validation,
        [],
        3,
    )
    assert missing["named_destinations"] == ["Porto", "Split"]
    assert missing["unresearched_named_destinations"] == ["Valencia"]

    # Absent otherwise, so the nine prompts that name nothing are untouched.
    plain = _build_payload(PlaceRequestProfile(purpose="remote_work"), [], validation, [], 3)
    assert "named_destinations" not in plain


@pytest.mark.asyncio
async def test_degradation_notice_survives_the_generator_rewriting_everything():
    """D32: the notice used to ride in profile.assumptions, which the generator
    is free to reword -- and did. P10's Request Interpreter failed with a 400,
    the deterministic parser took over, and the answer came back looking like an
    ordinary complete run. The model here returns markdown that mentions none of
    it, exactly as the real one did."""
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
        max_output_tokens=128,
        service_notices=["The request-interpreter model was unavailable."],
    )

    assert "**Reduced-capability run:**" in markdown
    assert "The request-interpreter model was unavailable." in markdown


@pytest.mark.asyncio
async def test_out_of_scope_asks_are_declined_by_name():
    """D32: P10 asked for confirmed flight prices, nightly hotel rates and a
    visa fee, and got a ranked list of cities mentioning none of the three."""
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
        max_output_tokens=128,
        out_of_scope=["live or confirmed flight prices", "visa fees or entry eligibility"],
    )

    assert "outside what this agent can answer" in markdown
    assert "live or confirmed flight prices" in markdown
    assert "visa fees or entry eligibility" in markdown


def test_a_clean_run_carries_neither_disclosure():
    profile, validation = _profile_and_validation()
    markdown = render_recommendation_fallback(profile, [], validation, [])
    assert "Reduced-capability run" not in markdown
    assert "outside what this agent can answer" not in markdown


@pytest.mark.asyncio
async def test_a_priority_nothing_could_measure_is_stated_not_footnoted():
    """D36: P04 ranked five cities without measuring food scene, street food,
    market culture or party level, and said so only in the limitations section
    at the bottom."""
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
        max_output_tokens=128,
        unmeasured_priorities=["food scene", "street food"],
    )

    assert "Not used in this ranking" in markdown
    assert "food scene" in markdown
    assert "street food" in markdown


def test_the_model_is_handed_labels_not_internal_scores():
    """D41: the generator received model_dump() and copied the floats into
    prose -- "Total score is 0.8145", "Confidence 0.61" -- which is meaningless
    to a traveller and, in P05, identical for six of seven candidates."""
    from app.agent.recommendation_generator import _llm_payload

    raw = {
        "candidates": [
            {
                "place": "Sofia",
                "country": "Bulgaria",
                "total_score": 0.939,
                "confidence_score": 0.82,
                "criterion_scores": {"cost": 0.98, "transportation": 0.4},
                "hard_constraint_results": {"cost": True, "timezone": None},
                "advantages": ["8 coworking, 300 cafe nearby."],
                "drawbacks": [],
                "missing_evidence": [],
                "criterion_sources": {"cost": ["WhereNext"]},
            }
        ]
    }

    presented = _llm_payload(raw)["candidates"][0]

    assert "total_score" not in presented
    assert "confidence_score" not in presented
    assert presented["confidence"] == "High"
    assert presented["criterion_strength"] == {"cost": "strong", "transportation": "weak"}
    assert presented["hard_constraints"] == {"cost": "met", "timezone": "could not be checked"}
    assert presented["rank"] == 1
    assert "8 coworking, 300 cafe nearby." in presented["advantages"]


def test_the_deterministic_renderer_still_gets_the_numbers_it_needs():
    """It compares scores to name the real trade-off between the top two, so it
    must keep the raw payload."""
    from app.agent.recommendation_generator import _build_payload

    profile, validation = _profile_and_validation()
    evaluation = CandidateEvaluation(
        place="Sofia", country="Bulgaria", total_score=0.9, confidence_score=0.8
    )

    payload = _build_payload(profile, [evaluation], validation, [], 3)

    assert payload["candidates"][0]["total_score"] == 0.9


def test_the_field_name_is_absent_from_the_prompt_when_nothing_was_named():
    """D42: it told a retired couple "I did not receive a `named_destinations`
    field", and nine of ten answers headed a section "Verdict on the named
    destinations" when the traveller had named nothing."""
    from app.agent.recommendation_generator import NAMED_DESTINATION_PROMPT, SYSTEM_PROMPT

    assert "named_destinations" not in SYSTEM_PROMPT
    assert "named_destinations" not in NAMED_DESTINATION_PROMPT


def test_the_named_destination_instruction_exists_for_when_one_was_named():
    from app.agent.recommendation_generator import NAMED_DESTINATION_PROMPT

    assert "has named specific places" in NAMED_DESTINATION_PROMPT
    assert "never relabel another candidate" in NAMED_DESTINATION_PROMPT


def test_leaky_payload_keys_are_renamed_for_the_model():
    from app.agent.recommendation_generator import _llm_payload

    presented = _llm_payload(
        {
            "candidates": [],
            "validation_issues": ["a caveat"],
            "named_destinations": ["Lisbon"],
            "unmeasured_priorities": ["food scene"],
        }
    )

    assert "validation_issues" not in presented
    assert "named_destinations" not in presented
    assert presented["caveats_to_pass_on"] == ["a caveat"]
    assert presented["places_the_traveller_named"] == ["Lisbon"]
    assert presented["priorities_no_evidence_could_be_found_for"] == ["food scene"]


def test_writer_ranking_must_preserve_the_evaluators_order():
    from app.agent.recommendation_generator import _ranking_matches_payload

    payload = {"candidates": [{"place": "Porto"}, {"place": "Valencia"}, {"place": "Split"}]}
    correct = """## Best matches
| Rank | Place | Why | Drawback | Confidence |
|---:|---|---|---|---|
| 1 | Porto | x | y | Medium |
| 2 | Valencia | x | y | Medium |
| 3 | Split | x | y | Medium |"""
    relabelled = correct.replace("| 2 | Valencia |", "| 2 | Lisbon |")
    reordered = correct.replace("| 1 | Porto |", "| 1 | Split |").replace(
        "| 3 | Split |", "| 3 | Porto |"
    )

    assert _ranking_matches_payload(correct, payload)
    assert not _ranking_matches_payload(relabelled, payload)
    assert not _ranking_matches_payload(reordered, payload)
