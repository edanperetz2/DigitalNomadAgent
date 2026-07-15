from datetime import UTC, datetime

import pytest

from app.agent.dynamic_evaluation import evaluate_candidates
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


def test_budget_hard_constraint_eliminates_candidate():
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
                {"lower_monthly_estimate": 3000.0, "upper_monthly_estimate": 4000.0, "currency": "USD"},
            )
        ]
    }
    evaluations = evaluate_candidates([expensive], profile, evidence)
    assert evaluations[0].eliminated is True
    assert evaluations[0].elimination_reason is not None


def test_car_free_hard_constraint_eliminates_car_dependent_candidate():
    profile = PlaceRequestProfile(
        purpose="remote_work",
        mobility_requirements=["car-free"],
        budget=Budget(),
    )
    candidate = _candidate("CarCity")
    evidence = {
        "CarCity": [
            _tool_result(
                "AccessibilityTool",
                "CarCity",
                {"airports_within_50km": 0, "train_stations_within_5km": 0, "likely_car_dependent": True},
            )
        ]
    }
    evaluations = evaluate_candidates([candidate], profile, evidence)
    assert evaluations[0].eliminated is True


def test_do_not_care_removes_criterion_weight():
    profile_caring = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["activities"],
        inferred_weights={"activities": 0.9},
        budget=Budget(),
    )
    profile_not_caring = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["warm"],
        inferred_weights={"climate": 0.9},
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {
        "City": [
            _tool_result("ActivitiesTool", "City", {"categories": ["beach"], "count": 10}),
            _tool_result("WeatherTool", "City", {"avg_high_c": 22.0}),
        ]
    }
    eval_caring = evaluate_candidates([candidate], profile_caring, evidence)[0]
    eval_not_caring = evaluate_candidates([candidate], profile_not_caring, evidence)[0]
    # Activities criterion only weighted heavily in the "caring" profile.
    assert eval_caring.criterion_weights.get("activities", 0) > eval_not_caring.criterion_weights.get("activities", 0)


def test_eliminated_candidates_sorted_last():
    profile = PlaceRequestProfile(
        purpose="remote_work",
        hard_constraints=["must stay within budget"],
        relevant_criteria=["cost"],
        budget=Budget(amount=100.0, period="monthly"),
    )
    good = _candidate("Affordable")
    bad = _candidate("TooExpensive")
    affordable_data = {"lower_monthly_estimate": 80.0, "upper_monthly_estimate": 100.0, "currency": "USD"}
    expensive_data = {"lower_monthly_estimate": 5000.0, "upper_monthly_estimate": 6000.0, "currency": "USD"}
    evidence = {
        "Affordable": [_tool_result("BudgetFitTool", "Affordable", affordable_data)],
        "TooExpensive": [_tool_result("BudgetFitTool", "TooExpensive", expensive_data)],
    }
    evaluations = evaluate_candidates([bad, good], profile, evidence)
    assert evaluations[-1].eliminated is True
    assert evaluations[0].place == "Affordable"


def test_scoring_is_deterministic():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["climate"],
        climate_preferences=["warm"],
        budget=Budget(),
    )
    candidate = _candidate("City")
    evidence = {"City": [_tool_result("WeatherTool", "City", {"avg_high_c": 24.0})]}
    result1 = evaluate_candidates([candidate], profile, evidence)[0]
    result2 = evaluate_candidates([candidate], profile, evidence)[0]
    assert result1.total_score == result2.total_score


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
