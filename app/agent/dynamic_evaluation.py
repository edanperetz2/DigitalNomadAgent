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
    "safety": 0.6,
}

REQUIRED_TIMEZONE_OVERLAP_HOURS = 4.0
WIKIVOYAGE_CLIMATE_WEIGHT = 0.20
CLIMATE_CONTRADICTION_GAP = 0.50
WORK_AMENITY_SATURATION = {"coworking": 5.0, "cafe": 25.0}
STUDENT_AMENITY_SATURATION = {"university": 3.0, "library": 8.0}


def _profile_purposes(profile: PlaceRequestProfile) -> set[str]:
    if profile.purpose == "mixed":
        return set(profile.secondary_purposes) or {"remote_work", "vacation"}
    return {profile.purpose}


def _amenity_component(counts: dict, category: str, saturation: float) -> float | None:
    count = counts.get(category)
    if not isinstance(count, (int, float)) or count < 0:
        return None
    return min(1.0, count / saturation)


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
            unsupported = nd.get("unsupported_categories", [])
            if isinstance(unsupported, list) and unsupported:
                drawbacks.append(
                    "Requested nearby categories could not be evaluated: "
                    + ", ".join(str(category) for category in unsupported)
                    + "."
                )
            counts = nd.get("counts_by_category", {})
            if not isinstance(counts, dict):
                continue
            if nd.get("partial") and nd.get("valid_element_count", 0) == 0:
                continue

            purposes = _profile_purposes(profile)
            support_factor = 0.5 if nd.get("partial") or r.confidence == "low" or r.stale else 1.0
            if "remote_work" in purposes or "work_infrastructure" in profile.relevant_criteria:
                coworking = _amenity_component(counts, "coworking", WORK_AMENITY_SATURATION["coworking"])
                cafe = _amenity_component(counts, "cafe", WORK_AMENITY_SATURATION["cafe"])
                if coworking is not None and cafe is not None:
                    score = round(0.6 * coworking + 0.4 * cafe, 4)
                    scores["work_infrastructure"] = score
                    component_scores["work_infrastructure"] = {
                        "coworking": round(coworking, 4),
                        "cafe": round(cafe, 4),
                    }
                    confidence_factors["work_infrastructure"] = support_factor
                    if score >= 0.6:
                        advantages.append("Good density of coworking spaces and cafes nearby.")
                    else:
                        drawbacks.append("Coworking and cafe evidence suggests limited work infrastructure nearby.")

            if "study" in purposes or "student_life" in profile.relevant_criteria:
                university = _amenity_component(
                    counts, "university", STUDENT_AMENITY_SATURATION["university"]
                )
                library = _amenity_component(counts, "library", STUDENT_AMENITY_SATURATION["library"])
                if university is not None and library is not None:
                    score = round((university + library) / 2, 4)
                    scores["student_life"] = score
                    component_scores["student_life"] = {
                        "university": round(university, 4),
                        "library": round(library, 4),
                    }
                    confidence_factors["student_life"] = support_factor
                    if score >= 0.6:
                        advantages.append("Good density of universities and libraries nearby.")
                    else:
                        drawbacks.append("University and library density appears limited nearby.")

        elif r.tool_name == "ActivitiesTool":
            limitation = (
                "Requested-category activity counts and Wikivoyage context were collected, "
                "but activity scoring awaits the LLM reasoning contract."
            )
            if limitation not in drawbacks:
                drawbacks.append(limitation)

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

        elif r.tool_name == "TransportAccessTool":
            limitation = (
                "Arrival-infrastructure counts, origin distance, and Wikivoyage context were collected, "
                "but accessibility scoring awaits the LLM reasoning contract."
            )
            if limitation not in drawbacks:
                drawbacks.append(limitation)

        elif r.tool_name == "LocalMobilityTool":
            limitation = (
                "Local mobility counts and Wikivoyage context were collected, but transportation "
                "scoring awaits the LLM reasoning contract."
            )
            if limitation not in drawbacks:
                drawbacks.append(limitation)

        elif r.tool_name == "SafetyTool":
            composite = nd.get("composite_score")
            component_count = nd.get("available_component_count")
            if (
                isinstance(composite, (int, float))
                and isinstance(component_count, int)
                and component_count >= 2
                and not r.stale
            ):
                score = clamp(float(composite))
                scores["safety"] = score
                raw_components = nd.get("component_scores", {})
                if isinstance(raw_components, dict):
                    component_scores["safety"] = {
                        name: clamp(float(value))
                        for name, value in raw_components.items()
                        if isinstance(value, (int, float))
                    }
                confidence_factors["safety"] = 1.0 if r.confidence == "medium" else 0.5
                description = (
                    "available safety evidence compares favorably"
                    if score >= 0.7
                    else "available safety evidence raises concerns"
                )
                (advantages if score >= 0.7 else drawbacks).append(
                    f"The {description}; this is comparative evidence, not a universal city-safety rating."
                )

    return scores, component_scores, advantages, drawbacks, confidence_factors


def _check_hard_constraints(
    profile: PlaceRequestProfile, criterion_scores: dict[str, float]
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
            profile, criterion_scores
        )

        missing_evidence = [c for c in profile.relevant_criteria if c not in criterion_scores]
        unresolved_tool_criteria = {
            "ActivitiesTool": "activities",
            "LocalMobilityTool": "transportation",
            "TransportAccessTool": "accessibility",
        }
        unscored_evidence = sorted(
            {
                unresolved_tool_criteria[result.tool_name]
                for result in results
                if result.tool_name in unresolved_tool_criteria
                and not result.error
                and result.normalized_data.get("scoring_status") == "unresolved_pending_llm"
            }
        )

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

        if not criterion_scores and not eliminated:
            drawbacks.append("No scored evidence was available; this candidate is provisional.")
        elif not drawbacks and not eliminated:
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
                unscored_evidence=unscored_evidence,
                advantages=advantages[:5],
                drawbacks=drawbacks[:5],
                eliminated=eliminated,
                elimination_reason=elimination_reason,
            )
        )

    evaluations.sort(key=lambda e: (e.eliminated, -e.total_score))
    return evaluations
