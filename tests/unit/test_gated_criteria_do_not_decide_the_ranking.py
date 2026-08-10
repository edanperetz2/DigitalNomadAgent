"""A non-negotiable gates the field; it must not also decide the order.

Found by reading P06 on 2026-08-10. A retired couple said mild winters were
"the main thing", then made accessibility, English and step-free transport
non-negotiable. The interpreter weighted those 0.95-1.0 and climate 0.8 -- all
correctly extracted -- so every English-speaking flat city cleared the gates and
then *also* collected almost all of the score for clearing them. The answer
ranked Manchester, London and Birmingham as a six-month winter escape while
conceding their climate was "only adequate", and no warm destination appeared.

The same criterion was being counted three times: `constraint_tier`,
`confirmed_constraint_count`, and again at full stated weight in `total_score`.
"""

from app.agent.dynamic_evaluation import (
    DEFAULT_WEIGHTS,
    GATED_CRITERION_WEIGHT_CAP,
    _score_totals,
    canonical_criterion_name,
)

# What P06's interpreter actually returned on 2026-08-10.
P06_WEIGHTS = {
    "language": 0.95,
    "accessibility": 1.0,
    "public_transport": 0.95,
    "topography": 0.9,
    "healthcare": 0.75,
    "climate": 0.8,
}
P06_GATED = {
    "accessibility": True,
    "language_spoken": True,
    "transportation": None,
    "terrain": True,
}


def test_topography_reaches_the_terrain_criterion():
    """The interpreter's own word for the criterion it was asked to weight."""
    assert canonical_criterion_name("topography") == "terrain"
    assert canonical_criterion_name("Topographical") == "terrain"
    # The wording the traveller used still works.
    assert canonical_criterion_name("reasonably flat terrain") == "terrain"


def test_a_stated_terrain_weight_is_not_silently_replaced_by_the_default():
    scores = {"terrain": 0.8, "climate": 0.5}
    weights, _, _ = _score_totals(scores, {"topography": 0.9}, {})
    # Normalized, so compare against climate's share rather than the raw 0.9.
    assert weights["terrain"] > weights["climate"]
    assert DEFAULT_WEIGHTS["terrain"] == 0.4


def test_a_gated_criterion_is_capped_but_never_dropped():
    scores = {"accessibility": 0.9, "climate": 0.5}
    weights, _, _ = _score_totals(scores, P06_WEIGHTS, {}, {"accessibility": True})
    assert weights["accessibility"] > 0.0, "a capped constraint still counts for something"
    assert weights["climate"] > weights["accessibility"], (
        "climate was stated as the main thing; accessibility is already gated"
    )


def test_climate_outweighs_the_gates_on_p06s_real_weights():
    """The exact vector that produced the Manchester ranking."""
    scores = {
        "accessibility": 0.8,
        "language_spoken": 1.0,
        "transportation": 0.8,
        "terrain": 0.9,
        "climate": 0.4,
    }
    weights, _, _ = _score_totals(scores, P06_WEIGHTS, {}, P06_GATED)
    assert weights["climate"] == max(weights.values()), (
        "among candidates that clear the gates, the stated preference must decide"
    )


def test_a_warm_city_now_outranks_a_cold_one_that_clears_the_same_gates():
    """The P06 shape: both clear the gates, the cold one clears them *better*.

    A native-English city with documented step-free transit outscores a warm one
    on all four gated criteria -- by 0.10-0.25 each -- while losing on climate by
    0.60. Uncapped, the four gates outvote the one thing the traveller called the
    main thing. Capped, they no longer do.
    """
    gates = dict(P06_GATED)
    cold = {
        "accessibility": 0.85,
        "language_spoken": 1.00,
        "transportation": 0.90,
        "terrain": 0.95,
        "climate": 0.30,
    }
    warm = {
        "accessibility": 0.70,
        "language_spoken": 0.75,
        "transportation": 0.80,
        "terrain": 0.85,
        "climate": 0.90,
    }

    # The shipped behaviour, which is the defect this test holds closed.
    _, cold_uncapped, _ = _score_totals(cold, P06_WEIGHTS, {})
    _, warm_uncapped, _ = _score_totals(warm, P06_WEIGHTS, {})
    assert cold_uncapped > warm_uncapped

    _, cold_total, _ = _score_totals(cold, P06_WEIGHTS, {}, gates)
    _, warm_total, _ = _score_totals(warm, P06_WEIGHTS, {}, gates)
    assert warm_total > cold_total


def test_a_warm_city_that_fails_the_gates_badly_still_loses():
    """The cap must not turn a non-negotiable into a suggestion.

    A hilly, poorly-served city is still the wrong answer for a wheelchair user,
    however mild its winters. (Constraint elimination and `constraint_tier` are
    the primary guards; this checks the score agrees rather than fighting them.)
    """
    cold = {
        "accessibility": 0.85,
        "language_spoken": 1.00,
        "transportation": 0.90,
        "terrain": 0.95,
        "climate": 0.30,
    }
    unsuitable = {
        "accessibility": 0.20,
        "language_spoken": 0.25,
        "transportation": 0.30,
        "terrain": 0.15,
        "climate": 0.95,
    }
    _, cold_total, _ = _score_totals(cold, P06_WEIGHTS, {}, P06_GATED)
    _, unsuitable_total, _ = _score_totals(unsuitable, P06_WEIGHTS, {}, P06_GATED)
    assert cold_total > unsuitable_total


def test_an_ungated_request_is_scored_exactly_as_before():
    """No hard constraints means no cap, so existing behaviour is untouched."""
    scores = {"cost": 0.7, "climate": 0.6}
    weights_none, total_none, _ = _score_totals(scores, {"cost": 0.9, "climate": 0.5}, {})
    weights_empty, total_empty, _ = _score_totals(scores, {"cost": 0.9, "climate": 0.5}, {}, {})
    assert weights_none == weights_empty
    assert total_none == total_empty
    assert weights_none["cost"] > weights_none["climate"]


def test_the_cap_only_touches_criteria_that_are_actually_gated():
    scores = {"cost": 0.7, "work_infrastructure": 0.6}
    weights, _, _ = _score_totals(
        scores, {"cost": 0.95, "work_infrastructure": 0.9}, {}, {"cost": True}
    )
    # work_infrastructure keeps its stated weight; cost is capped to 0.5.
    assert weights["work_infrastructure"] > weights["cost"]
    assert GATED_CRITERION_WEIGHT_CAP == 0.5
