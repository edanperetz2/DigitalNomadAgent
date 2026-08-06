"""D53: "I don't care about nightlife at all" was recorded as a deal-breaker.

P01's interpreter wrote it into deal_breakers *and* weighted it 0.0 -- two
records that contradict each other. Harmless until D35 made a deal-breaker score
actively against a place; after that, a city gets marked down for something the
traveller merely shrugged at.
"""

from app.agent.models import PlaceRequestProfile
from app.agent.orchestrator import _drop_indifferent_deal_breakers
from app.agent.request_interpreter import SYSTEM_PROMPT


def test_a_zero_weighted_deal_breaker_is_dropped():
    profile = PlaceRequestProfile(
        purpose="remote_work",
        deal_breakers=["nightlife"],
        inferred_weights={"budget": 1.0, "nightlife": 0.0},
    )

    assert _drop_indifferent_deal_breakers(profile).deal_breakers == []


def test_a_genuine_avoidance_survives():
    """P04's "big party destinations" is a real deal-breaker and must keep
    scoring against a place."""
    profile = PlaceRequestProfile(
        purpose="vacation",
        deal_breakers=["big party destinations"],
        inferred_weights={"safety": 0.95, "walkability": 0.8},
    )

    assert _drop_indifferent_deal_breakers(profile).deal_breakers == ["big party destinations"]


def test_a_deal_breaker_with_no_stated_weight_is_left_alone():
    """Absence of a weight says nothing either way, so it is not evidence of
    indifference."""
    profile = PlaceRequestProfile(
        purpose="vacation", deal_breakers=["extreme heat"], inferred_weights={"safety": 0.9}
    )

    assert _drop_indifferent_deal_breakers(profile).deal_breakers == ["extreme heat"]


def test_only_the_indifferent_entry_is_removed():
    profile = PlaceRequestProfile(
        purpose="vacation",
        deal_breakers=["nightlife", "unsafe areas"],
        inferred_weights={"nightlife": 0.0, "safety": 0.95},
    )

    assert _drop_indifferent_deal_breakers(profile).deal_breakers == ["unsafe areas"]


def test_two_phrasings_of_one_indifferent_criterion_both_go():
    """"nightlife" and "big party destinations" are the same criterion, so a
    zero weight on it contradicts both."""
    profile = PlaceRequestProfile(
        purpose="vacation",
        deal_breakers=["nightlife", "big party destinations"],
        inferred_weights={"nightlife": 0.0},
    )

    assert _drop_indifferent_deal_breakers(profile).deal_breakers == []


def test_the_original_profile_is_not_mutated():
    profile = PlaceRequestProfile(
        purpose="remote_work", deal_breakers=["nightlife"], inferred_weights={"nightlife": 0.0}
    )

    _drop_indifferent_deal_breakers(profile)

    assert profile.deal_breakers == ["nightlife"]


def test_the_interpreter_is_told_indifference_is_not_avoidance():
    assert "Indifference is not avoidance" in SYSTEM_PROMPT
