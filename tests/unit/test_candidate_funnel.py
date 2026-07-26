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
