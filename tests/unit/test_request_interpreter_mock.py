from app.agent.request_interpreter import SYSTEM_PROMPT
from app.llm.mock import interpret_prompt


def test_remote_work_prompt_detected():
    profile = interpret_prompt(
        "I want to spend three months somewhere in Europe where I can work remotely, "
        "live without a car, and stay within €1,800 per month."
    )
    assert profile["purpose"] == "remote_work"
    assert profile["budget"]["amount"] == 1800.0
    assert profile["budget"]["currency"] == "EUR"
    assert profile["budget"]["period"] == "monthly"
    assert "car-free" in profile["mobility_requirements"]
    assert profile["clarification_required"] is False


def test_study_prompt_with_field_detected():
    profile = interpret_prompt(
        "Recommend a city for a one-semester computer-science exchange. I care about "
        "student life, public transportation, safety, and affordable housing."
    )
    assert profile["purpose"] == "study"
    assert profile["study_field"] == "computer science"
    assert profile["clarification_required"] is False
    assert "transportation" in profile["relevant_criteria"]
    assert "safety" in profile["relevant_criteria"]


def test_study_prompt_without_field_requires_clarification():
    profile = interpret_prompt("I want to study abroad for a semester somewhere affordable.")
    assert profile["purpose"] == "study"
    assert profile["clarification_required"] is True
    assert profile["clarification_question"]


def test_vacation_prompt_detected():
    profile = interpret_prompt(
        "Find a quiet beach destination for two weeks in October, with warm but not "
        "extremely hot weather and good hiking nearby."
    )
    assert profile["purpose"] == "vacation"
    assert "not extremely hot" in profile["climate_preferences"]


def test_mixed_purpose_detected():
    profile = interpret_prompt(
        "I want to work remotely for six weeks while staying close to a beach and "
        "overlapping with Israeli working hours."
    )
    assert profile["purpose"] == "mixed"
    assert "remote_work" in profile["secondary_purposes"]
    assert "vacation" in profile["secondary_purposes"]
    assert profile["origin"] == "Israel"


def test_unknown_purpose_requires_clarification():
    profile = interpret_prompt("Surprise me.")
    assert profile["purpose"] == "unknown"
    assert profile["clarification_required"] is True
    assert profile["clarification_question"]


def test_budget_period_assumption_recorded():
    profile = interpret_prompt("I need a European city to study data science on €1,500 per month.")
    assert profile["budget"]["period"] == "monthly"
    assert profile["budget"]["amount"] == 1500.0


def test_amenity_preferences_are_inferred_and_negated_categories_are_excluded():
    profile = interpret_prompt(
        "I want to work remotely near coworking spaces, quiet cafés, a park, a gym, and a hospital, "
        "but I do not need pharmacies."
    )

    assert profile["amenity_preferences"] == [
        "coworking",
        "cafe",
        "park",
        "fitness_centre",
        "hospital",
    ]


def test_real_interpreter_contract_requests_normalized_amenity_preferences():
    assert "amenity_preferences" in SYSTEM_PROMPT
    for category in ("coworking", "cafe", "university", "library", "park", "pharmacy", "supermarket"):
        assert f'"{category}"' in SYSTEM_PROMPT
