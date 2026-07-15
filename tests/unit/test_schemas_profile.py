from app.agent.models import (
    Budget,
    CandidateEvaluation,
    CandidatePlace,
    PlaceRequestProfile,
    ValidationResult,
)


def test_budget_defaults():
    b = Budget()
    assert b.amount is None
    assert b.period == "unknown"
    assert b.confidence == "medium"


def test_place_request_profile_required_fields_present():
    profile = PlaceRequestProfile(purpose="remote_work")
    for field in (
        "purpose",
        "secondary_purposes",
        "duration",
        "dates_or_season",
        "target_months",
        "origin",
        "nationality",
        "preferred_regions",
        "excluded_regions",
        "preferred_languages",
        "mobility_requirements",
        "climate_preferences",
        "activity_preferences",
        "amenity_preferences",
        "budget",
        "hard_constraints",
        "soft_preferences",
        "deal_breakers",
        "relevant_criteria",
        "inferred_weights",
        "missing_information",
        "assumptions",
        "clarification_required",
        "clarification_question",
    ):
        assert hasattr(profile, field)


def test_place_request_profile_validates_target_months():
    import pytest
    from pydantic import ValidationError

    profile = PlaceRequestProfile(purpose="vacation", target_months=[1, 7, 12])
    assert profile.target_months == [1, 7, 12]

    with pytest.raises(ValidationError):
        PlaceRequestProfile(purpose="vacation", target_months=[0, 13])


def test_place_request_profile_rejects_extra_fields():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlaceRequestProfile(purpose="remote_work", unexpected_field="oops")


def test_candidate_place_defaults_unverified():
    c = CandidatePlace(place_name="Lisbon", country="Portugal", reason_for_inclusion="test")
    assert c.verified is False
    assert c.lat is None


def test_candidate_evaluation_defaults():
    e = CandidateEvaluation(place="Lisbon")
    assert e.eliminated is False
    assert e.total_score == 0.0
    assert e.criterion_component_scores == {}


def test_validation_result_requires_approved():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ValidationResult()
    v = ValidationResult(approved=True)
    assert v.should_research_again is False
