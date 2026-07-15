import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.tools.official_sources import DATA_PATH as OFFICIAL_SOURCES_DATA_PATH
from app.tools.official_sources import OfficialSourceTool


def _candidate(name, country="Portugal", lat=38.7, lon=-9.1):
    return CandidatePlace(place_name=name, country=country, reason_for_inclusion="t", verified=True, lat=lat, lon=lon)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not OFFICIAL_SOURCES_DATA_PATH.exists(), reason="production official-source dataset has not been supplied"
)
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
