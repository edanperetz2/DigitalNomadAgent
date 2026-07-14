import asyncio
from datetime import UTC, datetime

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.main import _build_tool_registry
from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_candidate_geocoding_is_serial_even_with_larger_registry_limit():
    class TrackingGeocodingTool:
        def __init__(self):
            self.active = 0
            self.maximum_active = 0

        async def run(self, candidate, profile):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return ToolResult(
                tool_name="GeocodingTool",
                place=candidate.place_name,
                normalized_data={"lat": 1.0, "lon": 2.0},
                source_name="test",
                retrieved_at=datetime.now(UTC),
            )

    geocoding = TrackingGeocodingTool()
    registry = ToolRegistry({"GeocodingTool": geocoding}, max_concurrent_requests=5)
    candidates = [
        CandidatePlace(place_name=f"City {index}", country="Country", reason_for_inclusion="test")
        for index in range(3)
    ]

    verified, _ = await registry.verify_candidates(candidates, PlaceRequestProfile(purpose="vacation"))

    assert len(verified) == 3
    assert geocoding.maximum_active == 1


def test_production_registry_reuses_shared_clients():
    registry = _build_tool_registry(cache=object(), timeout=1.0, max_concurrent=5)

    geocoding = registry.get("GeocodingTool")
    weather = registry.get("WeatherTool")
    amenities = registry.get("AmenitiesTool")
    accessibility = registry.get("AccessibilityTool")
    activities = registry.get("ActivitiesTool")
    place_context = registry.get("PlaceContextTool")
    wikivoyage_climate = registry.get("WikivoyageClimateTool")

    assert geocoding._http is weather._http
    assert amenities._overpass is accessibility._overpass is activities._overpass
    assert place_context._mediawiki._http is geocoding._http
    assert wikivoyage_climate._mediawiki is place_context._mediawiki


@pytest.mark.asyncio
async def test_geocoding_failure_for_one_candidate_does_not_abort_the_rest():
    class PartlyFailingGeocodingTool:
        async def run(self, candidate, profile):
            if candidate.place_name == "Broken City":
                raise RuntimeError("malformed provider response")
            return ToolResult(
                tool_name="GeocodingTool",
                place=candidate.place_name,
                normalized_data={"lat": 1.0, "lon": 2.0},
                source_name="test",
                retrieved_at=datetime.now(UTC),
            )

    registry = ToolRegistry({"GeocodingTool": PartlyFailingGeocodingTool()})
    candidates = [
        CandidatePlace(place_name="Broken City", country="X", reason_for_inclusion="test"),
        CandidatePlace(place_name="Valid City", country="Y", reason_for_inclusion="test"),
    ]

    verified, results = await registry.verify_candidates(candidates, PlaceRequestProfile(purpose="vacation"))

    assert [candidate.place_name for candidate in verified] == ["Valid City"]
    assert "malformed provider response" in results[0].error
    assert results[1].error is None
