"""D63: a requirement no place could be checked against is not a drawback of any place.

P14 stated three requirements, all phrased so nothing matched them, so every one
of its eight candidates carried all three as unverified. The note went in at
`drawbacks[0]` -- which is the "Main drawback" column -- giving eight identical
rows in the one column meant to tell them apart, each claiming the place was
"ranked below places that could be checked" when no such place existed.

Same rule as D36 for unmeasured priorities: a gap on one candidate is a drawback
for that candidate, a gap on every candidate is a fact about the ranking.
"""

from app.agent.dynamic_evaluation import (
    _constraint_label,
    apply_unmet_constraint_notes,
    universally_unverified_constraints,
)
from app.agent.models import CandidateEvaluation
from app.agent.recommendation_generator import _unverifiable_requirements_disclosure

REAL_DRAWBACK = "Cost evidence is country-level only."


def _evaluation(place: str, constraints: dict, *, eliminated: bool = False) -> CandidateEvaluation:
    return CandidateEvaluation(
        place=place,
        country="Country",
        total_score=0.5,
        criterion_scores={},
        advantages=[],
        drawbacks=[REAL_DRAWBACK],
        confidence_score=0.5,
        hard_constraint_results=constraints,
        eliminated=eliminated,
    )


def test_a_requirement_unverified_everywhere_leaves_the_main_drawback_alone():
    """P14 exactly: three requirements, unverified for all eight candidates."""
    shared = {
        "avoid_hot_weather": None,
        "must_have_reliable_internet": None,
        "travel_time_under_10_hours": None,
    }
    evaluations = [_evaluation(p, dict(shared)) for p in ("Lisbon", "Taipei", "Tokyo", "Seoul")]

    assert universally_unverified_constraints(evaluations) == [
        "avoid hot weather",
        "must have reliable internet",
        "travel time under 10 hours",
    ]

    for evaluation in apply_unmet_constraint_notes(evaluations):
        assert evaluation.drawbacks[0] == REAL_DRAWBACK
        assert not any("Ranked below" in d for d in evaluation.drawbacks)


def test_a_requirement_that_tells_candidates_apart_is_still_a_drawback():
    """The note earns its place when it actually discriminates."""
    evaluations = [
        _evaluation("Verified", {"terrain": True}),
        _evaluation("Unverified", {"terrain": None}),
    ]
    by_place = {e.place: e for e in apply_unmet_constraint_notes(evaluations)}

    assert by_place["Verified"].drawbacks[0] == REAL_DRAWBACK
    assert "Ranked below places that could be checked" in by_place["Unverified"].drawbacks[0]
    # Named in the reader's words, not by the internal criterion key.
    assert _constraint_label("terrain") in by_place["Unverified"].drawbacks[0]


def test_the_shared_requirement_is_still_told_to_the_reader_once():
    """Removed from the rows, not removed from the answer."""
    text = _unverifiable_requirements_disclosure(["decent internet", "travel time under 10 hours"])
    assert "nothing here could check it" in text
    assert "decent internet" in text and "travel time under 10 hours" in text
    assert "did not affect the order" in text, "must not imply a ranking penalty that did not happen"


def test_no_disclosure_when_every_requirement_was_checked():
    assert _unverifiable_requirements_disclosure([]) == ""
    evaluations = [_evaluation("A", {"cost": True}), _evaluation("B", {"cost": False})]
    assert universally_unverified_constraints(evaluations) == []


def test_a_single_surviving_candidate_keeps_its_note():
    """With one place there is no field to be uniform across, so the note still applies."""
    evaluations = [_evaluation("Only", {"terrain": None})]
    assert universally_unverified_constraints(evaluations) == []
    assert "Ranked below" in apply_unmet_constraint_notes(evaluations)[0].drawbacks[0]


def test_eliminated_candidates_do_not_mask_a_shared_gap():
    """A place already out of the running should not make a gap look discriminating."""
    evaluations = [
        _evaluation("In1", {"terrain": None}),
        _evaluation("In2", {"terrain": None}),
        _evaluation("Out", {"terrain": True}, eliminated=True),
    ]
    assert universally_unverified_constraints(evaluations) == [_constraint_label("terrain")]


def test_the_reader_never_sees_an_interpreter_identifier():
    """D42: `travel_time_under_10_hours` is machinery, not a sentence."""
    text = _unverifiable_requirements_disclosure(
        universally_unverified_constraints(
            [_evaluation(p, {"travel_time_under_10_hours": None}) for p in ("A", "B")]
        )
    )
    assert "_" not in text.split("**")[-1]
    assert "travel time under 10 hours" in text
