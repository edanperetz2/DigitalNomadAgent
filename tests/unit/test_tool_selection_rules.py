from app.agent.agentic_research import select_tools
from app.agent.models import Budget, PlaceRequestProfile


def _profile(**overrides) -> PlaceRequestProfile:
    defaults = dict(purpose="remote_work", budget=Budget())
    defaults.update(overrides)
    return PlaceRequestProfile(**defaults)


def test_remote_work_selects_work_infrastructure_tools_not_education():
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


def test_study_selects_education_tool_not_timezone():
    profile = _profile(purpose="study", study_field="data science")
    tools = select_tools(profile)
    assert "EducationOptionsTool" in tools
    assert "TimezoneFitTool" not in tools


def test_vacation_selects_weather_and_activities_not_education():
    profile = _profile(purpose="vacation")
    tools = select_tools(profile)
    assert "WeatherTool" in tools
    assert "ActivitiesTool" in tools
    assert "EducationOptionsTool" not in tools


def test_vacation_selects_accessibility_when_origin_present():
    profile = _profile(purpose="vacation", origin="Israel")
    tools = select_tools(profile)
    assert "AccessibilityTool" in tools


def test_official_source_included_when_nationality_present():
    profile = _profile(purpose="vacation", nationality="Israeli")
    tools = select_tools(profile)
    assert "OfficialSourceTool" in tools


def test_tool_sets_differ_across_purposes():
    remote_tools = select_tools(_profile(purpose="remote_work"))
    study_tools = select_tools(_profile(purpose="study", study_field="business"))
    vacation_tools = select_tools(_profile(purpose="vacation"))
    assert remote_tools != study_tools
    assert study_tools != vacation_tools
    assert remote_tools != vacation_tools


def test_geocoding_always_included():
    for purpose in ("remote_work", "study", "vacation"):
        kwargs = {"purpose": purpose}
        if purpose == "study":
            kwargs["study_field"] = "law"
        assert "GeocodingTool" in select_tools(_profile(**kwargs))


def test_place_context_is_deferred_for_all_purposes():
    profiles = [
        _profile(purpose="remote_work"),
        _profile(purpose="study", study_field="law"),
        _profile(purpose="vacation"),
        _profile(purpose="mixed", secondary_purposes=["remote_work", "study"]),
    ]

    assert all("PlaceContextTool" not in select_tools(profile) for profile in profiles)
