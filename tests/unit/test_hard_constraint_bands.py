"""Hard-constraint bands distinguish met, unverified, and failed."""

import pytest

from app.agent.dynamic_evaluation import (
    HARD_CONSTRAINT_ELIMINATION_THRESHOLD,
    HARD_CONSTRAINT_MET_THRESHOLD,
    _check_hard_constraints,
    constraint_tier,
    unmet_constraint_note,
)
from app.agent.models import CandidatePlace, PlaceRequestProfile


def _candidate(name: str = "Valletta") -> CandidatePlace:
    return CandidatePlace(
        place_name=name, country="Malta", reason_for_inclusion="test", verified=True, lat=1.0, lon=1.0
    )


def test_the_middle_band_ranks_below_a_confirmed_place_without_being_dropped():
    """Unconfirmed is a demotion, not an elimination -- the D24/D28 guarantee."""
    assert constraint_tier({"criterion": True}) == 0
    assert constraint_tier({"criterion": None}) == 1
    assert constraint_tier({"criterion": False}) == 2


def test_the_reader_is_told_which_requirement_was_not_confirmed():
    note = unmet_constraint_note({"criterion": None})
    assert note is not None
    assert "nothing in the evidence confirms" in note
    assert "non-negotiable" in note


@pytest.mark.parametrize(
    ("band", "score", "expected"),
    [("native", 1.0, True), ("widespread", 0.75, True), ("limited", 0.25, None)],
)
def test_english_bands_land_where_they_should(band: str, score: float, expected):
    """The D58/D55 interaction.

    D58 stopped a country whose official list omits English from scoring 0.0 and
    being eliminated. That left the weakest band, 0.25, above the 0.2 floor --
    so a hard English requirement could no longer eliminate *or* be doubted, and
    "English alone will not get you far" was reported as met. It now reads as
    unconfirmed, which is what it is.
    """
    profile = PlaceRequestProfile(
        purpose="vacation",
        preferred_languages=["English"],
        hard_constraints=["English widely spoken"],
    )
    _, _, results = _check_hard_constraints(
        profile, {"language_spoken": score}, _candidate(band), []
    )
    assert results["language_spoken"] is expected


def test_the_two_thresholds_are_distinct_and_ordered():
    """If these ever collapse back into one value, D55 is reintroduced."""
    assert HARD_CONSTRAINT_ELIMINATION_THRESHOLD < HARD_CONSTRAINT_MET_THRESHOLD


def test_a_constraint_nothing_measured_is_still_recorded_as_unconfirmed():
    """D33: a silent skip is how an unchecked flight cap cost Madeira nothing."""
    profile = PlaceRequestProfile(
        purpose="vacation", hard_constraints=["reasonably flat terrain"]
    )
    _, _, results = _check_hard_constraints(profile, {}, _candidate(), [])
    assert results.get("terrain") is None
