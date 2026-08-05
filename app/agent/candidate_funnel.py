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


def budget_comparison(normalized_data: dict | None) -> tuple[float, float, str] | None:
    """(monthly estimate, budget remaining after it, currency) from BudgetFitTool.

    None when there is no stated budget or nothing comparable to compare it
    against. A negative `remaining` means the evidence puts this place over
    budget, and by how much -- which is what lets the orchestrator say plainly
    that nothing researched fits, rather than only scoring it low.

    The currency travels with the figure because the estimate is not always in
    USD: `monthly_total_local` is in the place's own currency, so a caller that
    prints "USD" unconditionally would misreport it.

    Reuses BudgetFitTool's already-computed comparison rather than re-deriving
    currency conversion here.
    """
    if not normalized_data:
        return None

    data = normalized_data
    budget_context = data.get("budget_context") or {}
    status = budget_context.get("status")
    if status not in ("converted_to_usd", "comparable_without_conversion"):
        return None

    scenarios = data.get("fixed_cost_scenarios") or {}
    scenario = scenarios.get("center") or scenarios.get("outside_center") or {}
    monthly_total = scenario.get("monthly_total_usd")
    currency = "USD"
    if not monthly_total:
        monthly_total = scenario.get("monthly_total_local")
        currency = budget_context.get("comparison_currency") or ""
    remaining_info = scenario.get("budget_remaining_after_named_items")
    remaining = remaining_info.get("amount") if remaining_info else None

    if remaining is None or not monthly_total:
        country_context = data.get("country_context") or {}
        monthly_total = country_context.get("monthly_estimate_usd")
        currency = "USD"
        comparison_amount = budget_context.get("comparison_amount")
        if monthly_total and comparison_amount is not None:
            remaining = comparison_amount - monthly_total

    if remaining is None or not monthly_total:
        return None
    return float(monthly_total), float(remaining), currency


def estimate_affordability(normalized_data: dict | None) -> float:
    """0-1 affordability score from BudgetFitTool's own budget comparison.

    0.5 is neutral (evidence that can't be meaningfully compared); the score
    rises toward 1.0 when comfortably under budget and falls toward 0.0 when
    over it. Takes the plain normalized_data dict (not a ToolResult) so
    MockLLMClient's deterministic Dynamic Evaluation stand-in can reuse this
    exact logic.
    """
    if not normalized_data:
        return 0.5
    status = (normalized_data.get("budget_context") or {}).get("status")
    if status in (None, "not_provided"):
        return 1.0

    comparison = budget_comparison(normalized_data)
    if comparison is None:
        return 0.5
    monthly_total, remaining, _currency = comparison
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
    # A place the user explicitly named must survive to the finalists, even if
    # it ranks poorly -- they asked about it, and "no, and here is why" is a
    # legitimate answer. Without this the user's own city was eliminated and the
    # response silently answered a different question.
    named = {n.strip().casefold() for n in profile.named_destinations if n.strip()}
    pinned = [c for c in candidates if c.place_name.strip().casefold() in named]
    rest = [c for c in candidates if c.place_name.strip().casefold() not in named]

    survivors = _rank_survivors(rest, profile, budget_results)

    # check_geocoded_constraints can only compare *country* identity -- no
    # region-taxonomy dataset exists here to expand "Europe" into its member
    # countries. So a continental preference ("somewhere in Europe"), or a
    # non-geographic string the interpreter placed in preferred_regions
    # (observed: "mid-sized city"), matches no candidate's country and
    # eliminates the entire field, failing the whole request.
    #
    # A preference that excludes everything is far more likely an unresolvable
    # preference than a true "nothing qualifies", so retry ignoring it. This
    # matches the fail-open rule the rest of the pipeline follows for evidence
    # it cannot evaluate. excluded_regions is a stated deal-breaker and keeps
    # eliminating absolutely -- it is only ever matched against country
    # identity, which it can actually resolve.
    if not survivors and profile.preferred_regions:
        relaxed = profile.model_copy(update={"preferred_regions": []})
        survivors = _rank_survivors(rest, relaxed, budget_results)

    survivors.sort(key=lambda pair: pair[0], reverse=True)
    ranked = [candidate for _, candidate in survivors]
    return (pinned + ranked)[:max_finalists]


def _rank_survivors(
    candidates: list[CandidatePlace],
    profile: PlaceRequestProfile,
    budget_results: dict[str, list[ToolResult]],
) -> list[tuple[float, CandidatePlace]]:
    survivors: list[tuple[float, CandidatePlace]] = []
    for candidate in candidates:
        eliminated, _ = check_geocoded_constraints(profile, candidate)
        if eliminated:
            continue
        importance = clamp(candidate.geocoding_importance or 0.0)
        budget_result = (budget_results.get(candidate.place_name) or [None])[0]
        normalized_data = budget_result.normalized_data if budget_result and not budget_result.error else None
        affordability = estimate_affordability(normalized_data)
        composite = IMPORTANCE_WEIGHT * importance + AFFORDABILITY_WEIGHT * affordability
        survivors.append((composite, candidate))
    return survivors
