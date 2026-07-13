"""Dynamic Evaluation: deterministic, purpose-driven scoring of candidates.

No LLM call. score(place) = sum(weight_i * normalized_rating_i) - uncertainty_penalty,
computed only over criteria that actually have evidence. Missing evidence is
never scored as a positive value, and hard-constraint violations eliminate a
candidate outright rather than merely lowering its score.
"""

from __future__ import annotations

from app.agent.models import CandidateEvaluation, CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult

DEFAULT_WEIGHTS: dict[str, float] = {
    "climate": 0.5,
    "work_infrastructure": 0.5,
    "cost": 0.5,
    "timezone": 0.5,
    "transportation": 0.4,
    "activities": 0.4,
    "student_life": 0.5,
    "education": 0.6,
    "accessibility": 0.4,
    "culture": 0.3,
    "nightlife": 0.3,
}

_CLIMATE_TARGETS: dict[str, tuple[float, float]] = {
    "warm": (22, 8),
    "hot": (32, 8),
    "cold": (5, 8),
    "mild": (18, 6),
    "sunny": (24, 10),
    "cool": (12, 8),
    "not extremely hot": (26, 6),
}

REQUIRED_TIMEZONE_OVERLAP_HOURS = 4.0


def _climate_rating(avg_high: float, climate_preferences: list[str]) -> float:
    matched = [_CLIMATE_TARGETS[w] for w in climate_preferences if w in _CLIMATE_TARGETS]
    if not matched:
        mid, spread = 22.0, 8.0
    else:
        mid = sum(m[0] for m in matched) / len(matched)
        spread = sum(m[1] for m in matched) / len(matched)
    return max(0.0, 1.0 - abs(avg_high - mid) / spread)


def _extract_criterion_scores(
    results: list[ToolResult], profile: PlaceRequestProfile
) -> tuple[dict[str, float], list[str], list[str]]:
    scores: dict[str, float] = {}
    advantages: list[str] = []
    drawbacks: list[str] = []

    for r in results:
        if r.error:
            continue
        nd = r.normalized_data

        if r.tool_name == "WeatherTool" and "avg_high_c" in nd:
            score = _climate_rating(nd["avg_high_c"], profile.climate_preferences)
            scores["climate"] = score
            (advantages if score >= 0.7 else drawbacks).append(
                f"Climate {'matches preferences well' if score >= 0.7 else 'may not closely match preferences'} "
                f"(avg high {nd['avg_high_c']}°C, {r.data_date or 'historical estimate'})."
            )

        elif r.tool_name == "AmenitiesTool":
            cats = nd.get("categories", [])
            count = nd.get("count", 0)
            if "coworking" in cats or "cafe" in cats:
                score = min(1.0, count / 10)
                scores["work_infrastructure"] = score
                if score >= 0.6:
                    advantages.append("Good density of coworking spaces/cafés nearby.")
            if "university" in cats or "library" in cats:
                scores["student_life"] = min(1.0, count / 5)
            if "public_transit" in cats or "train_station" in cats:
                scores["transportation"] = min(1.0, count / 10)
            if "park" in cats or "beach" in cats or "museum" in cats:
                scores.setdefault("activities", min(1.0, count / 8))

        elif r.tool_name == "ActivitiesTool":
            score = min(1.0, nd.get("count", 0) / 8)
            scores["activities"] = score
            if score >= 0.5:
                advantages.append("Good density of requested activities nearby.")

        elif r.tool_name == "BudgetFitTool":
            lower = nd.get("lower_monthly_estimate")
            upper = nd.get("upper_monthly_estimate")
            if (
                profile.budget.amount
                and profile.budget.period in ("monthly", "unknown")
                and lower is not None
                and upper is not None
            ):
                mid_estimate = (lower + upper) / 2
                if mid_estimate:
                    ratio = profile.budget.amount / mid_estimate
                    score = max(0.0, min(1.0, ratio))
                    scores["cost"] = score
                    currency = nd.get("currency", "")
                    if score < 0.6:
                        drawbacks.append(
                            f"Estimated cost (~{lower:.0f}-{upper:.0f} {currency}/month) may exceed your budget."
                        )
                    else:
                        advantages.append(
                            f"Estimated cost (~{lower:.0f}-{upper:.0f} {currency}/month) fits within your budget."
                        )

        elif r.tool_name == "TimezoneFitTool":
            overlap = nd.get("estimated_workday_overlap_hours")
            if overlap is not None:
                score = max(0.0, min(1.0, overlap / REQUIRED_TIMEZONE_OVERLAP_HOURS))
                scores["timezone"] = score
                if score >= 0.75:
                    advantages.append(f"Good working-hours overlap (~{overlap:.1f}h).")
                else:
                    drawbacks.append(f"Limited working-hours overlap (~{overlap:.1f}h).")

        elif r.tool_name == "AccessibilityTool":
            airports = nd.get("airports_within_50km", 0)
            stations = nd.get("train_stations_within_5km", 0)
            score = 0.8 if (airports >= 1 or stations >= 1) else 0.3
            if nd.get("likely_car_dependent"):
                score = min(score, 0.4)
            scores["accessibility"] = score

        elif r.tool_name == "EducationOptionsTool":
            score = nd.get("match_score")
            if score is not None:
                scores["education"] = score
                if nd.get("field_matched"):
                    advantages.append("A relevant university/program was identified.")
                else:
                    drawbacks.append("The specific academic field could not be confirmed for this city.")

        elif r.tool_name == "PlaceContextTool" and nd.get("excerpt"):
            advantages.append(nd["excerpt"][:150])

    return scores, advantages, drawbacks


