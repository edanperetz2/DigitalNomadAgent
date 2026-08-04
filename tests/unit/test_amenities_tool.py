from datetime import UTC, datetime

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.tools.amenities import AmenitiesTool, build_query, select_categories


class FakeCache:
    def __init__(self, cached=None, stale=False):
        self.cached = cached
        self.stale = stale
        self.get_calls = []
        self.set_calls = []

    async def get(self, tool_name, place, params, ttl_key=None):
        self.get_calls.append((tool_name, place, params, ttl_key))
        return self.cached, self.stale

    async def set(self, tool_name, place, params, response, ttl_key=None):
        self.set_calls.append((tool_name, place, params, response, ttl_key))


class FakeOverpass:
    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else {"elements": []}
        self.error = error
        self.queries = []

    async def query(self, query):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.payload


def _counted(*totals: int) -> dict:
    """An Overpass `out count` response: one count element per selector group."""
    return {"elements": [{"type": "count", "id": 0, "tags": {"total": str(t)}} for t in totals]}


def _candidate(lat=38.72, lon=-9.14):
    return CandidatePlace(
        place_name="Lisbon",
        country="Portugal",
        reason_for_inclusion="test",
        verified=True,
        lat=lat,
        lon=lon,
    )


def _tool(*, payload=None, error=None, cache=None):
    overpass = FakeOverpass(payload=payload, error=error)
    fake_cache = cache or FakeCache()
    return AmenitiesTool(fake_cache, overpass=overpass), overpass, fake_cache


def test_category_selection_uses_purpose_defaults_and_explicit_preferences():
    remote = PlaceRequestProfile(purpose="remote_work")
    study = PlaceRequestProfile(purpose="study")
    mixed = PlaceRequestProfile(
        purpose="mixed",
        secondary_purposes=["remote_work", "study"],
        amenity_preferences=["green spaces", "gyms", "swimming pool", "parks"],
    )
    mixed_without_secondaries = PlaceRequestProfile(purpose="mixed")

    assert select_categories(remote) == (["coworking", "cafe"], [])
    assert select_categories(study) == (["university", "library"], [])
    assert select_categories(mixed) == (
        ["coworking", "cafe", "university", "library", "park", "fitness_centre"],
        ["swimming pool"],
    )
    assert select_categories(mixed_without_secondaries) == (["coworking", "cafe"], [])


def test_query_counts_server_side_and_never_requests_hospitals():
    query = build_query(["coworking", "cafe", "park"], 1.0, 2.0)

    assert 'nwr["office"="coworking"]' in query
    assert 'nwr["amenity"="coworking_space"]' in query
    assert 'nwr["amenity"="cafe"]' in query
    assert 'nwr["leisure"="park"]' in query
    assert "hospital" not in query
    # Counts only. `out tags;` shipped every matching element and returned 504 /
    # multi-MiB bodies against a real city, which is why no Overpass-backed tool
    # ever completed inside its 50s cap.
    assert "out tags" not in query
    assert query.count("out count;") == 3, "one counted set per requested category"


@pytest.mark.asyncio
async def test_one_request_returns_one_count_per_requested_category():
    # Counts arrive in the order the categories were requested.
    payload = _counted(2, 2, 1, 1, 1, 1, 1)
    tool, overpass, cache = _tool(payload=payload)
    profile = PlaceRequestProfile(
        purpose="mixed",
        secondary_purposes=["remote_work", "study"],
        amenity_preferences=["park", "supermarket", "fitness_centre"],
    )

    result = await tool.run(_candidate(), profile)

    assert result.error is None
    assert len(overpass.queries) == 1
    assert result.normalized_data["counts_by_category"] == {
        "coworking": 2,
        "cafe": 2,
        "university": 1,
        "library": 1,
        "park": 1,
        "supermarket": 1,
        "fitness_centre": 1,
    }
    assert result.normalized_data["valid_element_count"] == 9
    assert "count" not in result.normalized_data
    assert cache.set_calls


@pytest.mark.asyncio
async def test_a_count_mismatch_is_an_error_not_silent_zeros():
    """A zero count means "none nearby", which is real evidence. A truncated
    response must never be mistaken for it."""
    tool, _, _ = _tool(payload=_counted(3))

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="remote_work"))

    assert result.error is not None
    assert "count" in result.error


@pytest.mark.asyncio
async def test_unsupported_hospital_only_request_stays_unresolved_without_network():
    tool, overpass, _ = _tool()
    profile = PlaceRequestProfile(purpose="vacation", amenity_preferences=["hospital"])

    result = await tool.run(_candidate(), profile)

    assert result.error is None
    assert result.normalized_data["unsupported_categories"] == ["hospital"]
    assert any("unresolved" in warning for warning in result.warnings)
    assert overpass.queries == []


@pytest.mark.asyncio
async def test_partial_response_keeps_valid_counts_and_reduces_confidence():
    payload = dict(_counted(0, 1), remark="runtime error: Query timed out")
    tool, _, _ = _tool(payload=payload)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="remote_work"))

    assert result.normalized_data["counts_by_category"] == {"coworking": 0, "cafe": 1}
    assert result.normalized_data["partial"] is True
    assert result.confidence == "low"
    assert any("partial response" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_fresh_cache_avoids_overpass():
    cached_result = ToolResult(
        tool_name="AmenitiesTool",
        place="Lisbon",
        normalized_data={
            "requested_categories": ["coworking", "cafe"],
            "counts_by_category": {"coworking": 2, "cafe": 10},
            "unsupported_categories": [],
            "radius_m": 3000,
            "partial": False,
            "valid_element_count": 12,
        },
        source_name="OpenStreetMap Overpass",
        retrieved_at=datetime.now(UTC),
        confidence="medium",
    ).model_dump(mode="json")
    tool, overpass, cache = _tool(cache=FakeCache(cached=cached_result))

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="remote_work"))

    assert result.normalized_data["counts_by_category"]["cafe"] == 10
    assert overpass.queries == []
    assert cache.set_calls == []


@pytest.mark.asyncio
async def test_stale_cache_is_used_after_provider_failure():
    cached_result = ToolResult(
        tool_name="AmenitiesTool",
        place="Lisbon",
        normalized_data={
            "requested_categories": ["coworking", "cafe"],
            "counts_by_category": {"coworking": 1, "cafe": 4},
            "unsupported_categories": [],
            "radius_m": 3000,
            "partial": False,
            "valid_element_count": 5,
        },
        source_name="OpenStreetMap Overpass",
        retrieved_at=datetime.now(UTC),
        confidence="medium",
    ).model_dump(mode="json")
    tool, _, _ = _tool(error=RuntimeError("offline"), cache=FakeCache(cached=cached_result, stale=True))

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="remote_work"))

    assert result.error is None
    assert result.stale is True
    assert any("stale cached" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_complete_provider_failure_returns_missing_evidence():
    tool, _, _ = _tool(error=RuntimeError("offline"))

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="remote_work"))

    assert result.error == "Amenities lookup failed: offline"
    assert result.normalized_data == {}


@pytest.mark.asyncio
async def test_missing_coordinates_returns_error_without_request():
    tool, overpass, _ = _tool()
    candidate = CandidatePlace(place_name="Unverified", country="X", reason_for_inclusion="test")

    result = await tool.run(candidate, PlaceRequestProfile(purpose="remote_work"))

    assert result.error == "Cannot query amenities without verified coordinates."
    assert overpass.queries == []
