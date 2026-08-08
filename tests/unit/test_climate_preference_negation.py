"""A temperature the traveller rules out must never become the one they are scored against.

P06 asks to spend November-April "escaping the winter", "not housebound by
cold", and the interpreter recorded exactly that: `['mild winters', 'avoid
cold', 'not tropical heat']`. It resolved to a *cold* target of 5 C, because the
avoidance list matched "avoid freezing" but not "avoid cold", and the label loop
then found "cold" inside "avoid cold" by bare substring before it ever reached
"mild". The couple were given London, The Hague, Amsterdam, Dublin and Brussels,
with Singapore third -- the tropical heat they also ruled out -- while Lisbon,
the one mild-winter city researched, ranked last.
"""

from app.climate_scoring import climate_preference_directions

P06 = ["mild winters", "avoid cold", "not tropical heat"]


def test_p06_targets_mild_not_cold():
    """The defect exactly as it reached the deployment."""
    assert climate_preference_directions(P06)["temperature"] == "mild"


def test_p06_records_both_avoidances():
    directions = climate_preference_directions(P06)

    assert directions["freezing"] == "avoid"
    assert directions["extreme_heat"] == "avoid"


def test_an_avoided_temperature_never_becomes_the_target():
    """Whether it fired used to depend on where the word sat in CLIMATE_TARGETS:
    "somewhere warm, avoid cold" survived only because "warm" is enumerated
    before "cold"."""
    for phrasing in (["avoid cold"], ["not too cold"], ["escape the cold"], ["not cold"]):
        assert "temperature" not in climate_preference_directions(phrasing), phrasing


def test_avoiding_heat_is_recognised_however_it_is_worded():
    for phrasing in (["not tropical heat"], ["avoid extreme heat"], ["not too hot"], ["warm but not hot"]):
        assert climate_preference_directions(phrasing)["extreme_heat"] == "avoid", phrasing


def test_a_negation_does_not_reach_the_next_stated_preference():
    """Preferences arrive as separate statements. In "no snow | mild" the "no"
    governs the snow; reading it across the boundary silently dropped the
    temperature the traveller did ask for."""
    directions = climate_preference_directions(["no snow", "mild"])

    assert directions["temperature"] == "mild"
    assert directions["snow"] == "avoid"


def test_escaping_a_season_is_an_avoidance_not_a_request():
    directions = climate_preference_directions(["escaping the winter", "mild winters"])

    assert directions["temperature"] == "mild"


def test_a_requested_temperature_still_sets_the_target():
    """The fix must not make the ordinary case unreachable."""
    assert climate_preference_directions(["hot"])["temperature"] == "hot"
    assert climate_preference_directions(["cold"])["temperature"] == "cold"
    assert climate_preference_directions(["mild autumn weather"])["temperature"] == "mild"


def test_wanting_heat_while_capping_it_records_both():
    directions = climate_preference_directions(["somewhere hot but not too hot"])

    assert directions["temperature"] == "hot"
    assert directions["extreme_heat"] == "avoid"
