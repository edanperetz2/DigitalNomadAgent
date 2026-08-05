from datetime import UTC, datetime

import pytest

from app.agent.dynamic_evaluation import (
    canonicalize_criterion_weights,
    check_geocoded_constraints,
    evaluate_candidates,
    unevidenced_criteria,
)
from app.agent.models import Budget, CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult


def _tool_result(tool_name: str, place: str, normalized_data: dict, **overrides) -> ToolResult:
    defaults = dict(
        tool_name=tool_name,
        place=place,
        normalized_data=normalized_data,
        source_name=f"{tool_name} source",
        retrieved_at=datetime.now(UTC),
        confidence="medium",
    )
    defaults.update(overrides)
    return ToolResult(**defaults)


def _candidate(name: str, country: str = "Testland") -> CandidatePlace:
    return CandidatePlace(
        place_name=name, country=country, reason_for_inclusion="test", verified=True, lat=1.0, lon=1.0
    )


def test_missing_evidence_is_excluded_not_scored():
    profile = PlaceRequestProfile(purpose="vacation", relevant_criteria=["climate"], budget=Budget())
    candidate = _candidate("Nowhere")
    evaluations = evaluate_candidates([candidate], profile, {"Nowhere": []})
    evaluation = evaluations[0]
    assert "climate" not in evaluation.criterion_scores
    assert "climate" in evaluation.missing_evidence
    assert evaluation.total_score == 0.0


def test_higher_amenity_count_scores_higher():
    profile = PlaceRequestProfile(purpose="remote_work", relevant_criteria=["work_infrastructure"], budget=Budget())
    good = _candidate("GoodCity")
    bad = _candidate("BadCity")
    evidence = {
        "GoodCity": [
            _tool_result(
                "AmenitiesTool",
                "GoodCity",
                {"counts_by_category": {"coworking": 5, "cafe": 25}, "partial": False},
            )
        ],
        "BadCity": [
            _tool_result(
                "AmenitiesTool",
                "BadCity",
                {"counts_by_category": {"coworking": 0, "cafe": 1}, "partial": False},
            )
        ],
    }
    evaluations = evaluate_candidates([good, bad], profile, evidence)
    scores = {e.place: e.total_score for e in evaluations}
    assert scores["GoodCity"] > scores["BadCity"]


