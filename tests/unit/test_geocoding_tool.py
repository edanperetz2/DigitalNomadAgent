from datetime import UTC, datetime

import httpx
import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.tools.geocoding import CACHE_PARAMS, MAX_RESULTS, GeocodingTool


class FakeCache:
    def __init__(self, cached=None, stale=False):
        self.cached = cached
        self.stale = stale
        self.get_calls = []
        self.set_calls = []

    async def get(self, tool_name, place, params, ttl_key=None):
        self.get_calls.append((tool_name, place, params))
        return self.cached, self.stale

    async def set(self, tool_name, place, params, response, ttl_key=None):
        self.set_calls.append((tool_name, place, params, response))


class FakeHttp:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    async def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        before_request = kwargs.get("before_request")
        if before_request is not None:
            await before_request()
        if self.error is not None:
            raise self.error
        return self.payload


class FakeRateLimiter:
    def __init__(self):
        self.calls = 0

    async def wait(self):
        self.calls += 1


def _candidate(place="Lisbon", country="Portugal"):
    return CandidatePlace(place_name=place, country=country, reason_for_inclusion="test")


def _match(
    *,
    name="Lisbon",
    country="Portugal",
    country_code="pt",
    state="Lisbon",
    county="Lisbon",
    importance=0.75,
    lat="38.7077507",
    lon="-9.1365919",
    place_id=123,
    osm_type="relation",
    osm_id=5400890,
):
    return {
        "place_id": place_id,
        "osm_type": osm_type,
        "osm_id": osm_id,
        "lat": lat,
        "lon": lon,
        "name": name,
        "display_name": f"{name}, {state}, {country}",
        "importance": importance,
        "namedetails": {"name:en": name},
        "address": {
            "city": name,
            "county": county,
            "state": state,
            "country": country,
            "country_code": country_code,
        },
    }


def _tool(payload, *, cache=None, error=None):
    limiter = FakeRateLimiter()
    http = FakeHttp(payload=payload, error=error)
    return GeocodingTool(cache or FakeCache(), rate_limiter=limiter, http=http), http, limiter


@pytest.mark.asyncio
async def test_success_returns_canonical_osm_identity_and_requests_multiple_matches():
    cache = FakeCache()
    tool, http, limiter = _tool([_match()], cache=cache)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.confidence == "high"
    assert result.source_url == "https://www.openstreetmap.org/relation/5400890"
    assert result.normalized_data == {
        "canonical_name": "Lisbon",
        "display_name": "Lisbon, Lisbon, Portugal",
        "lat": 38.7077507,
        "lon": -9.1365919,
        "country": "Portugal",
        "country_code": "PT",
        "region": "Lisbon",
        "place_id": 123,
        "osm_type": "relation",
        "osm_id": 5400890,
        "importance": 0.75,
    }
    assert http.calls[0][1]["params"]["limit"] == MAX_RESULTS
    assert http.calls[0][1]["params"]["namedetails"] == 1
    assert limiter.calls == 1
    assert cache.set_calls[0][2] == CACHE_PARAMS


@pytest.mark.asyncio
async def test_country_alias_accepts_matching_iso_code():
    tool, _, _ = _tool(
        [_match(name="Boston", country="United States", country_code="us", state="Massachusetts")]
    )

    result = await tool.run(_candidate("Boston", "USA"), PlaceRequestProfile(purpose="study"))

    assert result.error is None
    assert result.normalized_data["country_code"] == "US"


@pytest.mark.asyncio
async def test_country_mismatch_is_rejected():
    tool, _, _ = _tool([_match(country="Spain", country_code="es")])

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert "did not agree with the requested country" in result.error
    assert result.normalized_data == {}


@pytest.mark.asyncio
async def test_distinct_localities_with_close_importance_are_ambiguous():
    matches = [
        _match(name="Springfield", country="United States", country_code="us", state="Illinois", importance=0.50),
        _match(
            name="Springfield",
            country="United States",
            country_code="us",
            state="Massachusetts",
            importance=0.48,
            place_id=456,
            osm_id=3369158,
        ),
    ]
    tool, _, _ = _tool(matches)

    result = await tool.run(_candidate("Springfield", "United States"), PlaceRequestProfile(purpose="study"))

    assert "ambiguous" in result.error


@pytest.mark.asyncio
async def test_duplicate_osm_representations_of_same_locality_are_collapsed():
    matches = [
        _match(importance=0.60, osm_type="relation", osm_id=1),
        _match(importance=0.59, osm_type="node", osm_id=2, place_id=456),
    ]
    tool, _, _ = _tool(matches)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.normalized_data["osm_id"] == 1


@pytest.mark.asyncio
async def test_all_low_importance_matches_are_rejected():
    tool, _, _ = _tool([_match(importance=0.14)])

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert "low importance" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a list"},
        ["not an object"],
        [_match(lat=None)],
        [_match(osm_type="invalid")],
        [_match(place_id=0)],
    ],
)
async def test_malformed_responses_return_controlled_errors(payload):
    tool, _, _ = _tool(payload)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is not None
    assert result.confidence == "low"


@pytest.mark.asyncio
async def test_stale_cache_is_used_when_live_lookup_fails():
    cached_result = ToolResult(
        tool_name="GeocodingTool",
        place="Lisbon",
        normalized_data={"lat": 38.7, "lon": -9.1, "canonical_name": "Lisbon", "country_code": "PT"},
        source_name="OpenStreetMap Nominatim",
        retrieved_at=datetime.now(UTC),
        confidence="high",
    )
    cache = FakeCache(cached=cached_result.model_dump(mode="json"), stale=True)
    error = httpx.ConnectError("offline")
    tool, _, _ = _tool(None, cache=cache, error=error)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.stale is True
    assert any("stale cached geocoding" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_fresh_cache_avoids_live_lookup_and_rate_limit_wait():
    cached_result = ToolResult(
        tool_name="GeocodingTool",
        place="Lisbon",
        normalized_data={"lat": 38.7, "lon": -9.1},
        source_name="OpenStreetMap Nominatim",
        retrieved_at=datetime.now(UTC),
    )
    cache = FakeCache(cached=cached_result.model_dump(mode="json"), stale=False)
    tool, http, limiter = _tool(None, cache=cache)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.normalized_data["lat"] == 38.7
    assert http.calls == []
    assert limiter.calls == 0
