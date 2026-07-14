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
    "cool": (12, 8),
}

REQUIRED_TIMEZONE_OVERLAP_HOURS = 4.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _scale(value: float, low: float, high: float) -> float:
    return _clamp((value - low) / (high - low))


def _requested_climate_dimensions(climate_preferences: list[str]) -> set[str]:
    preferences = " | ".join(preference.casefold() for preference in climate_preferences)
    if not preferences:
        return set()

    requested: set[str] = set()
    avoids_extreme_heat = any(
        phrase in preferences for phrase in ("not extremely hot", "avoid extreme heat", "not too hot")
    )
    avoids_freezing = any(phrase in preferences for phrase in ("avoid freezing", "no freezing", "not cold"))
    if any(
        label in preferences
        for label in _CLIMATE_TARGETS
        if not (label == "hot" and avoids_extreme_heat) and not (label == "cold" and avoids_freezing)
    ):
        requested.add("temperature")
    if avoids_extreme_heat:
        requested.add("extreme_heat")
    if avoids_freezing:
        requested.add("freezing")
    if any(phrase in preferences for phrase in ("low humidity", "not humid", "avoid humidity", "humid")):
        requested.add("humidity")
    if any(phrase in preferences for phrase in ("dry", "little rain", "avoid rain", "not rainy", "rainy")):
        requested.add("rain")
    if any(phrase in preferences for phrase in ("sunny", "sunshine", "cloudy")):
        requested.add("sunshine")
    if any(phrase in preferences for phrase in ("no snow", "avoid snow", "not snowy", "snowy", "snow")):
        requested.add("snow")
    if any(phrase in preferences for phrase in ("calm", "not windy", "avoid strong wind", "windy")):
        requested.add("wind")
    return requested


def _climate_component_scores(normalized_data: dict, climate_preferences: list[str]) -> dict[str, float]:
    """Score only climate dimensions explicitly requested by the user."""
    preferences = " | ".join(preference.casefold() for preference in climate_preferences)
    if not preferences:
        return {}

    scores: dict[str, float] = {}
    avg_high = normalized_data.get("avg_high_c")
    blocked_temperature_labels = set()
    if any(phrase in preferences for phrase in ("not extremely hot", "avoid extreme heat", "not too hot")):
        blocked_temperature_labels.add("hot")
    if any(phrase in preferences for phrase in ("avoid freezing", "no freezing", "not cold")):
        blocked_temperature_labels.add("cold")
    for label, (target, tolerance) in _CLIMATE_TARGETS.items():
        if label not in blocked_temperature_labels and label in preferences and isinstance(avg_high, (int, float)):
            scores["temperature"] = _clamp(1.0 - abs(float(avg_high) - target) / tolerance)
            break

    extreme_heat = normalized_data.get("extreme_heat_frequency")
    avoids_extreme_heat = any(
        phrase in preferences for phrase in ("not extremely hot", "avoid extreme heat", "not too hot")
    )
    if avoids_extreme_heat and isinstance(extreme_heat, (int, float)):
        scores["extreme_heat"] = 1.0 - _clamp(float(extreme_heat) / 0.20)

    freezing = normalized_data.get("freezing_night_frequency")
    avoids_freezing = any(phrase in preferences for phrase in ("avoid freezing", "no freezing", "not cold"))
    if avoids_freezing and isinstance(freezing, (int, float)):
        scores["freezing"] = 1.0 - _clamp(float(freezing) / 0.20)

    humidity = normalized_data.get("mean_relative_humidity_pct")
    if isinstance(humidity, (int, float)):
        if any(phrase in preferences for phrase in ("low humidity", "not humid", "avoid humidity")):
            scores["humidity"] = 1.0 - _scale(float(humidity), 50.0, 80.0)
        elif "humid" in preferences:
            scores["humidity"] = _scale(float(humidity), 50.0, 80.0)

    rainy_days = normalized_data.get("rainy_day_frequency")
    if isinstance(rainy_days, (int, float)):
        if any(phrase in preferences for phrase in ("dry", "little rain", "avoid rain", "not rainy")):
            scores["rain"] = 1.0 - _clamp(float(rainy_days) / 0.50)
        elif "rainy" in preferences:
            scores["rain"] = _clamp(float(rainy_days) / 0.50)

    sunshine = normalized_data.get("sunshine_fraction_of_daylight")
    if isinstance(sunshine, (int, float)):
        if any(phrase in preferences for phrase in ("sunny", "sunshine")):
            scores["sunshine"] = _scale(float(sunshine), 0.30, 0.80)
        elif "cloudy" in preferences:
            scores["sunshine"] = 1.0 - _scale(float(sunshine), 0.30, 0.80)

    snow_days = normalized_data.get("snow_day_frequency")
    if isinstance(snow_days, (int, float)):
        if any(phrase in preferences for phrase in ("no snow", "avoid snow", "not snowy")):
            scores["snow"] = 1.0 - _clamp(float(snow_days) / 0.20)
        elif any(phrase in preferences for phrase in ("snowy", "snow")):
            scores["snow"] = _clamp(float(snow_days) / 0.30)

    gust_p95 = normalized_data.get("p95_daily_max_wind_gust_kmh")
    if isinstance(gust_p95, (int, float)):
        if any(phrase in preferences for phrase in ("calm", "not windy", "avoid strong wind")):
            scores["wind"] = 1.0 - _scale(float(gust_p95), 30.0, 70.0)
        elif "windy" in preferences:
            scores["wind"] = _scale(float(gust_p95), 30.0, 70.0)

    return {name: round(score, 4) for name, score in scores.items()}


def _extract_criterion_scores(
    results: list[ToolResult], profile: PlaceRequestProfile
) -> tuple[dict[str, float], dict[str, dict[str, float]], list[str], list[str]]:
    scores: dict[str, float] = {}
    component_scores: dict[str, dict[str, float]] = {}
    advantages: list[str] = []
    drawbacks: list[str] = []

    for r in results:
        if r.error:
            continue
        nd = r.normalized_data

        if r.tool_name == "WeatherTool":
            requested_components = _requested_climate_dimensions(profile.climate_preferences)
            climate_components = _climate_component_scores(nd, profile.climate_preferences)
            missing_components = sorted(requested_components - climate_components.keys())
            if missing_components:
                drawbacks.append(
                    "Requested-season climate evidence is unavailable for: "
                    + ", ".join(missing_components)
                    + "."
                )
            if climate_components:
                score = sum(climate_components.values()) / len(climate_components)
                scores["climate"] = score
                component_scores["climate"] = climate_components
                component_names = ", ".join(climate_components)
                (advantages if score >= 0.7 else drawbacks).append(
                    "Requested-season climate "
                    f"{'matches preferences well' if score >= 0.7 else 'may not closely match preferences'} "
                    f"across {component_names} ({r.data_date or 'historical climatology'})."
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

    return scores, component_scores, advantages, drawbacks


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
        criterion_scores, criterion_component_scores, advantages, drawbacks = _extract_criterion_scores(
            results, profile
        )
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
        requested_climate_components = _requested_climate_dimensions(profile.climate_preferences)
        if "climate" in criterion_scores and requested_climate_components:
            climate_component_coverage = len(criterion_component_scores.get("climate", {})) / len(
                requested_climate_components
            )
            supported_weight -= weights.get("climate", 0) * (1.0 - climate_component_coverage)
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