def test_amenities_score_work_and_student_components_independently():
    profile = PlaceRequestProfile(
        purpose="mixed",
        secondary_purposes=["remote_work", "study"],
        relevant_criteria=["work_infrastructure", "student_life"],
        budget=Budget(),
    )
    candidate = _candidate("MixedCity")
    evidence = {
        "MixedCity": [
            _tool_result(
                "AmenitiesTool",
                "MixedCity",
                {
                    "counts_by_category": {
                        "coworking": 4,
                        "cafe": 30,
                        "university": 1,
                        "library": 3,
                        "public_transit": 1000,
                        "park": 100,
                    },
                    "partial": False,
                },
            )
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert evaluation.criterion_scores["work_infrastructure"] == 0.88
    assert evaluation.criterion_component_scores["work_infrastructure"] == {"coworking": 0.8, "cafe": 1.0}
    assert evaluation.criterion_scores["student_life"] == 0.3542
    assert "transportation" not in evaluation.criterion_scores
    assert "activities" not in evaluation.criterion_scores


def test_empty_partial_amenity_response_is_missing_not_zero_scored():
    profile = PlaceRequestProfile(
        purpose="remote_work", relevant_criteria=["work_infrastructure"], budget=Budget()
    )
    candidate = _candidate("PartialCity")
    evidence = {
        "PartialCity": [
            _tool_result(
                "AmenitiesTool",
                "PartialCity",
                {
                    "counts_by_category": {"coworking": 0, "cafe": 0},
                    "partial": True,
                    "valid_element_count": 0,
                },
                confidence="low",
            )
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert "work_infrastructure" not in evaluation.criterion_scores
    assert "work_infrastructure" in evaluation.missing_evidence


def test_unsupported_amenity_request_is_exposed_as_drawback():
    profile = PlaceRequestProfile(purpose="vacation", amenity_preferences=["hospital"], budget=Budget())
    candidate = _candidate("UnsupportedCity")
    evidence = {
        "UnsupportedCity": [
            _tool_result(
                "AmenitiesTool",
                "UnsupportedCity",
                {
                    "counts_by_category": {},
                    "unsupported_categories": ["hospital"],
                    "partial": False,
                    "valid_element_count": 0,
                },
                confidence="low",
            )
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert any("hospital" in drawback for drawback in evaluation.drawbacks)
    assert evaluation.criterion_scores == {}


def test_budget_hard_constraint_remains_unresolved_before_llm_reasoning():
    profile = PlaceRequestProfile(
        purpose="remote_work",
        relevant_criteria=["cost"],
        hard_constraints=["must stay within budget"],
        budget=Budget(amount=500.0, period="monthly"),
    )
    expensive = _candidate("ExpensiveCity")
    evidence = {
        "ExpensiveCity": [
            _tool_result(
                "BudgetFitTool",
                "ExpensiveCity",
                {
                    "evidence_level": "city",
                    "price_basket": [{"item": "1-bedroom apartment, center", "price_usd": 3000.0}],
                    "fixed_cost_scenarios": {
                        "center": {"monthly_total_usd": 3500.0, "local_currency": "USD"}
                    },
                    "scoring_status": "unresolved_pending_llm",
                },
            )
        ]
    }
    evaluations = evaluate_candidates([expensive], profile, evidence)
    assert evaluations[0].eliminated is False
    assert evaluations[0].hard_constraint_results == {}
    assert "cost" not in evaluations[0].criterion_scores
    assert evaluations[0].unscored_evidence == ["cost"]
    assert any("affordability scoring awaits" in drawback for drawback in evaluations[0].drawbacks)


def test_car_free_requirement_is_not_eliminated_before_llm_mobility_reasoning():
    profile = PlaceRequestProfile(
        purpose="remote_work",
        mobility_requirements=["car-free"],
        budget=Budget(),
    )
    candidate = _candidate("CarCity")
    evidence = {
        "CarCity": [
            _tool_result(
                "LocalMobilityTool",
                "CarCity",
                {
                    "counts_by_component": {
                        "bus_stops": 0,
                        "rail_metro_tram_stations": 0,
                        "pedestrian_ways": 0,
                        "cycleways": 0,
                    },
                    "wikivoyage_context": {"excerpt": "A car is commonly used."},
                    "scoring_status": "unresolved_pending_llm",
                },
            )
        ]
    }
    evaluations = evaluate_candidates([candidate], profile, evidence)
    assert evaluations[0].eliminated is False
    assert evaluations[0].hard_constraint_results == {}
    assert "transportation" not in evaluations[0].criterion_scores
    assert evaluations[0].unscored_evidence == ["transportation"]
    assert any("scoring awaits" in drawback for drawback in evaluations[0].drawbacks)


def test_transport_access_evidence_remains_unscored_before_llm_reasoning():
    profile = PlaceRequestProfile(
        purpose="vacation",
        origin="Tel Aviv",
        relevant_criteria=["accessibility"],
        inferred_weights={"accessibility": 0.9},
        budget=Budget(),
    )
    candidate = _candidate("ArrivalCity")
    evidence = {
        "ArrivalCity": [
            _tool_result(
                "TransportAccessTool",
                "ArrivalCity",
                {
                    "counts_by_component": {"airports_within_50km": 1},
                    "straight_line_distance_km": 2500.0,
                    "wikivoyage_context": {"preview_excerpt": "Several arrival options are described."},
                    "scoring_status": "unresolved_pending_llm",
                },
            )
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert evaluation.eliminated is False
    assert "accessibility" not in evaluation.criterion_scores
    assert evaluation.unscored_evidence == ["accessibility"]
    assert any("accessibility scoring awaits" in drawback for drawback in evaluation.drawbacks)


def test_activity_evidence_remains_unscored_before_llm_reasoning():
    profile = PlaceRequestProfile(
        purpose="vacation",
        activity_preferences=["culture", "hiking"],
        relevant_criteria=["activities"],
        inferred_weights={"activities": 0.9},
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {
        "City": [
            _tool_result(
                "ActivitiesTool",
                "City",
                {
                    "counts_by_category": {"culture": 10, "hiking": 2},
                    "wikivoyage_see_context": {"preview_excerpt": "Many sights."},
                    "wikivoyage_do_context": {"preview_excerpt": "Several hikes."},
                    "scoring_status": "unresolved_pending_llm",
                },
            ),
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert evaluation.eliminated is False
    assert "activities" not in evaluation.criterion_scores
    assert evaluation.unscored_evidence == ["activities"]
    assert any("activity scoring awaits" in drawback for drawback in evaluation.drawbacks)


def test_safety_composite_is_scored_with_visible_components():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["safety"],
        inferred_weights={"safety": 0.9},
        budget=Budget(),
    )
    candidate = _candidate("SafeCity")
    evidence = {
        "SafeCity": [
            _tool_result(
                "SafetyTool",
                "SafeCity",
                {
                    "composite_score": 0.82,
                    "component_scores": {
                        "fcdo_advisory": 1.0,
                        "homicide_rate": 0.75,
                        "wikivoyage_stay_safe": 0.70,
                    },
                    "available_component_count": 3,
                },
            )
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert evaluation.criterion_scores["safety"] == 0.82
    assert evaluation.criterion_component_scores["safety"] == {
        "fcdo_advisory": 1.0,
        "homicide_rate": 0.75,
        "wikivoyage_stay_safe": 0.70,
    }
    assert evaluation.confidence_score == 1.0
    assert any("not a universal" in advantage for advantage in evaluation.advantages)


def test_stale_or_single_source_safety_evidence_is_not_scored():
    profile = PlaceRequestProfile(purpose="vacation", relevant_criteria=["safety"], budget=Budget())
    stale_candidate = _candidate("StaleCity")
    single_candidate = _candidate("SingleCity")
    evidence = {
        "StaleCity": [
            _tool_result(
                "SafetyTool",
                "StaleCity",
                {
                    "composite_score": 0.9,
                    "component_scores": {"fcdo_advisory": 0.9, "homicide_rate": 0.9},
                    "available_component_count": 2,
                },
                stale=True,
            )
        ],
        "SingleCity": [
            _tool_result(
                "SafetyTool",
                "SingleCity",
                {
                    "composite_score": 1.0,
                    "component_scores": {"fcdo_advisory": 1.0},
                    "available_component_count": 1,
                },
                confidence="low",
            )
        ],
    }

    evaluations = evaluate_candidates([stale_candidate, single_candidate], profile, evidence)

    assert all("safety" not in evaluation.criterion_scores for evaluation in evaluations)
    assert all("safety" in evaluation.missing_evidence for evaluation in evaluations)


def test_scoring_is_deterministic():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["warm"],
        target_months=[7],
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {"City": [_tool_result("WeatherTool", "City", {"avg_high_c": 24.0})]}
    result1 = evaluate_candidates([candidate], profile, evidence)[0]
    result2 = evaluate_candidates([candidate], profile, evidence)[0]
    assert result1.total_score == result2.total_score


def test_climate_is_not_scored_when_the_request_pinned_no_months():
    """D31: WeatherTool falls back to the *current* calendar month when
    target_months is empty, so scoring its output answers a question about
    whatever month the run happens on. P04 asked about October and P06 about
    November-April; both were ranked on August climatology."""
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["warm"],
        target_months=[],
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {"City": [_tool_result("WeatherTool", "City", {"avg_high_c": 24.0})]}

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert "climate" not in evaluation.criterion_scores
    assert "climate" in evaluation.missing_evidence
    assert any("did not pin down when" in drawback for drawback in evaluation.drawbacks)


def test_weather_is_not_scored_without_explicit_climate_preferences():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {
        "City": [
            _tool_result(
                "WeatherTool",
                "City",
                {
                    "avg_high_c": 22.0,
                    "mean_relative_humidity_pct": 50.0,
                    "rainy_day_frequency": 0.1,
                },
            )
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert "climate" not in evaluation.criterion_scores
    assert evaluation.criterion_component_scores == {}
    assert "climate" in evaluation.missing_evidence


def test_climate_score_averages_only_requested_available_dimensions():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["warm", "low humidity", "sunny", "calm"],
        target_months=[7],
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {
        "City": [
            _tool_result(
                "WeatherTool",
                "City",
                {
                    "avg_high_c": 22.0,
                    "mean_relative_humidity_pct": 80.0,
                    "sunshine_fraction_of_daylight": 0.8,
                    "p95_daily_max_wind_gust_kmh": 50.0,
                    "snow_day_frequency": 0.5,
                },
            )
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert evaluation.criterion_component_scores["climate"] == {
        "temperature": 1.0,
        "humidity": 0.0,
        "sunshine": 1.0,
        "wind": 0.5,
    }
    assert evaluation.criterion_scores["climate"] == pytest.approx(0.625)
    assert "snow" not in evaluation.criterion_component_scores["climate"]


def test_missing_requested_climate_dimension_is_missing_not_zero():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["warm", "low humidity"],
        target_months=[7],
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {
        "City": [
            _tool_result(
                "WeatherTool",
                "City",
                {"avg_high_c": 22.0, "mean_relative_humidity_pct": None},
            )
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert evaluation.criterion_component_scores["climate"] == {"temperature": 1.0}
    assert evaluation.criterion_scores["climate"] == 1.0
    assert evaluation.confidence_score == 0.5
    assert any("humidity" in drawback for drawback in evaluation.drawbacks)


def test_negated_heat_preference_scores_extreme_heat_without_requesting_hot_weather():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["not extremely hot"],
        target_months=[7],
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {
        "City": [
            _tool_result(
                "WeatherTool",
                "City",
                {"avg_high_c": 32.0, "extreme_heat_frequency": 0.1},
            )
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert evaluation.criterion_component_scores["climate"] == {"extreme_heat": 0.5}
    assert evaluation.eliminated is False


def test_wikivoyage_climate_combines_at_twenty_percent_independent_of_result_order():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["warm", "low humidity"],
        target_months=[7],
        budget=Budget(),
    )
    candidate = _candidate("City")
    weather = _tool_result(
        "WeatherTool",
        "City",
        {"avg_high_c": 22.0, "mean_relative_humidity_pct": 80.0},
    )
    wikivoyage = _tool_result(
        "WikivoyageClimateTool",
        "City",
        {"component_scores": {"temperature": 0.5, "humidity": 1.0}},
    )

    forward = evaluate_candidates([candidate], profile, {"City": [weather, wikivoyage]})[0]
    reverse = evaluate_candidates([candidate], profile, {"City": [wikivoyage, weather]})[0]

    assert forward.criterion_component_scores["climate"] == {"humidity": 0.2, "temperature": 0.9}
    assert forward.criterion_scores["climate"] == pytest.approx(0.55)
    assert forward == reverse


def test_climate_source_contradiction_reduces_confidence_and_is_exposed():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["warm"],
        target_months=[7],
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {
        "City": [
            _tool_result("WeatherTool", "City", {"avg_high_c": 22.0}),
            _tool_result(
                "WikivoyageClimateTool",
                "City",
                {"component_scores": {"temperature": 0.0}},
            ),
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert evaluation.criterion_component_scores["climate"] == {"temperature": 0.8}
    assert evaluation.confidence_score == 0.75
    assert any("disagree on: temperature" in drawback for drawback in evaluation.drawbacks)


def test_wikivoyage_only_climate_score_is_low_confidence_secondary_evidence():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["sunny"],
        target_months=[7],
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {
        "City": [
            _tool_result(
                "WikivoyageClimateTool",
                "City",
                {"component_scores": {"sunshine": 0.8}},
            )
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert evaluation.criterion_scores["climate"] == 0.8
    assert evaluation.confidence_score == 0.5
    assert evaluation.eliminated is False
    assert any("Only secondary Wikivoyage" in drawback for drawback in evaluation.drawbacks)


def test_stale_wikivoyage_climate_is_not_used_for_scoring():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["warm"],
        target_months=[7],
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {
        "City": [
            _tool_result("WeatherTool", "City", {"avg_high_c": 22.0}),
            _tool_result(
                "WikivoyageClimateTool",
                "City",
                {"component_scores": {"temperature": 0.0}},
                stale=True,
            ),
        ]
    }

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert evaluation.criterion_component_scores["climate"] == {"temperature": 1.0}
    assert evaluation.confidence_score == 1.0
    assert not any("disagree" in drawback for drawback in evaluation.drawbacks)


def test_place_context_excerpt_is_not_treated_as_an_advantage():
    profile = PlaceRequestProfile(purpose="remote_work", budget=Budget())
    candidate = _candidate("City")
    excerpt = "City is a scenic destination, but this generic description does not establish request fit."
    evidence = {"City": [_tool_result("PlaceContextTool", "City", {"excerpt": excerpt})]}

    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert all(excerpt[:30] not in advantage for advantage in evaluation.advantages)
    assert evaluation.criterion_scores == {}


def _geocoded_candidate(name: str, country: str, country_code: str) -> CandidatePlace:
    return CandidatePlace(
        place_name=name,
        country=country,
        reason_for_inclusion="test",
        verified=True,
        country_code=country_code,
    )


def test_check_geocoded_constraints_eliminates_excluded_region():
    profile = PlaceRequestProfile(purpose="vacation", excluded_regions=["France"])
    candidate = _geocoded_candidate("Nice", "France", "FR")
    eliminated, reason = check_geocoded_constraints(profile, candidate)
    assert eliminated is True
    assert "excluded" in reason.lower()


def test_check_geocoded_constraints_matches_by_country_code_too():
    profile = PlaceRequestProfile(purpose="vacation", excluded_regions=["FR"])
    candidate = _geocoded_candidate("Nice", "France", "FR")
    eliminated, _ = check_geocoded_constraints(profile, candidate)
    assert eliminated is True


def test_check_geocoded_constraints_eliminates_outside_preferred_region():
    profile = PlaceRequestProfile(purpose="vacation", preferred_regions=["Spain"])
    candidate = _geocoded_candidate("Nice", "France", "FR")
    eliminated, reason = check_geocoded_constraints(profile, candidate)
    assert eliminated is True
    assert "preferred" in reason.lower()


def test_check_geocoded_constraints_passes_matching_preferred_region():
    profile = PlaceRequestProfile(purpose="vacation", preferred_regions=["France"])
    candidate = _geocoded_candidate("Nice", "France", "FR")
    eliminated, reason = check_geocoded_constraints(profile, candidate)
    assert eliminated is False
    assert reason is None


def test_check_geocoded_constraints_fails_open_without_country_identity():
    profile = PlaceRequestProfile(purpose="vacation", excluded_regions=["France"])
    candidate = CandidatePlace(place_name="Unknown", country="", reason_for_inclusion="test")
    eliminated, reason = check_geocoded_constraints(profile, candidate)
    assert eliminated is False
    assert reason is None


def test_budget_hard_constraint_eliminates_after_llm_scoring():
    from app.agent.dynamic_evaluation import apply_llm_scores

    profile = PlaceRequestProfile(
        purpose="remote_work",
        relevant_criteria=["cost"],
        hard_constraints=["must stay within budget"],
        budget=Budget(amount=500.0, period="monthly"),
    )
    expensive = _candidate("ExpensiveCity")
    evidence = {
        "ExpensiveCity": [
            _tool_result(
                "BudgetFitTool",
                "ExpensiveCity",
                {
                    "evidence_level": "city",
                    "price_basket": [{"item": "1-bedroom apartment, center", "price_usd": 3000.0}],
                    "fixed_cost_scenarios": {
                        "center": {"monthly_total_usd": 3500.0, "local_currency": "USD"}
                    },
                    "scoring_status": "unresolved_pending_llm",
                },
            )
        ]
    }
    # A companion that passes, so elimination is observable: when every
    # candidate fails the same bar the field is relaxed rather than emptied
    # (D28), and a single-candidate fixture would only ever show that path.
    affordable = _candidate("CheapCity")
    evidence["CheapCity"] = evidence["ExpensiveCity"]
    candidates = [expensive, affordable]
    evaluations = evaluate_candidates(candidates, profile, evidence)
    llm_scores = {
        "ExpensiveCity": {"cost": (0.05, "Far over the stated budget.")},
        "CheapCity": {"cost": (0.9, "Comfortably inside the stated budget.")},
    }

    updated = {
        e.place: e for e in apply_llm_scores(evaluations, candidates, profile, evidence, llm_scores)
    }

    assert updated["ExpensiveCity"].eliminated is True
    assert "cost" in updated["ExpensiveCity"].elimination_reason
    assert updated["ExpensiveCity"].criterion_scores["cost"] == 0.05
    assert "cost" not in updated["ExpensiveCity"].unscored_evidence
    assert updated["CheapCity"].eliminated is False


def test_llm_scoring_does_not_eliminate_when_score_is_affordable():
    from app.agent.dynamic_evaluation import apply_llm_scores

    profile = PlaceRequestProfile(
        purpose="remote_work",
        relevant_criteria=["cost"],
        hard_constraints=["must stay within budget"],
        budget=Budget(amount=500.0, period="monthly"),
    )
    cheap = _candidate("CheapCity")
    evidence = {
        "CheapCity": [
            _tool_result(
                "BudgetFitTool",
                "CheapCity",
                {
                    "evidence_level": "city",
                    "fixed_cost_scenarios": {
                        "center": {"monthly_total_usd": 400.0, "local_currency": "USD"}
                    },
                    "scoring_status": "unresolved_pending_llm",
                },
            )
        ]
    }
    evaluations = evaluate_candidates([cheap], profile, evidence)
    llm_scores = {"CheapCity": {"cost": (0.8, "Comfortably within budget.")}}

    updated = apply_llm_scores(evaluations, [cheap], profile, evidence, llm_scores)

    assert updated[0].eliminated is False
    assert updated[0].criterion_scores["cost"] == 0.8
    assert updated[0].total_score > 0


def test_apply_llm_scores_leaves_already_eliminated_candidates_untouched():
    from app.agent.dynamic_evaluation import apply_llm_scores

    profile = PlaceRequestProfile(purpose="vacation", excluded_regions=["France"])
    candidate = _geocoded_candidate("Nice", "France", "FR")
    evaluations = evaluate_candidates([candidate], profile, {})
    assert evaluations[0].eliminated is True

    updated = apply_llm_scores(
        evaluations, [candidate], profile, {}, {"Nice": {"cost": (0.9, "irrelevant")}}
    )
    assert updated[0].eliminated is True
    assert updated[0].criterion_scores == {}


def test_canonicalize_maps_observed_real_interpreter_keys():
    # Keys the real LLM emitted in the 2026-08-04 verification run.
    weights = canonicalize_criterion_weights(
        {
            "time_zone_overlap": 1.0,
            "internet_speed": 0.95,
            "airport_connectivity": 0.85,
            "budget": 0.7,
            "car_free_livability": 0.9,
            "walkability/terrain": 0.8,
        }
    )
    assert weights["timezone"] == 1.0
    assert weights["work_infrastructure"] == 0.95
    assert weights["accessibility"] == 0.85
    assert weights["cost"] == 0.7
    assert weights["transportation"] == 0.9


def test_canonicalize_keeps_exact_and_unknown_keys():
    weights = canonicalize_criterion_weights({"nightlife": 0.0, "city_size": 0.5, "english_prevalence": 0.9})
    assert weights["nightlife"] == 0.0
    assert weights["city_size"] == 0.5
    assert weights["english_prevalence"] == 0.9


def test_canonicalize_takes_strongest_weight_on_collision():
    weights = canonicalize_criterion_weights({"walkability": 0.85, "public transport access": 0.75})
    assert weights == {"transportation": 0.85}


def test_free_form_timezone_weight_drives_ranking():
    """A user's 1.0 weight on time_zone_overlap must outrank a strong amenity score."""
    profile = PlaceRequestProfile(
        purpose="remote_work",
        relevant_criteria=["timezone", "work_infrastructure"],
        inferred_weights={"time_zone_overlap": 1.0, "internet_and_coworking": 0.1},
        budget=Budget(),
    )
    good_tz = _candidate("GoodOverlap")
    good_amenities = _candidate("GoodAmenities")
    evidence = {
        "GoodOverlap": [
            _tool_result("TimezoneFitTool", "GoodOverlap", {"estimated_workday_overlap_hours": 6.0}),
            _tool_result(
                "AmenitiesTool",
                "GoodOverlap",
                {"counts_by_category": {"coworking": 0, "cafe": 1}, "partial": False},
            ),
        ],
        "GoodAmenities": [
            _tool_result("TimezoneFitTool", "GoodAmenities", {"estimated_workday_overlap_hours": 1.0}),
            _tool_result(
                "AmenitiesTool",
                "GoodAmenities",
                {"counts_by_category": {"coworking": 5, "cafe": 25}, "partial": False},
            ),
        ],
    }
    evaluations = evaluate_candidates([good_tz, good_amenities], profile, evidence)
    assert evaluations[0].place == "GoodOverlap"


def test_scored_criteria_are_not_reported_missing_under_free_form_names():
    """The 2026-08-05 run reported every criterion missing while scoring all of them.

    `relevant_criteria` is interpreter prose and `criterion_scores` is the
    scoring vocabulary, so the two only line up once canonicalized.
    """
    profile = PlaceRequestProfile(
        purpose="remote_work",
        # Verbatim from the P05 profile of validation_runs/20260805T051351Z.
        relevant_criteria=[
            "time_zone_overlap",
            "internet_quality",
            "airport_connectivity",
            "budget",
        ],
        budget=Budget(),
    )
    candidate = _candidate("Lisbon")
    evidence = {
        "Lisbon": [
            _tool_result("TimezoneFitTool", "Lisbon", {"estimated_workday_overlap_hours": 3.0}),
            _tool_result(
                "AmenitiesTool",
                "Lisbon",
                {"counts_by_category": {"coworking": 5, "cafe": 25}, "partial": False},
            ),
        ]
    }
    evaluation = evaluate_candidates([candidate], profile, evidence)[0]

    assert "timezone" in evaluation.criterion_scores
    assert "work_infrastructure" in evaluation.criterion_scores
    assert "time_zone_overlap" not in evaluation.missing_evidence
    assert "internet_quality" not in evaluation.missing_evidence
    # Nothing evidenced these two, so they are still reported -- in the user's wording.
    assert evaluation.missing_evidence == ["airport_connectivity", "budget"]


def _timezone_profile(*hard_constraints: str) -> PlaceRequestProfile:
    return PlaceRequestProfile(
        purpose="remote_work",
        hard_constraints=list(hard_constraints),
        relevant_criteria=["time_zone_overlap"],
        inferred_weights={"time_zone_overlap": 1.0},
        budget=Budget(),
    )


def _overlap_evidence(place: str, hours: float) -> dict[str, list[ToolResult]]:
    return {
        place: [
            _tool_result("TimezoneFitTool", place, {"estimated_workday_overlap_hours": hours})
        ]
    }


def test_stated_overlap_minimum_eliminates_a_candidate_that_misses_it():
    """P05 asked for four hours; Lisbon gave ~3.0h and was still recommended first.

    The score (3.0/4.0 = 0.75) clears the 0.2 elimination threshold easily, so
    only comparing the hours themselves catches this.
    """
    profile = _timezone_profile("at least four hours of overlap with US Eastern")
    evidence = _overlap_evidence("Lisbon", 3.0) | _overlap_evidence("Guadalajara", 6.0)
    evaluations = {
        e.place: e
        for e in evaluate_candidates([_candidate("Lisbon"), _candidate("Guadalajara")], profile, evidence)
    }

    assert evaluations["Lisbon"].eliminated is True
    assert evaluations["Lisbon"].hard_constraint_results["timezone"] is False
    assert "3.0h" in evaluations["Lisbon"].elimination_reason
    assert evaluations["Guadalajara"].eliminated is False
    assert evaluations["Guadalajara"].hard_constraint_results["timezone"] is True


def _overlap_verdicts(profile: PlaceRequestProfile, hours: dict[str, float]) -> dict[str, bool]:
    """Each place's timezone hard-constraint verdict, whatever the field does as a whole."""
    evidence: dict[str, list[ToolResult]] = {}
    for place, value in hours.items():
        evidence |= _overlap_evidence(place, value)
    evaluations = evaluate_candidates([_candidate(p) for p in hours], profile, evidence)
    return {e.place: e.hard_constraint_results["timezone"] for e in evaluations}


def test_digits_and_number_words_both_state_a_minimum():
    verdicts = _overlap_verdicts(
        _timezone_profile("must have 6 hours of overlap with London"),
        {"Lima": 5.0, "Accra": 6.0},
    )
    assert verdicts == {"Lima": False, "Accra": True}


def test_a_requirement_without_a_figure_falls_back_to_the_scoring_bar():
    profile = _timezone_profile("must have working hours overlap with the team")
    verdicts = _overlap_verdicts(profile, {"Lima": 3.9, "Oslo": 4.1})
    assert verdicts == {"Lima": False, "Oslo": True}


def test_an_unrelated_hours_constraint_does_not_supply_the_overlap_minimum():
    """P02 caps flight time in hours; that number must not become the overlap bar."""
    profile = _timezone_profile("no more than 5 hours flight from Tel Aviv")
    evaluation = evaluate_candidates([_candidate("Sofia")], profile, _overlap_evidence("Sofia", 1.0))[0]
    assert evaluation.eliminated is False
    assert "timezone" not in evaluation.hard_constraint_results


def test_an_overlap_no_candidate_can_meet_ranks_and_discloses_rather_than_failing():
    """Eliminating the whole field means no answer at all -- strictly worse.

    Mock P05's candidate set is entirely European, so none of it reaches four
    hours of US Eastern overlap; enforcing the minimum turned the request into
    "All candidate destinations were eliminated by hard constraints".
    """
    profile = _timezone_profile("four hours of overlap with us eastern")
    evidence = (
        _overlap_evidence("Lisbon", 3.0)
        | _overlap_evidence("Barcelona", 2.0)
        | _overlap_evidence("Taipei", 0.0)
    )
    places = [_candidate("Lisbon"), _candidate("Barcelona"), _candidate("Taipei")]
    evaluations = evaluate_candidates(places, profile, evidence)

    assert [e.eliminated for e in evaluations] == [False, False, False]
    # The failed check stays visible, and the shortfall leads the drawbacks.
    assert all(e.hard_constraint_results["timezone"] is False for e in evaluations)
    assert "short of the 4h" in evaluations[0].drawbacks[0]
    # Best available overlap still ranks first.
    assert evaluations[0].place == "Lisbon"


def test_a_candidate_still_loses_when_another_meets_the_overlap():
    """Relaxation is only for an unmeetable bar, not a merely demanding one."""
    profile = _timezone_profile("four hours of overlap with us eastern")
    evidence = _overlap_evidence("Lisbon", 3.0) | _overlap_evidence("Guadalajara", 6.0)
    evaluations = {
        e.place: e
        for e in evaluate_candidates(
            [_candidate("Lisbon"), _candidate("Guadalajara")], profile, evidence
        )
    }
    assert evaluations["Lisbon"].eliminated is True
    assert evaluations["Guadalajara"].eliminated is False


def test_overlap_minimum_never_eliminates_on_missing_evidence():
    """The tool timing out must not read as a failed constraint."""
    profile = _timezone_profile("at least four hours of overlap with US Eastern")
    evaluation = evaluate_candidates([_candidate("Nowhere")], profile, {"Nowhere": []})[0]
    assert evaluation.eliminated is False
    assert "timezone" not in evaluation.hard_constraint_results


def test_unevidenced_criteria_reports_each_criterion_once():
    """Two raw names for one criterion must not become two research targets."""
    assert unevidenced_criteria(["internet quality", "coworking availability"], {}) == [
        "internet quality"
    ]
    assert unevidenced_criteria(["cost of living", "climate"], {"cost": 0.5}) == ["climate"]


def test_amenity_and_safety_justifications_differ_between_places():
    """E1: five of P01's finalists carried the identical "why it fits", and all
    four of P03's carried the identical safety line. The numbers were there."""
    profile = PlaceRequestProfile(
        purpose="remote_work",
        relevant_criteria=["work_infrastructure", "safety"],
        budget=Budget(),
    )

    def amenities(place: str, coworking: int, cafe: int) -> ToolResult:
        return _tool_result(
            "AmenitiesTool",
            place,
            {"counts_by_category": {"coworking": coworking, "cafe": cafe}, "partial": False},
        )

    def safety(place: str, composite: float) -> ToolResult:
        return _tool_result(
            "SafetyTool",
            place,
            {
                "composite_score": composite,
                "available_component_count": 2,
                "component_scores": {"advisory": composite, "crime_index": composite - 0.05},
            },
        )

    evidence = {
        "Krakow": [amenities("Krakow", 4, 337), safety("Krakow", 0.82)],
        "Barcelona": [amenities("Barcelona", 53, 967), safety("Barcelona", 0.74)],
    }
    evaluations = {
        e.place: e
        for e in evaluate_candidates([_candidate("Krakow"), _candidate("Barcelona")], profile, evidence)
    }

    krakow = " ".join(evaluations["Krakow"].advantages)
    barcelona = " ".join(evaluations["Barcelona"].advantages)

    assert "4 coworking, 337 cafe" in krakow
    assert "53 coworking, 967 cafe" in barcelona
    assert "0.82" in krakow and "0.74" in barcelona
    assert "advisory" in krakow and "crime index" in krakow
    # The whole point: a reader can tell the two apart.
    assert krakow != barcelona


def test_the_evidence_trail_uses_the_names_the_bibliography_uses():
    """A tool publishing explicit evidence items carries a source name per item,
    which need not equal the envelope's. Citing the envelope produced references
    that matched nothing, and those criteria dropped out of the trail silently."""
    from app.evidence.models import EvidenceItem, EvidenceSource

    profile = PlaceRequestProfile(
        purpose="remote_work", relevant_criteria=["work_infrastructure"], budget=Budget()
    )
    now = datetime.now(UTC)
    result = _tool_result(
        "AmenitiesTool",
        "Krakow",
        {"counts_by_category": {"coworking": 5, "cafe": 25}, "partial": False},
        source_name="AmenitiesTool envelope",
        evidence_items=[
            EvidenceItem(
                criterion="work_infrastructure",
                normalized_data={},
                source=EvidenceSource(
                    source_name="OpenStreetMap", retrieved_at=now, confidence="medium"
                ),
            )
        ],
    )

    evaluation = evaluate_candidates([_candidate("Krakow")], profile, {"Krakow": [result]})[0]

    assert evaluation.criterion_sources["work_infrastructure"] == ["OpenStreetMap"]
    assert "AmenitiesTool envelope" not in evaluation.criterion_sources["work_infrastructure"]


def test_an_unscored_criterion_is_never_given_a_citation():
    profile = PlaceRequestProfile(purpose="vacation", relevant_criteria=["climate"], budget=Budget())
    evaluation = evaluate_candidates([_candidate("Nowhere")], profile, {"Nowhere": []})[0]
    assert evaluation.criterion_sources == {}


def test_a_bar_no_candidate_can_meet_relaxes_whatever_criterion_it_was():
    """D28: this rule was timezone-only, and the 2026-08-05 full run showed why
    that was too narrow -- P08's Swedish candidates all failed a $400 budget and
    the request died outright with no answer at all."""
    from app.agent.dynamic_evaluation import apply_llm_scores

    profile = PlaceRequestProfile(
        purpose="remote_work",
        relevant_criteria=["cost"],
        hard_constraints=["budget must not exceed $400/month"],
        budget=Budget(amount=400.0, period="monthly"),
    )
    cities = ["Stockholm", "Uppsala", "Gothenburg"]
    evidence = {
        c: [
            _tool_result(
                "BudgetFitTool",
                c,
                {"evidence_level": "city", "scoring_status": "unresolved_pending_llm"},
            )
        ]
        for c in cities
    }
    candidates = [_candidate(c) for c in cities]
    evaluations = evaluate_candidates(candidates, profile, evidence)
    # Every one of them scores far under the elimination threshold.
    llm_scores = {c: {"cost": (0.02, "Far over the stated budget.")} for c in cities}

    updated = apply_llm_scores(evaluations, candidates, profile, evidence, llm_scores)

    assert [e.eliminated for e in updated] == [False, False, False]
    assert all(e.hard_constraint_results["cost"] is False for e in updated)
    assert all("hard constraint on cost" in e.drawbacks[0] for e in updated)


def test_a_place_the_user_named_is_never_eliminated():
    """D29: P09 asked "is Lisbon a good fit?" and got eight other cities plus
    "the available candidate data does not include Lisbon"."""
    profile = PlaceRequestProfile(
        purpose="remote_work",
        named_destinations=["Lisbon"],
        preferred_regions=["Scandinavia"],
        relevant_criteria=["cost"],
        hard_constraints=["budget must not exceed 100 USD/month"],
        budget=Budget(amount=100.0, period="monthly"),
    )
    lisbon = CandidatePlace(
        place_name="Lisbon", country="Portugal", reason_for_inclusion="named", verified=True
    )
    oslo = CandidatePlace(
        place_name="Oslo", country="Norway", reason_for_inclusion="t", verified=True
    )
    evidence = {
        place: [
            _tool_result(
                "BudgetFitTool",
                place,
                {"evidence_level": "city", "scoring_status": "unresolved_pending_llm"},
            )
        ]
        for place in ("Lisbon", "Oslo")
    }
    evaluations = evaluate_candidates([lisbon, oslo], profile, evidence)

    by_place = {e.place: e for e in evaluations}
    # Outside the requested region and failing the budget, and still present.
    assert by_place["Lisbon"].eliminated is False
    # The region genuinely does exclude it; that is a verdict, not a deletion.
    assert by_place["Oslo"].eliminated is False  # relaxed: nothing else survived


def test_a_named_place_survives_a_region_that_excludes_it():
    """The narrow case: other candidates are viable, so no relaxation applies,
    and the named place must still come through."""
    profile = PlaceRequestProfile(
        purpose="vacation", named_destinations=["Lisbon"], preferred_regions=["Scandinavia"]
    )
    lisbon = CandidatePlace(
        place_name="Lisbon", country="Portugal", reason_for_inclusion="named", verified=True
    )
    oslo = CandidatePlace(
        place_name="Oslo", country="Norway", reason_for_inclusion="t", verified=True
    )

    by_place = {e.place: e for e in evaluate_candidates([lisbon, oslo], profile, {})}

    assert by_place["Lisbon"].eliminated is False
    assert by_place["Oslo"].eliminated is False
