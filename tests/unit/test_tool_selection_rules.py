from app.agent.agentic_research import select_tools
from app.agent.models import Budget, PlaceRequestProfile
from app.agent.orchestrator import _CRITERION_TO_TOOLS, Orchestrator


def _profile(**overrides) -> PlaceRequestProfile:
    defaults = dict(purpose="remote_work", budget=Budget())
    defaults.update(overrides)
    return PlaceRequestProfile(**defaults)


def test_tool_priorities_follow_user_weights_and_hard_constraints():
    profile = _profile(
        relevant_criteria=["cost", "activities"],
        inferred_weights={"cost": 0.9, "activities": 0.2},
        hard_constraints=["The monthly budget is non-negotiable."],
    )

    priorities = Orchestrator._tool_priorities(profile, {"BudgetFitTool", "ActivitiesTool"})

    assert priorities["BudgetFitTool"] == 2.0
    assert priorities["ActivitiesTool"] == 0.2


def test_remote_work_selects_work_infrastructure_tools():
    profile = _profile(purpose="remote_work")
    tools = select_tools(profile)
    assert "AmenitiesTool" in tools
    assert "BudgetFitTool" in tools
    assert "EducationOptionsTool" not in tools


def test_remote_work_selects_timezone_only_when_relevant():
    profile = _profile(purpose="remote_work")
    tools = select_tools(profile)
    assert "TimezoneFitTool" not in tools

    profile_with_overlap = _profile(purpose="remote_work", relevant_criteria=["timezone"])
    tools_with_overlap = select_tools(profile_with_overlap)
    assert "TimezoneFitTool" in tools_with_overlap


def test_study_selects_amenities_not_a_dedicated_education_tool():
    profile = _profile(purpose="study")
    tools = select_tools(profile)
    assert "AmenitiesTool" in tools
    assert "EducationOptionsTool" not in tools
    assert "TimezoneFitTool" not in tools


def test_vacation_selects_weather_and_activities():
    profile = _profile(purpose="vacation")
    tools = select_tools(profile)
    assert "WeatherTool" in tools
    assert "ActivitiesTool" in tools
    assert "WikivoyageClimateTool" not in tools


def test_explicit_amenity_preferences_select_amenities_for_vacation():
    profile = _profile(purpose="vacation", amenity_preferences=["cafe", "park"])

    assert "AmenitiesTool" in select_tools(profile)


def test_explicit_activity_preferences_select_activities_outside_vacation():
    profile = _profile(purpose="remote_work", activity_preferences=["hiking"])

    assert "ActivitiesTool" in select_tools(profile)


def test_explicit_climate_preferences_select_both_climate_tools():
    profile = _profile(purpose="remote_work", climate_preferences=["sunny", "dry"])

    tools = select_tools(profile)

    assert "WeatherTool" in tools
    assert "WikivoyageClimateTool" in tools


def test_climate_gap_research_retries_both_climate_sources():
    assert _CRITERION_TO_TOOLS["climate"] == {"WeatherTool", "WikivoyageClimateTool"}


def test_transportation_is_routed_only_to_local_mobility():
    assert _CRITERION_TO_TOOLS["transportation"] == {"LocalMobilityTool"}


def test_education_and_student_life_are_routed_to_amenities():
    assert _CRITERION_TO_TOOLS["education"] == {"AmenitiesTool"}
    assert _CRITERION_TO_TOOLS["student_life"] == {"AmenitiesTool"}


def test_safety_concerns_select_and_route_safety_tool():
    assert _CRITERION_TO_TOOLS["safety"] == {"SafetyTool"}
    for profile in (
        _profile(relevant_criteria=["safety"]),
        _profile(soft_preferences=["a safe place"]),
        _profile(deal_breakers=["high crime"]),
    ):
        assert "SafetyTool" in select_tools(profile)


def test_car_free_and_public_transport_select_local_mobility_not_arrival_access():
    profiles = [
        _profile(mobility_requirements=["car-free"]),
        _profile(relevant_criteria=["public transportation"]),
        _profile(soft_preferences=["walkable centre"]),
    ]

    for profile in profiles:
        tools = select_tools(profile)
        assert "LocalMobilityTool" in tools
        assert "TransportAccessTool" not in tools


def test_vacation_selects_transport_access_when_origin_present():
    profile = _profile(purpose="vacation", origin="Israel")
    tools = select_tools(profile)
    assert "TransportAccessTool" in tools


def test_arrival_and_remoteness_concerns_select_transport_access():
    for criterion in ("distance", "airport access", "not too remote", "easy to get there"):
        assert "TransportAccessTool" in select_tools(_profile(relevant_criteria=[criterion]))


def test_official_source_included_when_nationality_present():
    profile = _profile(purpose="vacation", nationality="Israeli")
    tools = select_tools(profile)
    assert "OfficialSourceTool" in tools


def test_tool_sets_differ_across_purposes():
    remote_tools = select_tools(_profile(purpose="remote_work"))
    study_tools = select_tools(_profile(purpose="study"))
    vacation_tools = select_tools(_profile(purpose="vacation"))
    assert remote_tools != study_tools
    assert study_tools != vacation_tools
    assert remote_tools != vacation_tools


def test_geocoding_always_included():
    for purpose in ("remote_work", "study", "vacation"):
        assert "GeocodingTool" in select_tools(_profile(purpose=purpose))


def test_place_context_is_deferred_for_all_purposes():
    profiles = [
        _profile(purpose="remote_work"),
        _profile(purpose="study"),
        _profile(purpose="vacation"),
        _profile(purpose="mixed", secondary_purposes=["remote_work", "study"]),
    ]

    assert all("PlaceContextTool" not in select_tools(profile) for profile in profiles)
