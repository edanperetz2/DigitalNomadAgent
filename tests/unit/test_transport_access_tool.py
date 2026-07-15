import asyncio
from datetime import UTC, datetime

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.tools.origin_resolution import GEOCODING_PARAMS, ORIGIN_CACHE_TOOL, OriginResolution, OriginResolver
from app.tools.transport_access import (
    AIRPORT_RADIUS_M,
    TERMINAL_RADIUS_M,
    TransportAccessTool,
    build_query,
    parse_counts,
    straight_line_distance_km,
)
from app.tools.wikivoyage_sections import (
    WikivoyageContextChunk,
    WikivoyageSection,
    WikivoyageSectionContext,
    WikivoyageSectionNotFound,
)


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
        self.cached = response
        self.stale = False


class FakeOverpass:
    def __init__(self, payload=None, error=None, delay=0):
        self.payload = payload if payload is not None else {"elements": []}
        self.error = error
        self.delay = delay
        self.queries = []

    async def query(self, query):
        self.queries.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.payload


class FakeWikivoyage:
    def __init__(self, result=None):
        self.result = result if result is not None else _section()
        self.calls = []

    async def fetch(self, title, section_names):
        self.calls.append((title, section_names))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeOriginResolver:
    def __init__(self, result=None, warnings=None):
        self.result = result
        self.warnings = warnings or []
        self.calls = []

    async def resolve(self, origin, now, *, require_coordinates=False):
        self.calls.append((origin, require_coordinates))
        return self.result, list(self.warnings)


class FakeHttp:
    def __init__(self, payload=None, delay=0, error=None):
        self.payload = payload
        self.delay = delay
        self.error = error
        self.calls = []

    async def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.payload


def _candidate(lat=0.0, lon=1.0):
    return CandidatePlace(
        place_name="Test City",
        canonical_name="Test City, Exampleland",
        country="Exampleland",
        reason_for_inclusion="test",
        verified=True,
        lat=lat,
        lon=lon,
    )


def _section():
    text = "The airport has buses to the centre. Intercity trains arrive at the main station."
    context = WikivoyageSectionContext(
        preview_excerpt=text,
        context_chunks=(WikivoyageContextChunk(subsection="By plane", text=text),),
        full_section_chars=len(text),
        included_chars=len(text),
        truncated=False,
        included_subsections=("By plane",),
        truncated_subsections=(),
        omitted_subsections=(),
    )
    return WikivoyageSection(
        resolved_title="Test City",
        page_id=10,
        revision_id=20,
        revision_timestamp="2026-07-01T00:00:00Z",
        section_title="Get in",
        section_index="2",
        section_anchor="Get_in",
        context=context,
        source_url="https://en.wikivoyage.org/w/index.php?oldid=20#Get_in",
    )


def _origin(*, stale=False):
    return OriginResolution(
        name="Origin City",
        country="Originland",
        country_code="OO",
        timezone="UTC",
        method="open_meteo",
        retrieved_at=datetime(2026, 7, 15, tzinfo=UTC),
        latitude=0.0,
        longitude=0.0,
        stale=stale,
    )


def test_query_uses_one_nwr_union_with_approved_radii():
    query = build_query(12.3, 45.6)

    assert query.count("nwr[") == 4
    assert f"around:{AIRPORT_RADIUS_M},12.3,45.6" in query
    assert query.count(f"around:{TERMINAL_RADIUS_M},12.3,45.6") == 3
    assert query.endswith("out tags;")


def test_parse_counts_deduplicates_osm_identity_and_excludes_subway_stations():
    counts, invalid, valid = parse_counts(
        {
            "elements": [
                {"type": "node", "id": 1, "tags": {"aeroway": "aerodrome"}},
                {"type": "node", "id": 1, "tags": {"aeroway": "aerodrome"}},
                {"type": "way", "id": 1, "tags": {"railway": "station"}},
                {"type": "relation", "id": 2, "tags": {"railway": "station", "station": "subway"}},
                {"type": "node", "id": 3, "tags": {"amenity": "bus_station"}},
                {"type": "relation", "id": 4, "tags": {"amenity": "ferry_terminal"}},
                {"type": "node", "tags": {"amenity": "bus_station"}},
            ]
        }
    )

    assert counts == {
        "airports_within_50km": 1,
        "potential_mainline_rail_stations_within_10km": 1,
        "bus_terminals_within_10km": 1,
        "ferry_terminals_within_10km": 1,
    }
    assert invalid == 1
    assert valid == 5


