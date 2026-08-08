"""A priority is unmeasured if nothing the reader is shown measured it.

`universally_unmeasured_priorities` ran over every evaluation, eliminated ones
included, so a criterion scored only on a candidate that was dropped counted as
measured and the disclosure stayed silent. P03's student ranked student life
first and it carried the highest weight; no delivered place had any evidence for
it, and the answer's "Not used in this ranking" block never fired -- the payload
had no such key at all. Its sibling, `universally_unverified_constraints`,
already judged over the viable list only.
"""

from app.agent.dynamic_evaluation import universally_unmeasured_priorities
from app.agent.models import CandidateEvaluation, PlaceRequestProfile

P03 = PlaceRequestProfile(
    purpose="study",
    relevant_criteria=["student life", "public transport", "safety"],
    inferred_weights={"student_life": 0.35, "public_transport": 0.25, "safety": 0.2},
)


def _evaluation(place, scores, *, eliminated=False):
    return CandidateEvaluation(
        place=place, country="Poland", criterion_scores=scores, eliminated=eliminated
    )


DELIVERED = _evaluation("Sofia", {"cost": 0.8, "transportation": 0.9, "safety": 0.8})


def test_a_priority_no_delivered_place_measured_is_reported():
    assert "student life" in universally_unmeasured_priorities(P03, [DELIVERED])


def test_evidence_on_an_eliminated_candidate_does_not_count_as_coverage():
    """The reader never sees that candidate, so it cannot answer their priority."""
    dropped = _evaluation("Poznan", {"student_life": 0.8}, eliminated=True)

    assert "student life" in universally_unmeasured_priorities(P03, [DELIVERED, dropped])


def test_evidence_on_a_delivered_candidate_still_counts():
    """One place with the evidence means the ranking could use it, so the block
    must stay silent rather than overstate the gap."""
    measured = _evaluation("Wroclaw", {"student_life": 0.8})

    assert "student life" not in universally_unmeasured_priorities(P03, [DELIVERED, measured])


def test_a_field_with_nothing_delivered_reports_nothing():
    dropped = _evaluation("Poznan", {"cost": 0.5}, eliminated=True)

    assert universally_unmeasured_priorities(P03, [dropped]) == []
