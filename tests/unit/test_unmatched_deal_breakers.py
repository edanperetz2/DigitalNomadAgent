"""A deal-breaker that matches no keyword must not simply vanish.

P04 said "I'd rather skip the big party destinations". The interpreter recorded
it, `criteria_for_constraint` returned [] because the keyword table has no
"party", and the deal-breaker loop only ever called `matched_criteria.update`.
It was never scored and never disclosed: the traveller was given eight cities
with no indication that the one thing they asked to avoid had gone unchecked.
That is the silent drop D61 closed for hard constraints, left open on this side.
"""

from app.agent.dynamic_evaluation import _check_hard_constraints, criteria_for_constraint
from app.agent.models import CandidatePlace, PlaceRequestProfile

CANDIDATE = CandidatePlace(
    place_name="Barcelona", country="Spain", reason_for_inclusion="lively coastal city"
)


def _results(profile, scores=None):
    _, _, hard_results = _check_hard_constraints(profile, scores or {}, CANDIDATE, [])
    return hard_results


def test_the_p04_deal_breaker_still_matches_nothing():
    """The fix is that an unmatched requirement is disclosed, not that this
    particular phrase is added to the table -- one keyword would fix one prompt."""
    assert criteria_for_constraint("big party destinations") == []


def test_an_unmatched_deal_breaker_is_recorded_as_unchecked():
    profile = PlaceRequestProfile(purpose="vacation", deal_breakers=["big party destinations"])

    assert _results(profile).get("avoiding big party destinations") is None
    assert "avoiding big party destinations" in _results(profile)


def test_an_unchecked_deal_breaker_never_eliminates():
    """None is "not confirmed", not "failed" -- an unmatched phrase must not
    empty the field (D24, D28)."""
    profile = PlaceRequestProfile(purpose="vacation", deal_breakers=["big party destinations"])

    eliminated, reason, _ = _check_hard_constraints(profile, {}, CANDIDATE, [])

    assert eliminated is False
    assert reason is None


def test_a_matched_deal_breaker_still_pulls_its_criterion_in():
    profile = PlaceRequestProfile(purpose="vacation", deal_breakers=["nightlife"])

    results = _results(profile, {"activities": 0.9})

    assert "activities" in results
    assert "avoiding nightlife" not in results


def test_a_deal_breaker_about_a_region_is_left_to_the_geocoded_check():
    """"nothing confirms not in Southeast Asia" is false when the field was
    filtered on exactly that."""
    profile = PlaceRequestProfile(
        purpose="vacation",
        excluded_regions=["Southeast Asia"],
        deal_breakers=["Southeast Asia"],
    )

    assert not any(key.startswith("avoiding") for key in _results(profile))


def test_a_bogus_region_does_not_swallow_the_requirement():
    """P04's real interpreter filed "party destinations" under excluded_regions.
    Geography cannot exclude that -- it resolves to no countries, so nothing was
    filtered -- and treating it as already-answered dropped the deal-breaker a
    second way, after the keyword table had already missed it."""
    profile = PlaceRequestProfile(
        purpose="vacation",
        excluded_regions=["party destinations"],
        deal_breakers=["big party destinations"],
    )

    assert "avoiding big party destinations" in _results(profile)


def test_a_country_named_as_a_region_is_still_left_to_the_geocoded_check():
    """resolve_region returns None for a bare country name too, but the
    geocoded check matches those directly."""
    profile = PlaceRequestProfile(
        purpose="vacation",
        preferred_regions=["Portugal"],
        deal_breakers=["Portugal"],
    )

    assert not any(key.startswith("avoiding") for key in _results(profile))
