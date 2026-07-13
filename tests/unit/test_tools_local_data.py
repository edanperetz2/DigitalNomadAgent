import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.tools.budget_fit import BudgetFitTool
from app.tools.education_options import EducationOptionsTool
from app.tools.official_sources import OfficialSourceTool
from app.tools.timezone_fit import TimezoneFitTool


def _candidate(name, country="Portugal", lat=38.7, lon=-9.1):
    return CandidatePlace(place_name=name, country=country, reason_for_inclusion="t", verified=True, lat=lat, lon=lon)


@pytest.mark.asyncio
async def test_budget_fit_tool_known_city():
    tool = BudgetFitTool()
    profile = PlaceRequestProfile(purpose="remote_work")
    result = await tool.run(_candidate("Lisbon"), profile)
    assert result.error is None
    assert result.normalized_data["lower_monthly_estimate"] > 0
    assert "sample" in " ".join(result.warnings).lower() or "sample" in result.source_name.lower()


@pytest.mark.asyncio
async def test_budget_fit_tool_unknown_city_returns_unknown_not_positive():
    tool = BudgetFitTool()
    profile = PlaceRequestProfile(purpose="remote_work")
    result = await tool.run(_candidate("Nonexistentville"), profile)
    assert result.error is not None
    assert result.normalized_data == {}


@pytest.mark.asyncio
async def test_education_options_tool_field_match():
    tool = EducationOptionsTool()
    profile = PlaceRequestProfile(purpose="study", study_field="computer science")
    result = await tool.run(_candidate("Berlin", country="Germany"), profile)
    assert result.normalized_data["field_matched"] is True
    assert result.normalized_data["match_score"] > 0.5


@pytest.mark.asyncio
async def test_education_options_tool_never_claims_admission():
    tool = EducationOptionsTool()
    profile = PlaceRequestProfile(purpose="study", study_field="astrology")
    result = await tool.run(_candidate("Berlin", country="Germany"), profile)
    assert result.normalized_data["field_matched"] is False
    assert any("could not be confirmed" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_official_source_tool_known_country():
    tool = OfficialSourceTool()
    profile = PlaceRequestProfile(purpose="study")
    result = await tool.run(_candidate("Berlin", country="Germany"), profile)
    assert result.error is None
    assert result.normalized_data["official_links"]
    assert any("verify" in w.lower() or "official" in w.lower() for w in result.warnings)


@pytest.mark.asyncio
async def test_official_source_tool_never_invents_visa_conclusion():
    tool = OfficialSourceTool()
    profile = PlaceRequestProfile(purpose="vacation")
    result = await tool.run(_candidate("Nowhereland", country="Nowhereland"), profile)
    assert result.error is not None


@pytest.mark.asyncio
async def test_timezone_fit_tool_unknown_origin():
    tool = TimezoneFitTool()
    profile = PlaceRequestProfile(purpose="remote_work")
    result = await tool.run(_candidate("Lisbon"), profile)
    assert "origin timezone is unknown" in " ".join(result.warnings).lower()


@pytest.mark.asyncio
async def test_timezone_fit_tool_known_origin_computes_overlap():
    tool = TimezoneFitTool()
    profile = PlaceRequestProfile(purpose="remote_work", origin="Israel")
    result = await tool.run(_candidate("Lisbon"), profile)
    assert result.error is None
    assert "estimated_workday_overlap_hours" in result.normalized_data


@pytest.mark.asyncio
async def test_timezone_fit_tool_requires_coordinates():
    tool = TimezoneFitTool()
    profile = PlaceRequestProfile(purpose="remote_work", origin="Israel")
    candidate = CandidatePlace(place_name="Unverified", country="X", reason_for_inclusion="t")
    result = await tool.run(candidate, profile)
    assert result.error is not None
