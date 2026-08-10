"""D60: being verified on a non-negotiable has to count for something.

`constraint_tier` is deliberately coarse -- one unverified requirement puts a
candidate in tier 1 whether the other four were confirmed or not. That is fine
until some requirement is unverified for the whole field, which is common: every
candidate lands in the same tier, the tier stops discriminating, and ordering
falls through to raw score.

P06 is the case. Seville had `terrain: met` -- confirmed flat, which the
wheelchair user called non-negotiable -- and Lisbon had it unverified, with the
answer itself describing Lisbon as hilly. Both carried an unverified
`transportation`, so both sat in tier 1 and Lisbon won on score.
"""

import pytest

from app.agent.dynamic_evaluation import (
    confirmed_constraint_count,
    constraint_tier,
    evaluate_candidates,
)
from app.agent.models import CandidateEvaluation, CandidatePlace, PlaceRequestProfile


def _evaluation(place: str, constraints: dict, score: float) -> CandidateEvaluation:
    return CandidateEvaluation(
        place=place,
        country="Country",
        total_score=score,
        criterion_scores={},
        advantages=[],
        drawbacks=["a drawback"],
        confidence_score=0.5,
        hard_constraint_results=constraints,
    )


def _order(evaluations: list[CandidateEvaluation]) -> list[str]:
    ranked = sorted(
        evaluations,
        key=lambda e: (
            e.eliminated,
            constraint_tier(e.hard_constraint_results),
            -confirmed_constraint_count(e.hard_constraint_results),
            -e.total_score,
        ),
    )
    return [e.place for e in ranked]


def test_a_confirmed_requirement_outranks_a_higher_score():
    """P06 exactly: same tier, Lisbon scores higher, Seville is the one verified."""
    evaluations = [
        _evaluation("Lisbon", {"terrain": None, "transportation": None}, 0.72),
        _evaluation("Seville", {"terrain": True, "transportation": None}, 0.65),
    ]
    assert [constraint_tier(e.hard_constraint_results) for e in evaluations] == [1, 1]
    assert _order(evaluations) == ["Seville", "Lisbon"]


def test_more_confirmations_outrank_fewer():
    evaluations = [
        _evaluation("One", {"a": True, "b": None, "c": None}, 0.9),
        _evaluation("Two", {"a": True, "b": True, "c": None}, 0.5),
    ]
    assert _order(evaluations) == ["Two", "One"]


def test_score_still_decides_when_confirmations_are_equal():
    """The tiebreak breaks ties; it does not replace the ranking."""
    evaluations = [
        _evaluation("Lower", {"a": True, "b": None}, 0.4),
        _evaluation("Higher", {"a": True, "b": None}, 0.8),
    ]
    assert _order(evaluations) == ["Higher", "Lower"]


def test_a_failed_constraint_still_sorts_last_whatever_it_confirmed():
    """D33/D51: a place shown not to meet a non-negotiable never climbs."""
    evaluations = [
        _evaluation("Failed", {"a": True, "b": True, "c": False}, 0.99),
        _evaluation("Unverified", {"a": None, "b": None, "c": None}, 0.10),
    ]
    assert _order(evaluations) == ["Unverified", "Failed"]


def test_fully_verified_still_beats_partially_verified():
    evaluations = [
        _evaluation("Partial", {"a": True, "b": None}, 0.95),
        _evaluation("Complete", {"a": True, "b": True}, 0.20),
    ]
    assert [constraint_tier(e.hard_constraint_results) for e in evaluations] == [1, 0]
    assert _order(evaluations) == ["Complete", "Partial"]


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ({}, 0),
        ({"a": None, "b": None}, 0),
        ({"a": True, "b": None, "c": False}, 1),
        ({"a": True, "b": True}, 2),
    ],
)
def test_confirmations_are_counted_not_inferred(results, expected):
    assert confirmed_constraint_count(results) == expected


def test_the_end_to_end_ranking_uses_it():
    """Guards the sort key itself, not just the helper."""
    from datetime import UTC, datetime

    from app.evidence.models import ToolResult

    def tool(name: str, place: str, data: dict) -> ToolResult:
        return ToolResult(
            tool_name=name,
            place=place,
            normalized_data=data,
            source_name="s",
            retrieved_at=datetime.now(UTC),
            confidence="medium",
        )

    def candidate(name: str) -> CandidatePlace:
        return CandidatePlace(
            place_name=name, country="Country", reason_for_inclusion="t", verified=True, lat=1.0, lon=1.0
        )

    profile = PlaceRequestProfile(
        purpose="vacation",
        preferred_languages=["English"],
        relevant_criteria=["language_spoken"],
        hard_constraints=["English widely spoken"],
    )
    evidence = {
        "Confirmed": [
            tool(
                "LanguageTool",
                "Confirmed",
                {
                    "spoken_languages": ["English"],
                    "requested_languages": ["English"],
                    "matched_languages": ["English"],
                    "english_reach": "native",
                    "english_score": 1.0,
                },
            )
        ],
        "Unknown": [],
    }
    ranked = evaluate_candidates([candidate("Unknown"), candidate("Confirmed")], profile, evidence)
    assert ranked[0].place == "Confirmed"
    assert ranked[0].hard_constraint_results["language_spoken"] is True
