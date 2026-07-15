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
    local_mobility = registry.get("LocalMobilityTool")
    transport_access = registry.get("TransportAccessTool")
    activities = registry.get("ActivitiesTool")
    place_context = registry.get("PlaceContextTool")
    wikivoyage_climate = registry.get("WikivoyageClimateTool")
    timezone_fit = registry.get("TimezoneFitTool")

    assert geocoding._http is weather._http is timezone_fit._http
    assert amenities._overpass is local_mobility._overpass is transport_access._overpass is activities._overpass
    assert place_context._mediawiki._http is geocoding._http
    assert wikivoyage_climate._mediawiki is place_context._mediawiki
    assert local_mobility._wikivoyage._mediawiki is place_context._mediawiki
    assert transport_access._wikivoyage is local_mobility._wikivoyage
    assert activities._wikivoyage is local_mobility._wikivoyage
    assert transport_access._origin_resolver is timezone_fit._origin_resolver


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


@pytest.mark.asyncio
async def test_independent_tool_candidate_jobs_run_concurrently_up_to_configured_limit():
    class ConcurrencyTracker:
        def __init__(self):
            self.active = 0
            self.maximum_active = 0

        async def run(self, candidate, profile):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0.02)
            self.active -= 1
            return ToolResult(
                tool_name="TrackedTool",
                place=candidate.place_name,
                normalized_data={"count": 1},
                source_name="test",
                retrieved_at=datetime.now(UTC),
            )

    tracker = ConcurrencyTracker()
    registry = ToolRegistry(
        {f"Tool{index}": tracker for index in range(4)},
        max_concurrent_requests=10,
    )
    candidates = [
        CandidatePlace(place_name=f"City {index}", country="X", reason_for_inclusion="test")
        for index in range(2)
    ]

    await registry.run_tools(
        {f"Tool{index}" for index in range(4)},
        candidates,
        PlaceRequestProfile(purpose="vacation"),
    )

    assert tracker.maximum_active == 8


@pytest.mark.asyncio
async def test_tool_batch_cutoff_keeps_completed_results_and_cancels_pending_calls():
    cancellation_observed = asyncio.Event()

    class FastTool:
        async def run(self, candidate, profile):
            await asyncio.sleep(0.001)
            return ToolResult(
                tool_name="FastTool",
                place=candidate.place_name,
                normalized_data={"count": 1},
                source_name="test",
                retrieved_at=datetime.now(UTC),
            )

    class SlowTool:
        async def run(self, candidate, profile):
            try:
                await asyncio.sleep(10)
            finally:
                cancellation_observed.set()

    registry = ToolRegistry(
        {"FastTool": FastTool(), "SlowTool": SlowTool()},
        max_concurrent_requests=10,
    )
    candidate = CandidatePlace(place_name="City", country="X", reason_for_inclusion="test")

    grouped = await registry.run_tools(
        {"FastTool", "SlowTool"},
        [candidate],
        PlaceRequestProfile(purpose="vacation"),
        timeout_seconds=0.1,
    )

    by_name = {result.tool_name: result for result in grouped["City"]}
    assert by_name["FastTool"].error is None
    assert "research time budget expired" in by_name["SlowTool"].error
    assert cancellation_observed.is_set()


@pytest.mark.asyncio
async def test_per_tool_budget_stops_one_slow_call_without_waiting_for_research_cutoff():
    cancellation_observed = asyncio.Event()

    class SlowTool:
        async def run(self, candidate, profile):
            try:
                await asyncio.sleep(10)
            finally:
                cancellation_observed.set()

    registry = ToolRegistry(
        {"SlowTool": SlowTool()},
        max_concurrent_requests=10,
        tool_execution_timeout_seconds=0.02,
    )
    candidate = CandidatePlace(place_name="City", country="X", reason_for_inclusion="test")

    grouped = await registry.run_tools(
        {"SlowTool"},
        [candidate],
        PlaceRequestProfile(purpose="vacation"),
        timeout_seconds=1.0,
    )

    result = grouped["City"][0]
    assert "per-invocation execution budget" in result.error
    assert cancellation_observed.is_set()


@pytest.mark.asyncio
async def test_tool_priority_runs_important_criterion_for_all_candidates_first():
    execution_order = []

    class OrderedTool:
        def __init__(self, name):
            self.name = name

        async def run(self, candidate, profile):
            execution_order.append((self.name, candidate.place_name))
            await asyncio.sleep(0)
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                normalized_data={"count": 1},
                source_name="test",
                retrieved_at=datetime.now(UTC),
            )

    registry = ToolRegistry(
        {
            "HighPriorityTool": OrderedTool("HighPriorityTool"),
            "LowPriorityTool": OrderedTool("LowPriorityTool"),
        },
        max_concurrent_requests=1,
    )
    candidates = [
        CandidatePlace(place_name=f"City {index}", country="X", reason_for_inclusion="test")
        for index in range(2)
    ]

    await registry.run_tools(
        {"LowPriorityTool", "HighPriorityTool"},
        candidates,
        PlaceRequestProfile(purpose="vacation"),
        tool_priorities={"HighPriorityTool": 0.9, "LowPriorityTool": 0.2},
    )

    assert execution_order == [
        ("HighPriorityTool", "City 0"),
        ("HighPriorityTool", "City 1"),
        ("LowPriorityTool", "City 0"),
        ("LowPriorityTool", "City 1"),
    ]
