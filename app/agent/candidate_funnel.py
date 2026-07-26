"""Stage-2 candidate funnel: deterministic, zero-LLM filter/rank/select-top-N.

Sits between Stage 1 (bulk LLM recall, ~30 candidates) and Stage 3 (full agentic
tool suite, run only on the finalists this module selects). No lexical search
and no LLM call -- every signal used here is either already on the geocoded
CandidatePlace or a single BudgetFitTool result, both of which the orchestrator
already fetched cheaply at bulk scale before calling into this module.
"""

from __future__ import annotations

from app.agent.dynamic_evaluation import check_geocoded_constraints
from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.climate_scoring import clamp
from app.evidence.models import ToolResult

IMPORTANCE_WEIGHT = 0.6
AFFORDABILITY_WEIGHT = 0.4


def estimate_affordability(result: ToolResult | None) -> float:
    """0-1 affordability score from BudgetFitTool's own budget comparison.

    0.5 is neutral (no stated budget, or evidence that can't be meaningfully
    compared); the score rises toward 1.0 when comfortably under budget and
    falls toward 0.0 when over it. Reuses BudgetFitTool's already-computed
    comparison rather than re-deriving currency conversion here.
    """
    if result is None or result.error or not result.normalized_data:
        return 0.5

    data = result.normalized_data
    budget_context = data.get("budget_context") or {}
    status = budget_context.get("status")
    if status in (None, "not_provided"):
        return 1.0
    if status != "converted_to_usd" and status != "comparable_without_conversion":
        return 0.5

    scenarios = data.get("fixed_cost_scenarios") or {}
    scenario = scenarios.get("center") or scenarios.get("outside_center") or {}
    monthly_total = scenario.get("monthly_total_usd") or scenario.get("monthly_total_local")
    remaining_info = scenario.get("budget_remaining_after_named_items")
    remaining = remaining_info.get("amount") if remaining_info else None

    if remaining is None or not monthly_total:
        country_context = data.get("country_context") or {}
        monthly_total = country_context.get("monthly_estimate_usd")
        comparison_amount = budget_context.get("comparison_amount")
        if monthly_total and comparison_amount is not None:
            remaining = comparison_amount - monthly_total

    if remaining is None or not monthly_total:
        return 0.5

    ratio = max(-1.0, min(1.0, remaining / monthly_total))
    return clamp(0.5 + 0.5 * ratio)


def select_finalists(
    candidates: list[CandidatePlace],
    profile: PlaceRequestProfile,
    budget_results: dict[str, list[ToolResult]],
    *,
    max_finalists: int,
) -> list[CandidatePlace]:
    """Filter out region-excluded candidates, rank survivors, take the top N.

    Ranking composite: geocoding importance (how well-known/unambiguous the
    match was) weighted above budget affordability, since importance is a
    stronger signal that Stage 1's proposed place actually exists as described,
    while affordability is a softer preference signal refined further by the
    full Dynamic Evaluation scoring in Stage 3.
    """
    survivors: list[tuple[float, CandidatePlace]] = []
    for candidate in candidates:
        eliminated, _ = check_geocoded_constraints(profile, candidate)
        if eliminated:
            continue
        importance = clamp(candidate.geocoding_importance or 0.0)
        budget_result = (budget_results.get(candidate.place_name) or [None])[0]
        affordability = estimate_affordability(budget_result)
        composite = IMPORTANCE_WEIGHT * importance + AFFORDABILITY_WEIGHT * affordability
        survivors.append((composite, candidate))

    survivors.sort(key=lambda pair: pair[0], reverse=True)
    return [candidate for _, candidate in survivors[:max_finalists]]