def _check_hard_constraints(
    profile: PlaceRequestProfile, criterion_scores: dict[str, float], results: list[ToolResult]
) -> tuple[bool, str | None, dict[str, bool]]:
    hard_results: dict[str, bool] = {}
    eliminated = False
    reason: str | None = None

    budget_is_hard = any(
        ("budget" in hc.lower() or "afford" in hc.lower()) for hc in profile.hard_constraints
    ) or any("budget" in db.lower() for db in profile.deal_breakers)
    if budget_is_hard and "cost" in criterion_scores:
        within_limit = criterion_scores["cost"] >= 0.3
        hard_results["budget_within_limit"] = within_limit
        if not within_limit:
            eliminated = True
            reason = "Estimated cost substantially exceeds the required budget."

    car_free_required = "car-free" in profile.mobility_requirements or any(
        "car" in hc.lower() for hc in profile.hard_constraints
    )
    if car_free_required:
        accessibility_result = next((r for r in results if r.tool_name == "AccessibilityTool"), None)
        if accessibility_result is not None and not accessibility_result.error:
            likely_car_dependent = accessibility_result.normalized_data.get("likely_car_dependent")
            if likely_car_dependent is not None:
                hard_results["car_free_feasible"] = not likely_car_dependent
                if likely_car_dependent and not eliminated:
                    eliminated = True
                    reason = "This destination appears car-dependent, violating the car-free requirement."

    return eliminated, reason, hard_results


def evaluate_candidates(
    candidates: list[CandidatePlace],
    profile: PlaceRequestProfile,
    evidence_by_place: dict[str, list[ToolResult]],
) -> list[CandidateEvaluation]:
    evaluations: list[CandidateEvaluation] = []

    for candidate in candidates:
        results = evidence_by_place.get(candidate.place_name, [])
        criterion_scores, advantages, drawbacks = _extract_criterion_scores(results, profile)
        eliminated, elimination_reason, hard_constraint_results = _check_hard_constraints(
            profile, criterion_scores, results
        )

        missing_evidence = [c for c in profile.relevant_criteria if c not in criterion_scores]

        weights = dict(profile.inferred_weights)
        for c in criterion_scores:
            weights.setdefault(c, DEFAULT_WEIGHTS.get(c, 0.4))

        available_weights = {c: w for c, w in weights.items() if c in criterion_scores}
        weight_sum = sum(available_weights.values())
        normalized_weights = (
            {c: w / weight_sum for c, w in available_weights.items()} if weight_sum > 0 else {}
        )

        total_score = sum(normalized_weights.get(c, 0.0) * criterion_scores[c] for c in normalized_weights)

        high_weight_criteria = [c for c, w in weights.items() if w >= 0.7]
        missing_high = [c for c in high_weight_criteria if c not in criterion_scores]
        uncertainty_penalty = (
            0.15 * (len(missing_high) / len(high_weight_criteria)) if high_weight_criteria else 0.0
        )
        total_score = max(0.0, total_score - uncertainty_penalty)

        confidence_score = (
            sum(weights.get(c, 0) for c in criterion_scores) / sum(weights.values()) if weights else 0.0
        )

        if not drawbacks and not eliminated:
            drawbacks.append("No significant drawbacks were identified from the available evidence.")

        evaluations.append(
            CandidateEvaluation(
                place=candidate.place_name,
                country=candidate.country,
                criterion_scores=criterion_scores,
                criterion_weights=normalized_weights,
                total_score=round(total_score, 4),
                confidence_score=round(min(1.0, confidence_score), 4),
                hard_constraint_results=hard_constraint_results,
                missing_evidence=missing_evidence,
                advantages=advantages[:5],
                drawbacks=drawbacks[:5],
                eliminated=eliminated,
                elimination_reason=elimination_reason,
            )
        )

    evaluations.sort(key=lambda e: (e.eliminated, -e.total_score))
    return evaluations
