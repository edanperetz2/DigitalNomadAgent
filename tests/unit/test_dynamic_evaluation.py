from datetime import UTC, datetime

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
        "GoodCity": [_tool_result("AmenitiesTool", "GoodCity", {"categories": ["coworking"], "count": 20})],
        "BadCity": [_tool_result("AmenitiesTool", "BadCity", {"categories": ["coworking"], "count": 1})],
    }
    evaluations = evaluate_candidates([good, bad], profile, evidence)
    scores = {e.place: e.total_score for e in evaluations}
    assert scores["GoodCity"] > scores["BadCity"]


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
    profile = PlaceRequestProfile(purpose="vacation", relevant_criteria=["climate"], budget=Budget())
    candidate = _candidate("City")
    evidence = {"City": [_tool_result("WeatherTool", "City", {"avg_high_c": 24.0})]}
    result1 = evaluate_candidates([candidate], profile, evidence)[0]
    result2 = evaluate_candidates([candidate], profile, evidence)[0]
    assert result1.total_score == result2.total_score
