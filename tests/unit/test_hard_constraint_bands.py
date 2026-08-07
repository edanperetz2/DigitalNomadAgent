"""D55: "met" must mean shown to meet it, not merely not catastrophic.

One threshold was answering two questions. The elimination floor is deliberately
low (0.2) because a false elimination is worse than a missed one -- but it was
also serving as the bar for reporting a non-negotiable as *met*, so anything
short of catastrophic was reported to the reader as satisfied.

P06's traveller uses a wheelchair and called flat terrain non-negotiable.
Valletta scores 0.6308 -- 49 m of spread, which this codebase labels "rolling" --
and the Wikivoyage evidence it was built from says the city is "steep in parts
(requiring walking up and down stairs)". It was recorded `met`, and the answer
came back "yes if you are comfortable with a compact, hilly historic center".
"""

import pytest

from app.agent.dynamic_evaluation import (
    HARD_CONSTRAINT_ELIMINATION_THRESHOLD,
    HARD_CONSTRAINT_MET_THRESHOLD,
    _check_hard_constraints,
    constraint_tier,
    unmet_constraint_note,
)
from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.tools.terrain import flatness_score, terrain_label

WHEELCHAIR_CONSTRAINTS = [
    "English widely spoken",
    "wheelchair accessibility",
    "step-free access around the city centre",
    "accessible public transport",
    "reasonably flat terrain",
]


def _candidate(name: str = "Valletta") -> CandidatePlace:
    return CandidatePlace(
        place_name=name, country="Malta", reason_for_inclusion="test", verified=True, lat=1.0, lon=1.0
    )


def _terrain_verdict(spread_m: float):
    profile = PlaceRequestProfile(purpose="vacation", hard_constraints=WHEELCHAIR_CONSTRAINTS)
    _, _, results = _check_hard_constraints(
        profile, {"terrain": flatness_score(spread_m)}, _candidate(), []
    )
    return results["terrain"]


def test_rolling_terrain_is_not_reported_as_meeting_a_flat_terrain_requirement():
    """Valletta exactly: 49 m of spread, scored 0.6308, labelled rolling."""
    assert terrain_label(49.0) == "rolling"
    assert _terrain_verdict(49.0) is None, "rolling terrain must not read as met"


def test_genuinely_flat_terrain_still_meets_it():
    assert terrain_label(15.0) == "flat"
    assert _terrain_verdict(15.0) is True


def test_terrain_bad_enough_to_fail_still_eliminates():
    profile = PlaceRequestProfile(purpose="vacation", hard_constraints=WHEELCHAIR_CONSTRAINTS)
    eliminated, reason, results = _check_hard_constraints(
        profile, {"terrain": flatness_score(95.0)}, _candidate("Steepville"), []
    )
    assert results["terrain"] is False
    assert eliminated is True
    assert reason and "Steepville" in reason


def test_the_middle_band_ranks_below_a_confirmed_place_without_being_dropped():
    """Unconfirmed is a demotion, not an elimination -- the D24/D28 guarantee."""
    assert constraint_tier({"terrain": True}) == 0
    assert constraint_tier({"terrain": None}) == 1
    assert constraint_tier({"terrain": False}) == 2


def test_the_reader_is_told_which_requirement_was_not_confirmed():
    note = unmet_constraint_note({"terrain": None})
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
    profile = PlaceRequestProfile(purpose="vacation", hard_constraints=WHEELCHAIR_CONSTRAINTS)
    _, _, results = _check_hard_constraints(profile, {}, _candidate(), [])
    assert results.get("terrain") is None
