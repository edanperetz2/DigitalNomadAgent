from datetime import UTC, datetime

from app.agent.candidate_funnel import estimate_affordability, select_finalists
from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult


def _candidate(name, country="Spain", country_code="ES", importance=0.7):
    return CandidatePlace(
        place_name=name,
        country=country,
        reason_for_inclusion="t",
        verified=True,
        country_code=country_code,
        geocoding_importance=importance,
    )


def test_orchestrator_relaxes_a_preferred_region_that_matches_nothing():
    """The pipeline-level fix. Relaxing only inside select_finalists is not
    enough: _check_hard_constraints re-runs the same region check during
    scoring, so an unrelaxed profile just moves the failure downstream from
    "eliminated by region constraints" to "eliminated by hard constraints".
    """
    from app.agent.orchestrator import _relax_unresolvable_preferred_regions

    profile = PlaceRequestProfile(
        purpose="remote_work", preferred_regions=["Europe", "mid-sized city"]
    )
    candidates = [_candidate("Valencia"), _candidate("Porto", country="Portugal", country_code="PT")]

    relaxed = _relax_unresolvable_preferred_regions(profile, candidates)

    assert relaxed.preferred_regions == []
    assert any("region preference" in a for a in relaxed.assumptions), "relaxation must be disclosed"
    assert profile.preferred_regions == ["Europe", "mid-sized city"], "must not mutate the original"


def test_orchestrator_keeps_a_preferred_region_that_matches_something():
    from app.agent.orchestrator import _relax_unresolvable_preferred_regions

    profile = PlaceRequestProfile(purpose="remote_work", preferred_regions=["Spain"])
    candidates = [_candidate("Valencia"), _candidate("Porto", country="Portugal", country_code="PT")]

    relaxed = _relax_unresolvable_preferred_regions(profile, candidates)

    assert relaxed.preferred_regions == ["Spain"]
    assert relaxed.assumptions == []


def test_continental_preferred_region_does_not_eliminate_every_candidate():
    """"Somewhere in Europe" must not wipe out the whole field.

    check_geocoded_constraints only compares country identity, so a continent
    name matches nothing and previously eliminated every candidate -- which the
    orchestrator turned into a hard "all candidates eliminated by region
    constraints" failure for the flagship remote-work prompt.
    """
    profile = PlaceRequestProfile(purpose="remote_work", preferred_regions=["Europe"])
    candidates = [_candidate("Valencia"), _candidate("Porto", country="Portugal", country_code="PT")]

    finalists = select_finalists(candidates, profile, {}, max_finalists=8)

    assert {c.place_name for c in finalists} == {"Valencia", "Porto"}


def test_non_geographic_preferred_region_does_not_eliminate_every_candidate():
    """The interpreter has been observed emitting preferred_regions=
    ["Europe", "mid-sized city"]; a size preference is not a region."""
    profile = PlaceRequestProfile(
        purpose="remote_work", preferred_regions=["Europe", "mid-sized city"]
    )
    candidates = [_candidate("Valencia")]

    finalists = select_finalists(candidates, profile, {}, max_finalists=8)

    assert [c.place_name for c in finalists] == ["Valencia"]


def test_country_level_preferred_region_still_filters_when_some_candidates_match():
    """The relaxation must not weaken a preference that genuinely resolves."""
    profile = PlaceRequestProfile(purpose="vacation", preferred_regions=["Spain"])
    candidates = [_candidate("Valencia"), _candidate("Porto", country="Portugal", country_code="PT")]

    finalists = select_finalists(candidates, profile, {}, max_finalists=8)

    assert [c.place_name for c in finalists] == ["Valencia"]


def test_excluded_region_still_eliminates_absolutely():
    """excluded_regions is a stated deal-breaker: it must never be relaxed,
    even when it removes every candidate."""
    profile = PlaceRequestProfile(purpose="vacation", excluded_regions=["Spain"])
    candidates = [_candidate("Valencia"), _candidate("Seville")]

    assert select_finalists(candidates, profile, {}, max_finalists=8) == []


def _budget_result(place, *, status, remaining=None, monthly_total=None, error=None):
    if error:
        return ToolResult(
            tool_name="BudgetFitTool",
            place=place,
            source_name="t",
            retrieved_at=datetime.now(UTC),
            confidence="low",
            error=error,
        )
    scenarios = {}
    if remaining is not None and monthly_total is not None:
        scenarios["center"] = {
            "monthly_total_usd": monthly_total,
            "budget_remaining_after_named_items": {"amount": remaining, "currency": "USD"},
        }
    return ToolResult(
        tool_name="BudgetFitTool",
        place=place,
        source_name="t",
        retrieved_at=datetime.now(UTC),
        confidence="medium",
        normalized_data={
            "budget_context": {"status": status, "comparison_amount": None},
            "fixed_cost_scenarios": scenarios,
            "country_context": {},
        },
    )


def test_estimate_affordability_neutral_when_budget_not_provided():
    result = _budget_result("Valencia", status="not_provided")
    assert estimate_affordability(result.normalized_data) == 1.0


def test_estimate_affordability_neutral_on_missing_or_error_evidence():
    assert estimate_affordability(None) == 0.5
    assert estimate_affordability({}) == 0.5


def test_estimate_affordability_scores_comfortably_under_budget_higher():
    comfortable = _budget_result("Valencia", status="comparable_without_conversion", remaining=500, monthly_total=1000)
    tight = _budget_result("Valencia", status="comparable_without_conversion", remaining=50, monthly_total=1000)
    over_budget = _budget_result("Valencia", status="comparable_without_conversion", remaining=-500, monthly_total=1000)
    comfortable_score = estimate_affordability(comfortable.normalized_data)
    tight_score = estimate_affordability(tight.normalized_data)
    over_budget_score = estimate_affordability(over_budget.normalized_data)
    assert comfortable_score > tight_score > over_budget_score
    assert over_budget_score < 0.5


def test_select_finalists_excludes_region_and_ranks_by_composite():
    profile = PlaceRequestProfile(purpose="vacation", excluded_regions=["France"])
    candidates = [
        _candidate("Nice", country="France", country_code="FR", importance=0.9),
        _candidate("Valencia", country="Spain", country_code="ES", importance=0.6),
        _candidate("Kotor", country="Montenegro", country_code="ME", importance=0.5),
    ]
    budget_results = {
        "Valencia": [_budget_result("Valencia", status="not_provided")],
        "Kotor": [_budget_result("Kotor", status="not_provided")],
    }
    finalists = select_finalists(candidates, profile, budget_results, max_finalists=5)
    names = [c.place_name for c in finalists]
    assert "Nice" not in names
    assert names == ["Valencia", "Kotor"]


def test_select_finalists_caps_at_max_finalists():
    profile = PlaceRequestProfile(purpose="vacation")
    candidates = [_candidate(f"Place{i}", importance=1.0 - i * 0.01) for i in range(10)]
    finalists = select_finalists(candidates, profile, {}, max_finalists=3)
    assert len(finalists) == 3
    assert [c.place_name for c in finalists] == ["Place0", "Place1", "Place2"]
