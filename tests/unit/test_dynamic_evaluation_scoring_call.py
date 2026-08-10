import json
from datetime import UTC, datetime

import pytest

from app.agent.dynamic_evaluation import (
    WIKIVOYAGE_EVIDENCE_CHARS,
    build_unresolved_scoring_payload,
    score_unresolved_criteria,
)
from app.agent.models import Budget, CandidateEvaluation, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.llm.base import BaseLLMClient, LLMRawResponse


def _evaluation(place, *, eliminated=False, unscored_evidence=None) -> CandidateEvaluation:
    return CandidateEvaluation(
        place=place,
        country="Testland",
        total_score=0.5,
        confidence_score=0.5,
        eliminated=eliminated,
        unscored_evidence=unscored_evidence or [],
    )


class _FakeBudget:
    async def check_before_call(self, request_id, module, est_input, est_output):
        return None

    async def record_call(self, request_id, module, model, input_tokens, output_tokens, cost, success):
        return None


class _EchoClient(BaseLLMClient):
    def __init__(self, text: str):
        self._text = text

    async def complete(self, messages, *, max_output_tokens, metadata=None):
        return LLMRawResponse(text=self._text, input_tokens=10, output_tokens=10, provider_cost_usd=0.0)


def test_build_unresolved_scoring_payload_skips_eliminated_and_fully_resolved():
    profile = PlaceRequestProfile(purpose="vacation")
    evaluations = [
        _evaluation("Eliminated", eliminated=True, unscored_evidence=["cost"]),
        _evaluation("FullyResolved", unscored_evidence=[]),
        _evaluation("Pending", unscored_evidence=["cost"]),
    ]
    evidence_by_place = {
        "Pending": [
            ToolResult(
                tool_name="BudgetFitTool",
                place="Pending",
                normalized_data={"budget_context": {"status": "not_provided"}},
                source_name="t",
                retrieved_at=datetime.now(UTC),
                confidence="medium",
            )
        ]
    }
    payload = build_unresolved_scoring_payload(evaluations, profile, evidence_by_place)
    assert [item["place"] for item in payload] == ["Pending"]
    assert "cost" in payload[0]["criteria"]


def _section(*chunks: tuple[str, str]) -> dict:
    """A Wikivoyage section context as the tools emit it."""
    return {
        "preview_excerpt": chunks[0][1][:600],
        "context_chunks": [
            {"subsection": subsection, "text": text, "subsection_truncated": False}
            for subsection, text in chunks
        ],
    }


def _activities_evidence(place: str, normalized_data: dict) -> dict[str, list[ToolResult]]:
    return {
        place: [
            ToolResult(
                tool_name="ActivitiesTool",
                place=place,
                normalized_data=normalized_data,
                source_name="OpenStreetMap and Wikivoyage",
                retrieved_at=datetime.now(UTC),
                confidence="medium",
            )
        ]
    }


def test_scoring_payload_carries_the_do_section_not_only_see():
    """"Do" is where hiking and nightlife live, and it used to be dropped
    whenever a "See" section existed."""
    profile = PlaceRequestProfile(purpose="vacation", activity_preferences=["hiking"])
    evidence = _activities_evidence(
        "Innsbruck",
        {
            "counts_by_category": {},
            "wikivoyage_see_context": _section(("Museums", "The regional museum has a folk art collection.")),
            "wikivoyage_do_context": _section(("Outdoors", "Countless hiking trails start from the funicular.")),
        },
    )
    payload = build_unresolved_scoring_payload(
        [_evaluation("Innsbruck", unscored_evidence=["activities"])], profile, evidence
    )
    excerpt = payload[0]["criteria"]["activities"]["wikivoyage_excerpt"]
    assert "hiking trails" in excerpt
    assert "folk art" in excerpt
    assert payload[0]["criteria"]["activities"]["wikivoyage_matched_interests"] == ["hiking"]


def test_scoring_payload_leads_with_the_prose_that_matches_a_stated_interest():
    """Selection is by relevance; it used to be the section's opening 600 chars."""
    profile = PlaceRequestProfile(purpose="vacation", activity_preferences=["nightlife"])
    filler = "A cathedral, a bridge and a square. " * 40
    evidence = _activities_evidence(
        "Porto",
        {
            "counts_by_category": {},
            "wikivoyage_see_context": _section(
                ("Landmarks", filler),
                ("After dark", "Nightlife concentrates around the Galerias de Paris."),
            ),
        },
    )
    payload = build_unresolved_scoring_payload(
        [_evaluation("Porto", unscored_evidence=["activities"])], profile, evidence
    )
    excerpt = payload[0]["criteria"]["activities"]["wikivoyage_excerpt"]
    assert excerpt.startswith("[After dark]")
    # The filler alone would have exhausted the budget and buried this.
    assert "Galerias de Paris" in excerpt


def test_scoring_payload_stays_within_its_char_budget():
    profile = PlaceRequestProfile(purpose="vacation")
    evidence = _activities_evidence(
        "Rome",
        {
            "counts_by_category": {},
            "wikivoyage_see_context": _section(("Ancient", "x" * 5_000), ("Baroque", "y" * 5_000)),
        },
    )
    payload = build_unresolved_scoring_payload(
        [_evaluation("Rome", unscored_evidence=["activities"])], profile, evidence
    )
    excerpt = payload[0]["criteria"]["activities"]["wikivoyage_excerpt"]
    assert len(excerpt) <= WIKIVOYAGE_EVIDENCE_CHARS + 40  # + subsection labels