@pytest.mark.asyncio
async def test_returns_separate_counts_distance_and_wikivoyage_evidence_without_score():
    cache = FakeCache()
    overpass = FakeOverpass(
        {"elements": [{"type": "node", "id": 1, "tags": {"aeroway": "aerodrome"}}]}
    )
    origin_resolver = FakeOriginResolver(_origin())
    tool = TransportAccessTool(
        cache,
        overpass=overpass,
        wikivoyage=FakeWikivoyage(),
        origin_resolver=origin_resolver,
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation", origin="Origin input"))

    assert result.error is None
    assert result.normalized_data["straight_line_distance_km"] == pytest.approx(111.2, abs=0.1)
    assert result.normalized_data["resolved_origin_name"] == "Origin City"
    assert result.normalized_data["scoring_status"] == "unresolved_pending_llm"
    assert "score" not in result.normalized_data
    assert [item.component for item in result.evidence_items] == [
        "osm_arrival_infrastructure_counts",
        "origin_resolution_and_straight_line_distance",
        "wikivoyage_get_in_context",
    ]
    assert result.evidence_items[2].normalized_data["context_chunks"][0]["subsection"] == "By plane"
    assert origin_resolver.calls == [("Origin input", True)]
    assert len(overpass.queries) == 1
    assert len(cache.set_calls) == 1


@pytest.mark.asyncio
async def test_partial_osm_preserves_counts_and_reduces_confidence():
    tool = TransportAccessTool(
        FakeCache(),
        overpass=FakeOverpass(
            {
                "remark": "runtime error: Query timed out",
                "elements": [
                    {"type": "way", "id": 2, "tags": {"amenity": "bus_station"}},
                    {"bad": "element"},
                ],
            }
        ),
        wikivoyage=FakeWikivoyage(),
        origin_resolver=FakeOriginResolver(),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.normalized_data["counts_by_component"]["bus_terminals_within_10km"] == 1
    assert result.normalized_data["osm_status"] == "partial"
    assert result.confidence == "low"
    assert any("partial response" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_stale_origin_is_attributed_only_to_distance_source_and_composite_is_not_cached():
    cache = FakeCache()
    tool = TransportAccessTool(
        cache,
        overpass=FakeOverpass(),
        wikivoyage=FakeWikivoyage(),
        origin_resolver=FakeOriginResolver(_origin(stale=True)),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation", origin="Origin"))

    assert result.stale is False
    assert result.normalized_data["partial"] is True
    assert result.confidence == "low"
    assert result.evidence_items[0].source.stale is False
    assert result.evidence_items[1].source.stale is True
    assert result.evidence_items[2].source.stale is False
    assert cache.set_calls == []


@pytest.mark.asyncio
async def test_slow_sublookup_becomes_partial_instead_of_blocking_other_evidence():
    tool = TransportAccessTool(
        FakeCache(),
        overpass=FakeOverpass(delay=1),
        wikivoyage=FakeWikivoyage(),
        origin_resolver=FakeOriginResolver(),
        sublookup_timeout=0.01,
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.normalized_data["osm_status"] == "error"
    assert result.normalized_data["wikivoyage_status"] == "available"
    assert result.normalized_data["partial"] is True
    assert [item.component for item in result.evidence_items] == ["wikivoyage_get_in_context"]


@pytest.mark.asyncio
async def test_stale_composite_cache_is_used_only_when_all_live_evidence_fails():
    cached_result = ToolResult(
        tool_name="TransportAccessTool",
        place="Test City",
        normalized_data={"scoring_status": "unresolved_pending_llm"},
        source_name="cached",
        retrieved_at=datetime.now(UTC),
    ).model_dump(mode="json")
    cache = FakeCache(cached=cached_result, stale=True)
    tool = TransportAccessTool(
        cache,
        overpass=FakeOverpass(error=ValueError("down")),
        wikivoyage=FakeWikivoyage(WikivoyageSectionNotFound("missing")),
        origin_resolver=FakeOriginResolver(),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.stale is True
    assert any("stale cached transport-access" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_fresh_composite_cache_avoids_all_live_lookups():
    cached_result = ToolResult(
        tool_name="TransportAccessTool",
        place="Test City",
        normalized_data={"scoring_status": "unresolved_pending_llm", "marker": "cached"},
        source_name="cached",
        retrieved_at=datetime.now(UTC),
    ).model_dump(mode="json")
    overpass = FakeOverpass(error=AssertionError("network should not run"))
    wikivoyage = FakeWikivoyage(AssertionError("network should not run"))
    origin_resolver = FakeOriginResolver()
    tool = TransportAccessTool(
        FakeCache(cached=cached_result),
        overpass=overpass,
        wikivoyage=wikivoyage,
        origin_resolver=origin_resolver,
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation", origin="Origin"))

    assert result.normalized_data["marker"] == "cached"
    assert overpass.queries == []
    assert wikivoyage.calls == []
    assert origin_resolver.calls == []


@pytest.mark.asyncio
async def test_origin_resolver_serializes_duplicate_cache_misses_and_reuses_coordinates():
    cache = FakeCache()
    http = FakeHttp(
        {
            "results": [
                {
                    "name": "Tel Aviv",
                    "country": "Israel",
                    "country_code": "IL",
                    "timezone": "Asia/Jerusalem",
                    "latitude": 32.08,
                    "longitude": 34.78,
                }
            ]
        },
        delay=0.01,
    )
    resolver = OriginResolver(cache, http=http)
    now = datetime.now(UTC)

    first, second = await asyncio.gather(
        resolver.resolve("Tel Aviv", now, require_coordinates=True),
        resolver.resolve("Tel Aviv", now, require_coordinates=True),
    )

    assert len(http.calls) == 1
    assert first[0].latitude == second[0].latitude == 32.08
    assert cache.set_calls[0][0] == ORIGIN_CACHE_TOOL
    assert cache.set_calls[0][2] == GEOCODING_PARAMS


@pytest.mark.asyncio
async def test_origin_resolver_uses_stale_coordinates_when_live_refresh_fails():
    cached = {
        "name": "Tel Aviv",
        "country": "Israel",
        "country_code": "IL",
        "timezone": "Asia/Jerusalem",
        "latitude": 32.08,
        "longitude": 34.78,
        "retrieved_at": datetime(2026, 6, 1, tzinfo=UTC).isoformat(),
    }
    resolver = OriginResolver(FakeCache(cached=cached, stale=True), http=FakeHttp(error=ValueError("down")))

    resolution, warnings = await resolver.resolve(
        "Tel Aviv", datetime.now(UTC), require_coordinates=True
    )

    assert resolution is not None
    assert resolution.stale is True
    assert resolution.latitude == 32.08
    assert any("stale cached origin" in warning for warning in warnings)


def test_haversine_is_symmetric_and_zero_for_same_point():
    assert straight_line_distance_km(1, 2, 1, 2) == 0
    assert straight_line_distance_km(0, 0, 0, 1) == pytest.approx(
        straight_line_distance_km(0, 1, 0, 0)
    )
