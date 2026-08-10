"""D61/D62: which stated requirements are recognised, and what happens to the rest.

Hard constraints arrive as the traveller's own free text and were matched by raw
substring against a small keyword list. That failed in both directions at once,
and the original ten evaluation prompts happened to phrase everything inside the
vocabulary, so neither half was visible until P11-P20 were added.

D61 -- unmatched requirements vanished. "must be liveable without a car" matched;
"no car required" did not, and left no trace: not "unverified", absent. Three of
the new prompts recorded nothing at all for every non-negotiable they stated.

D62 -- matched requirements were invented. "a one-bedroom flat" hit the terrain
keyword "flat", and "remote work" hit the accessibility keyword "remote" (which
means airport proximity). P12 stated neither and was told its top pick could not
be confirmed for flat terrain. That is D46 recurring, which narrowed these same
triggers once already.
"""

import pytest

from app.agent.dynamic_evaluation import (
    _check_hard_constraints,
    constraint_tier,
    criteria_for_constraint,
    unmet_constraint_note,
)
from app.agent.models import CandidatePlace, PlaceRequestProfile


def _candidate(name: str = "Somewhere") -> CandidatePlace:
    return CandidatePlace(
        place_name=name, country="Country", reason_for_inclusion="test", verified=True, lat=1.0, lon=1.0
    )


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        # Paraphrases of the same requirement must reach the same criterion.
        ("must be liveable without a car", ["transportation"]),
        ("no car required", ["transportation"]),
        ("I don't drive", ["transportation"]),
        ("reasonably flat terrain", ["terrain"]),
        ("wheelchair accessibility", ["terrain"]),
        ("step-free access around the city centre", ["terrain"]),
        ("English widely spoken", ["language_spoken"]),
        ("safety is my top priority", ["safety"]),
        ("budget no more than 1800 per month", ["cost"]),
    ],
)
def test_a_stated_requirement_reaches_the_criterion_that_measures_it(phrase, expected):
    assert criteria_for_constraint(phrase) == expected


@pytest.mark.parametrize(
    "phrase",
    [
        "budget around 1600 per month including a one-bedroom flat",  # "flat" the dwelling
        "I want a flat share near the centre",
    ],
)
def test_a_flat_to_live_in_is_not_a_terrain_requirement(phrase):
    """D62: the word means two entirely different things."""
    assert "terrain" not in criteria_for_constraint(phrase)


@pytest.mark.parametrize("phrase", ["remote work stay", "I work remotely", "somewhere remote and quiet"])
def test_remote_work_is_not_an_airport_access_requirement(phrase):
    """D62: `accessibility` here means ease of *getting there*, not working remotely."""
    assert "accessibility" not in criteria_for_constraint(phrase)


@pytest.mark.parametrize(
    "phrase",
    [
        "quick access to a hospital",
        "clean air is the deciding factor",
        "reachable from Madrid with at most one connecting flight",
        "coastal location",
        "must be within about two hours of UK time",
    ],
)
def test_a_requirement_nothing_measures_matches_nothing_rather_than_something_close(phrase):
    """Deliberately unmatched.

    A wrong match is worse than none: it is reported to the reader as checked.
    `accessibility` scores airport proximity, which does not answer "at most one
    connecting flight", so that phrase is left for the unmatched path to disclose.
    """
    assert criteria_for_constraint(phrase) == []


def test_a_stated_internet_requirement_now_matches_because_it_is_measured():
    """"decent internet" was in the list above until InternetConnectivityTool.

    D75's lesson in the other direction: a requirement is reported as uncheckable
    only while nothing checks it. Once it is measured, leaving it unmatched tells
    the reader their top priority went unexamined while the answer scores it.
    """
    for phrase in ("decent internet", "fast reliable wifi", "good broadband"):
        assert criteria_for_constraint(phrase) == ["internet"], phrase


def test_airport_connectivity_is_still_about_flights_not_wifi():
    """`connectivity` stays with accessibility -- stealing it would be D62."""
    assert "internet" not in criteria_for_constraint("good airport connectivity")


def test_an_unmatched_requirement_is_disclosed_rather_than_dropped():
    """D61: the whole point -- the reader is told it went unchecked."""
    profile = PlaceRequestProfile(
        purpose="remote_work",
        hard_constraints=["clean air is the deciding factor", "quick access to a hospital"],
    )
    eliminated, _, results = _check_hard_constraints(profile, {"cost": 0.9}, _candidate(), [])

    assert set(results) == {"clean air is the deciding factor", "quick access to a hospital"}
    assert all(value is None for value in results.values())
    assert not eliminated, "an unmeasurable requirement must never eliminate a place"
    assert constraint_tier(results) == 1, "unverified ranks below verified, above failed"

    note = unmet_constraint_note(results)
    assert note and "clean air" in note and "quick access to a hospital" in note


def test_a_region_requirement_is_not_reported_as_unconfirmed():
    """The region check already answers these.

    Saying "nothing in the evidence confirms not in Southeast Asia" would be
    false when every candidate was filtered out of it.
    """
    profile = PlaceRequestProfile(
        purpose="remote_work",
        excluded_regions=["Southeast Asia"],
        hard_constraints=["not in Southeast Asia", "decent internet"],
    )
    _, _, results = _check_hard_constraints(profile, {}, _candidate(), [])

    # Keyed by criterion now that internet is measured; unmatched requirements
    # are the ones keyed by the traveller's raw wording.
    assert "internet" in results
    assert not any("Southeast Asia" in key for key in results)


@pytest.mark.parametrize(
    "phrase",
    [
        "ten days in October",
        "five-month stay starting in February",
        "Stay duration is three months",
        "traveling alone",
        "family of four",
        "remote work stay",
    ],
)
def test_facts_about_the_trip_are_not_reported_as_unmet_requirements(phrase):
    """These are not properties a place can satisfy or fail.

    The interpreter files them under hard_constraints because it has nowhere
    better to put them; `duration`, `target_months` and `purpose` already carry
    them. "Nothing in the evidence confirms ten days in October" would read as a
    broken system rather than a careful one.
    """
    profile = PlaceRequestProfile(purpose="vacation", hard_constraints=[phrase])
    _, _, results = _check_hard_constraints(profile, {}, _candidate(), [])
    assert results == {}


def test_a_requirement_with_its_own_measured_check_is_not_also_called_unconfirmed():
    """Timezone overlap is measured separately; reporting it twice contradicts itself."""
    profile = PlaceRequestProfile(
        purpose="remote_work",
        hard_constraints=["at least four hours of overlap with US Eastern time"],
    )
    _, _, results = _check_hard_constraints(profile, {}, _candidate(), [])

    assert "timezone" in results
    assert not any("overlap" in key for key in results if key != "timezone")


def test_unmatched_requirements_are_bounded():
    """A verbose interpreter must not be able to flood the answer."""
    profile = PlaceRequestProfile(
        purpose="vacation",
        hard_constraints=[f"unmeasurable requirement number {n}" for n in range(12)]
        + ["x" * 400],
    )
    _, _, results = _check_hard_constraints(profile, {}, _candidate(), [])

    assert 0 < len(results) <= 4
    assert all(len(key) <= 90 for key in results)


def test_a_measured_requirement_still_eliminates_when_the_evidence_fails_it():
    """The bands from D55 are untouched by the matching change."""
    profile = PlaceRequestProfile(purpose="vacation", hard_constraints=["reasonably flat terrain"])
    eliminated, reason, results = _check_hard_constraints(
        profile, {"terrain": 0.05}, _candidate("Steepville"), []
    )
    assert results["terrain"] is False
    assert eliminated and reason and "Steepville" in reason
