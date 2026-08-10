"""A stated non-negotiable must keep its weight, and must reach its criterion.

Two things came out of reading P06 on 2026-08-10, one fix and one reverted
experiment. Both are pinned here because the second is the more expensive
lesson.

**The fix.** The interpreter files the weight for "reasonably flat terrain"
under whichever word it chooses -- `terrain` on one run, `topography` on the
next. `topography` matched no pattern in the alias table, so a 0.95 requirement
scored at the 0.4 default and the answer announced "Not used in this ranking:
topography" directly above a body citing elevation evidence for it.

**The experiment, reverted.** Criteria that are also hard constraints were
capped at 0.5 on the reasoning that `constraint_tier` and
`confirmed_constraint_count` already sort on them, so weighting them again
counted them a third time and crowded out the traveller's stated preferences.
Live verification (`20260810T111045Z-postfix-gated-weights`) showed the cap let
climate (0.9, ungated) outweigh terrain and wheelchair accessibility (0.5), and
P06 returned **Lisbon first** for a wheelchair user -- its own answer conceding
the centre is hilly and step-free access unestablished. That is D34 and D55
recreated. The weight was carrying *how well* a place meets a requirement,
which the coarse tier/count gates do not express.
"""

from app.agent.dynamic_evaluation import (
    DEFAULT_WEIGHTS,
    _score_totals,
    canonical_criterion_name,
)

# What P06's interpreter returned on the two 2026-08-10 runs. It used a
# different word for the same criterion each time, which is the point.
P06_WEIGHTS_RUN_A = {
    "language": 0.95,
    "accessibility": 1.0,
    "public_transport": 0.95,
    "topography": 0.9,
    "healthcare": 0.75,
    "climate": 0.8,
}
P06_WEIGHTS_RUN_B = {
    "climate": 0.9,
    "language": 0.9,
    "mobility_accessibility": 1.0,
    "public_transport": 0.95,
    "terrain": 0.95,
    "healthcare": 0.7,
}


def test_topography_reaches_the_terrain_criterion():
    assert canonical_criterion_name("topography") == "terrain"
    assert canonical_criterion_name("Topographical") == "terrain"
    assert canonical_criterion_name("reasonably flat terrain") == "terrain"
    assert canonical_criterion_name("terrain") == "terrain"


def test_both_wordings_the_interpreter_used_land_on_the_same_criterion():
    assert canonical_criterion_name("topography") == canonical_criterion_name("terrain") == "terrain"


def test_mobility_accessibility_is_read_as_getting_around():
    """Recorded because it is surprising, not because it is wrong.

    `transportation` is matched before `accessibility` and owns the pattern
    "mobility", so the interpreter's `mobility_accessibility` weight lands on
    getting around the city rather than on `accessibility` -- which in this
    vocabulary means airport/arrival reach, not step-free access. For a
    wheelchair user asking about moving around a city, that is the better of the
    two. Pinned so a future edit to the ordered pattern table has to notice it.
    """
    assert canonical_criterion_name("mobility_accessibility") == "transportation"


def test_a_stated_terrain_weight_is_not_silently_replaced_by_the_default():
    scores = {"terrain": 0.8, "climate": 0.5}
    weights, _, _ = _score_totals(scores, {"topography": 0.9}, {})
    assert weights["terrain"] > weights["climate"]
    assert DEFAULT_WEIGHTS["terrain"] == 0.4


def test_a_non_negotiable_outweighs_a_preference_in_the_score():
    """The property the reverted cap destroyed.

    A wheelchair user's stated requirements for terrain and for getting around
    must not be outweighed by a climate preference, however warmly phrased.
    """
    scores = {"terrain": 0.5, "transportation": 0.5, "climate": 0.5}
    weights, _, _ = _score_totals(scores, P06_WEIGHTS_RUN_B, {})
    assert weights["terrain"] > weights["climate"]
    assert weights["transportation"] > weights["climate"]


def test_a_hilly_city_does_not_outrank_a_flat_one_on_climate_alone():
    """P06's live regression, as a unit test: Lisbon must not beat Dublin here."""
    hilly_but_warm = {"terrain": 0.25, "accessibility": 0.62, "climate": 0.95}
    flat_but_cool = {"terrain": 0.90, "accessibility": 0.70, "climate": 0.45}
    _, warm_total, _ = _score_totals(hilly_but_warm, P06_WEIGHTS_RUN_B, {})
    _, cool_total, _ = _score_totals(flat_but_cool, P06_WEIGHTS_RUN_B, {})
    assert cool_total > warm_total


def test_the_same_holds_under_the_other_runs_weight_vector():
    hilly_but_warm = {"terrain": 0.25, "accessibility": 0.62, "climate": 0.95}
    flat_but_cool = {"terrain": 0.90, "accessibility": 0.70, "climate": 0.45}
    _, warm_total, _ = _score_totals(hilly_but_warm, P06_WEIGHTS_RUN_A, {})
    _, cool_total, _ = _score_totals(flat_but_cool, P06_WEIGHTS_RUN_A, {})
    assert cool_total > warm_total


def test_score_totals_takes_no_constraint_argument():
    """The cap is gone, not merely disabled -- see this module's docstring."""
    import inspect

    params = inspect.signature(_score_totals).parameters
    assert "hard_constraint_results" not in params
