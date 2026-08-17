"""Hard-constraint bands distinguish evidence status from elimination."""

import pytest

from app.agent.dynamic_evaluation import (
    HARD_CONSTRAINT_ELIMINATION_THRESHOLD,
    HARD_CONSTRAINT_MET_THRESHOLD,
    _check_hard_constraints,
    constraint_tier,
    evaluate_candidates,
    hard_constraint_sort_key,
    unmet_constraint_note,
)
from app.agent.models import (
    HARD_CONSTRAINT_BORDERLINE,
    HARD_CONSTRAINT_NO_EVIDENCE,
    HARD_CONSTRAINT_REQUIREMENT_NOT_MET,
    HARD_CONSTRAINT_VERIFIED,
    CandidateEvaluation,
    CandidatePlace,
    PlaceRequestProfile,
)


def _candidate(name: str = "Valletta") -> CandidatePlace:
    return CandidatePlace(
        place_name=name, country="Malta", reason_for_inclusion="test", verified=True, lat=1.0, lon=1.0
    )


def test_status_tiers_keep_legacy_compatibility():
    assert constraint_tier({"criterion": True}) == 0
    assert constraint_tier({"criterion": HARD_CONSTRAINT_BORDERLINE}) == 1
    assert constraint_tier({"criterion": None}) == 2
    assert constraint_tier({"criterion": False}) == 3


def test_the_reader_is_told_which_requirement_has_no_evidence():
    note = unmet_constraint_note({"criterion": None})
    assert note is not None
    assert "insufficient evidence to assess" in note
    assert "non-negotiable" in note


def test_the_reader_is_told_when_evidence_is_borderline():
    note = unmet_constraint_note({"criterion": HARD_CONSTRAINT_BORDERLINE})
    assert note is not None
    assert "Borderline" in note
    assert "not strong enough to verify" in note


def test_the_reader_is_told_when_requirement_is_not_met():
    note = unmet_constraint_note({"criterion": HARD_CONSTRAINT_REQUIREMENT_NOT_MET})
    assert note is not None
    assert "not adequately met" in note


@pytest.mark.parametrize(
    ("name", "score", "expected", "expected_eliminated"),
    [
        ("verified", 0.80, HARD_CONSTRAINT_VERIFIED, False),
        ("borderline", 0.70, HARD_CONSTRAINT_BORDERLINE, False),
        ("low borderline boundary", 0.50, HARD_CONSTRAINT_BORDERLINE, False),
        ("requirement not met without elimination", 0.42, HARD_CONSTRAINT_REQUIREMENT_NOT_MET, False),
        ("requirement not met with elimination", 0.15, HARD_CONSTRAINT_REQUIREMENT_NOT_MET, True),
    ],
)
def test_score_bands_land_where_they_should(
    name: str, score: float, expected: str, expected_eliminated: bool
):
    profile = PlaceRequestProfile(
        purpose="vacation",
        preferred_languages=["English"],
        hard_constraints=["English widely spoken"],
    )
    eliminated, _, results = _check_hard_constraints(
        profile, {"language_spoken": score}, _candidate(name), []
    )
    assert results["language_spoken"] == expected
    assert eliminated is expected_eliminated


def test_the_two_thresholds_are_distinct_and_ordered():
    """If these ever collapse back into one value, D55 is reintroduced."""
    assert HARD_CONSTRAINT_ELIMINATION_THRESHOLD < HARD_CONSTRAINT_MET_THRESHOLD


def test_a_constraint_nothing_measured_is_still_recorded_as_unconfirmed():
    """D33: a silent skip is how an unchecked flight cap cost Madeira nothing."""
    profile = PlaceRequestProfile(
        purpose="vacation", hard_constraints=["reasonably flat terrain"]
    )
    eliminated, _, results = _check_hard_constraints(profile, {}, _candidate(), [])
    assert eliminated is False
    assert results.get("terrain") == HARD_CONSTRAINT_NO_EVIDENCE


def test_english_course_requirement_without_education_evidence_stays_rankable():
    profile = PlaceRequestProfile(
        purpose="study",
        relevant_criteria=["education"],
        hard_constraints=["english_taught_courses"],
    )

    ranked = evaluate_candidates([_candidate("Valencia")], profile, {"Valencia": []})

    assert ranked[0].eliminated is False
    assert ranked[0].hard_constraint_results["education"] == HARD_CONSTRAINT_NO_EVIDENCE


def test_status_ordering_precedes_fit_score():
    evaluations = [
        CandidateEvaluation(place="Requirement Not Met", total_score=0.99, hard_constraint_results={"a": False}),
        CandidateEvaluation(place="No Evidence", total_score=0.98, hard_constraint_results={"a": None}),
        CandidateEvaluation(
            place="Borderline",
            total_score=0.20,
            hard_constraint_results={"a": HARD_CONSTRAINT_BORDERLINE},
        ),
        CandidateEvaluation(place="Verified", total_score=0.10, hard_constraint_results={"a": True}),
    ]

    ranked = sorted(
        evaluations,
        key=lambda evaluation: (
            evaluation.eliminated,
            *hard_constraint_sort_key(evaluation.hard_constraint_results),
            -evaluation.total_score,
        ),
    )

    assert [evaluation.place for evaluation in ranked] == [
        "Verified",
        "Borderline",
        "No Evidence",
        "Requirement Not Met",
    ]


def test_multiple_hard_constraints_are_combined_for_ranking():
    evaluations = [
        CandidateEvaluation(
            place="One verified",
            total_score=0.99,
            hard_constraint_results={
                "budget": HARD_CONSTRAINT_VERIFIED,
                "transportation": HARD_CONSTRAINT_BORDERLINE,
                "education": HARD_CONSTRAINT_NO_EVIDENCE,
            },
        ),
        CandidateEvaluation(
            place="Two verified",
            total_score=0.10,
            hard_constraint_results={
                "budget": HARD_CONSTRAINT_VERIFIED,
                "transportation": HARD_CONSTRAINT_VERIFIED,
                "education": HARD_CONSTRAINT_NO_EVIDENCE,
            },
        ),
    ]

    ranked = sorted(
        evaluations,
        key=lambda evaluation: (
            evaluation.eliminated,
            *hard_constraint_sort_key(evaluation.hard_constraint_results),
            -evaluation.total_score,
        ),
    )

    assert [evaluation.place for evaluation in ranked] == ["Two verified", "One verified"]
