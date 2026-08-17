"""A stated climate need must not be reported as something nothing could check.

`climate` is scored on every request that states months, and had no row in
`_HARD_CONSTRAINT_KEYWORDS` at all. That was invisible while unmatched
requirements were silently dropped. Once they are disclosed (D70), it told a
retired couple ranking cities by winter warmth that nothing here could check
"cold that leaves them housebound" -- in an answer whose every row discusses
winter climate.
"""

import pytest

from app.agent.dynamic_evaluation import (
    _check_hard_constraints,
    criteria_for_constraint,
)
from app.agent.models import (
    HARD_CONSTRAINT_REQUIREMENT_NOT_MET,
    HARD_CONSTRAINT_VERIFIED,
    CandidatePlace,
    PlaceRequestProfile,
)

CANDIDATE = CandidatePlace(place_name="London", country="United Kingdom", reason_for_inclusion="x")


@pytest.mark.parametrize(
    "phrase",
    ["cold that leaves them housebound", "mild winters", "avoid cold", "proper snow", "hot weather"],
)
def test_a_climate_requirement_matches_the_criterion_that_measures_it(phrase):
    assert criteria_for_constraint(phrase) == ["climate"]


def test_a_stated_climate_deal_breaker_is_not_reported_as_uncheckable():
    profile = PlaceRequestProfile(
        purpose="vacation", deal_breakers=["cold that leaves them housebound"]
    )

    _, _, results = _check_hard_constraints(profile, {"climate": 0.8}, CANDIDATE, [])

    assert results["climate"] == HARD_CONSTRAINT_VERIFIED
    assert not any(key.startswith("avoiding") for key in results)


def test_a_poor_climate_match_is_reported_but_never_eliminates():
    """The preference already carries weight in the ranking. Making it a filter
    at the 0.2 floor is a different change, and one that can empty the field."""
    profile = PlaceRequestProfile(
        purpose="vacation", deal_breakers=["cold that leaves them housebound"]
    )

    eliminated, reason, results = _check_hard_constraints(profile, {"climate": 0.05}, CANDIDATE, [])

    assert results["climate"] == HARD_CONSTRAINT_REQUIREMENT_NOT_MET
    assert eliminated is False
    assert reason is None


def test_a_failed_non_climate_constraint_still_eliminates():
    """The exemption is climate alone, not a general softening."""
    profile = PlaceRequestProfile(purpose="vacation", hard_constraints=["must feel safe"])

    eliminated, reason, _ = _check_hard_constraints(profile, {"safety": 0.05}, CANDIDATE, [])

    assert eliminated is True
    assert reason is not None


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("requires a car", "transportation"),
        ("renting a car", "transportation"),
        ("a car is needed", "transportation"),
        ("accessible public transport", "transportation"),
        ("lack of accessibility", "terrain"),
    ],
)
def test_phrasings_that_previously_matched_nothing_now_match(phrase, expected):
    assert expected in criteria_for_constraint(phrase)


def test_bare_accessible_still_does_not_reach_terrain():
    """The noun is matched, not the adjective: bare "accessible" among the
    terrain triggers is what made elevation the headline of a prompt about snow
    and cafes (D46)."""
    assert criteria_for_constraint("accessible from the airport") == ["accessibility"]


def test_the_car_trigger_is_word_bounded():
    assert criteria_for_constraint("carnival season") == []
