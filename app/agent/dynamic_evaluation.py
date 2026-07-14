"""Dynamic Evaluation: deterministic, purpose-driven scoring of candidates.

No LLM call. score(place) = sum(weight_i * normalized_rating_i) - uncertainty_penalty,
computed only over criteria that actually have evidence. Missing evidence is
never scored as a positive value, and hard-constraint violations eliminate a
candidate outright rather than merely lowering its score.
"""

from __future__ import annotations

from app.agent.models import CandidateEvaluation, CandidatePlace, PlaceRequestProfile
from app.climate_scoring import clamp, requested_climate_dimensions, weather_component_scores
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

REQUIRED_TIMEZONE_OVERLAP_HOURS = 4.0
WIKIVOYAGE_CLIMATE_WEIGHT = 0.20
CLIMATE_CONTRADICTION_GAP = 0.50


def _climate_evaluation(
    results: list[ToolResult], profile: PlaceRequestProfile
) -> tuple[dict[str, float], list[str], list[str], float]:
    requested = requested_climate_dimensions(profile.climate_preferences)
    if not requested:
        return {}, [], [], 0.0

    weather_result = next(
        (result for result in results if result.tool_name == "WeatherTool" and not result.error), None
    )
    wikivoyage_result = next(
        (
            result
            for result in results
            if result.tool_name == "WikivoyageClimateTool" and not result.error and not result.stale
        ),
        None,
    )
    weather_scores = (
        weather_component_scores(weather_result.normalized_data, profile.climate_preferences)
        if weather_result
        else {}
    )
    raw_wikivoyage_scores = (
        wikivoyage_result.normalized_data.get("component_scores", {}) if wikivoyage_result else {}
    )
    if not isinstance(raw_wikivoyage_scores, dict):
        raw_wikivoyage_scores = {}
    wikivoyage_scores = {
        name: clamp(float(value))
        for name, value in raw_wikivoyage_scores.items()
        if name in requested and isinstance(value, (int, float))
    }

    combined: dict[str, float] = {}
    source_support: dict[str, float] = {}
    contradictions: list[str] = []
    wikivoyage_only: list[str] = []
    for component in sorted(requested):
        weather_score = weather_scores.get(component)
        wikivoyage_score = wikivoyage_scores.get(component)
        if weather_score is not None and wikivoyage_score is not None:
            combined[component] = round(
                (1.0 - WIKIVOYAGE_CLIMATE_WEIGHT) * weather_score
                + WIKIVOYAGE_CLIMATE_WEIGHT * wikivoyage_score,
                4,
            )
            source_support[component] = 1.0
            if abs(weather_score - wikivoyage_score) >= CLIMATE_CONTRADICTION_GAP:
                contradictions.append(component)
        elif weather_score is not None:
            combined[component] = weather_score
            source_support[component] = 1.0
        elif wikivoyage_score is not None:
            combined[component] = round(wikivoyage_score, 4)
            source_support[component] = 0.5
            wikivoyage_only.append(component)

    advantages: list[str] = []
    drawbacks: list[str] = []
    missing = sorted(requested - combined.keys())
    if missing:
        drawbacks.append("Requested-season climate evidence is unavailable for: " + ", ".join(missing) + ".")
    if wikivoyage_only:
        drawbacks.append(
            "Only secondary Wikivoyage climate evidence was available for: "
            + ", ".join(wikivoyage_only)
            + "."
        )
    if contradictions:
        drawbacks.append("Open-Meteo and Wikivoyage climate evidence disagree on: " + ", ".join(contradictions) + ".")
    if combined:
        score = sum(combined.values()) / len(combined)
        component_names = ", ".join(combined)
        data_date = weather_result.data_date if weather_result else wikivoyage_result.data_date
        (advantages if score >= 0.7 else drawbacks).append(
            "Requested-season climate "
            f"{'matches preferences well' if score >= 0.7 else 'may not closely match preferences'} "
            f"across {component_names} ({data_date or 'climate evidence'})."
        )

    support_factor = sum(source_support.values()) / len(requested)
    if contradictions:
        support_factor *= 0.75
    return combined, advantages, drawbacks, support_factor


def _extract_criterion_scores(
    results: list[ToolResult], profile: PlaceRequestProfile
) -> tuple[dict[str, float], dict[str, dict[str, float]], list[str], list[str], dict[str, float]]:
    scores: dict[str, float] = {}
    component_scores: dict[str, dict[str, float]] = {}
    advantages: list[str] = []
    drawbacks: list[str] = []
    confidence_factors: dict[str, float] = {}

    climate_components, climate_advantages, climate_drawbacks, climate_support = _climate_evaluation(
        results, profile
    )
    if climate_components:
        scores["climate"] = sum(climate_components.values()) / len(climate_components)
        component_scores["climate"] = climate_components
        confidence_factors["climate"] = climate_support
    advantages.extend(climate_advantages)
    drawbacks.extend(climate_drawbacks)

    for r in results:
        if r.error:
            continue
        nd = r.normalized_data

        if r.tool_name in {"WeatherTool", "WikivoyageClimateTool"}:
            continue
        if r.tool_name == "AmenitiesTool":
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

    return scores, component_scores, advantages, drawbacks, confidence_factors


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
        (
            criterion_scores,
            criterion_component_scores,
            advantages,
            drawbacks,
            confidence_factors,
        ) = _extract_criterion_scores(results, profile)
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

        supported_weight = sum(weights.get(c, 0) for c in criterion_scores)
        for criterion, factor in confidence_factors.items():
            supported_weight -= weights.get(criterion, 0) * (1.0 - factor)
        confidence_score = supported_weight / sum(weights.values()) if weights else 0.0

        if not drawbacks and not eliminated:
            drawbacks.append("No significant drawbacks were identified from the available evidence.")

        evaluations.append(
            CandidateEvaluation(
                place=candidate.place_name,
                country=candidate.country,
                criterion_scores=criterion_scores,
                criterion_component_scores=criterion_component_scores,
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