def test_an_interest_filed_under_soft_preferences_still_selects_prose():
    """P04's food and market interests landed in soft_preferences, not
    activity_preferences, so relevance selection had nothing to match on."""
    profile = PlaceRequestProfile(
        purpose="vacation",
        activity_preferences=[],
        # Verbatim from the P04 profile of validation_runs/20260805T061515Z.
        soft_preferences=["good food scene", "strong street food culture", "strong market culture"],
    )
    filler = "The cathedral dates from the twelfth century. " * 30
    evidence = _activities_evidence(
        "Palermo",
        {
            "counts_by_category": {},
            "wikivoyage_see_context": _section(
                ("Churches", filler),
                ("Markets", "The Ballaro street market sells food from dawn."),
            ),
        },
    )
    payload = build_unresolved_scoring_payload(
        [_evaluation("Palermo", unscored_evidence=["activities"])], profile, evidence
    )
    activities = payload[0]["criteria"]["activities"]
    assert activities["wikivoyage_excerpt"].startswith("[Markets]")
    assert set(activities["wikivoyage_matched_interests"]) >= {"food", "market"}


def test_unsupported_touristiness_does_not_leak_into_activity_scoring():
    profile = PlaceRequestProfile(
        purpose="vacation",
        soft_preferences=["cheap", "warm city", "not too touristy"],
        relevant_criteria=["budget", "climate", "internet_quality", "touristy_level"],
    )
    evidence = _activities_evidence(
        "Seville",
        {
            "counts_by_category": {"culture": 10},
            "wikivoyage_see_context": _section(
                ("Old town", "The central quarter is the most touristy part of the city.")
            ),
        },
    )

    payload = build_unresolved_scoring_payload(
        [_evaluation("Seville", unscored_evidence=["activities"])], profile, evidence
    )

    assert payload[0]["preferences"]["soft_preferences"] == []
    activities = payload[0]["criteria"]["activities"]
    assert "wikivoyage_matched_interests" not in activities


def test_a_stray_word_from_a_preference_phrase_is_not_treated_as_a_match():
    """"car-free livability" once matched a line about a "free PDF guide"."""
    profile = PlaceRequestProfile(purpose="remote_work", mobility_requirements=["car-free livability"])
    evidence = {
        "Tirana": [
            ToolResult(
                tool_name="LocalMobilityTool",
                place="Tirana",
                normalized_data={
                    "counts_by_component": {},
                    "wikivoyage_context": _section(
                        ("By bike", "Bikes can be rented for self tours with free PDF guide provided.")
                    ),
                },
                source_name="Wikivoyage",
                retrieved_at=datetime.now(UTC),
                confidence="medium",
            )
        ]
    }
    payload = build_unresolved_scoring_payload(
        [_evaluation("Tirana", unscored_evidence=["transportation"])], profile, evidence
    )
    transportation = payload[0]["criteria"]["transportation"]
    # The prose is still sent -- it just does not count as a matched interest.
    assert "free PDF guide" in transportation["wikivoyage_excerpt"]
    assert "wikivoyage_matched_interests" not in transportation


def test_scoring_payload_omits_prose_when_a_tool_collected_none():
    profile = PlaceRequestProfile(purpose="vacation")
    evidence = _activities_evidence("Nowhere", {"counts_by_category": {"culture": 3}})
    payload = build_unresolved_scoring_payload(
        [_evaluation("Nowhere", unscored_evidence=["activities"])], profile, evidence
    )
    activities = payload[0]["criteria"]["activities"]
    assert activities["wikivoyage_excerpt"] is None
    assert "wikivoyage_matched_interests" not in activities


@pytest.mark.asyncio
async def test_score_unresolved_criteria_returns_empty_without_pending_work():
    profile = PlaceRequestProfile(purpose="vacation")
    evaluations = [_evaluation("Resolved", unscored_evidence=[])]
    result = await score_unresolved_criteria(
        evaluations,
        profile,
        {},
        client=_EchoClient(""),
        budget=_FakeBudget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_score_unresolved_criteria_parses_llm_response():
    profile = PlaceRequestProfile(purpose="vacation", budget=Budget())
    evaluations = [_evaluation("Nice", unscored_evidence=["cost"])]
    evidence_by_place = {
        "Nice": [
            ToolResult(
                tool_name="BudgetFitTool",
                place="Nice",
                normalized_data={"budget_context": {"status": "not_provided"}},
                source_name="t",
                retrieved_at=datetime.now(UTC),
                confidence="medium",
            )
        ]
    }
    response_text = json.dumps(
        {"scores": [{"place": "Nice", "criterion": "cost", "score": 0.75, "rationale": "Affordable."}]}
    )
    result = await score_unresolved_criteria(
        evaluations,
        profile,
        evidence_by_place,
        client=_EchoClient(response_text),
        budget=_FakeBudget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
    )
    assert result == {"Nice": {"cost": (0.75, "Affordable.")}}
