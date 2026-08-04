import asyncio
from datetime import UTC, datetime

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.local_mobility import LocalMobilityTool, build_query, parse_counts
from app.tools.wikivoyage_sections import (
    WikivoyageSection,
    WikivoyageSectionClient,
    WikivoyageSectionNotFound,
    build_section_context,
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


class FakeWikivoyage:
    def __init__(self, section=None, error=None):
        self.section = section or _section()
        self.error = error
        self.calls = []

    async def fetch(self, title, section_names, **options):
        self.calls.append((title, section_names, options))
        if self.error is not None:
            raise self.error
        return self.section


class FakeMediaWiki:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, **params):
        self.calls.append(params)
        if not self.responses:
            raise AssertionError("Unexpected MediaWiki request")
        return self.responses.pop(0)


def _section(excerpt="Frequent buses serve the centre, which is compact enough to explore on foot."):
    return WikivoyageSection(
        resolved_title="Lisbon",
        page_id=19798,
        revision_id=12345,
        revision_timestamp="2026-07-01T12:00:00Z",
        section_title="Get around",
        section_index="4",
        section_anchor="Get_around",
        context=build_section_context(f"<p>{excerpt}</p>", "Get around"),
        source_url="https://en.wikivoyage.org/w/index.php?oldid=12345#Get_around",
    )


def _candidate(lat=38.72, lon=-9.14):
    return CandidatePlace(
        place_name="Lisboa",
        canonical_name="Lisbon",
        country="Portugal",
        reason_for_inclusion="test",
        verified=True,
        lat=lat,
        lon=lon,
    )


def _tool(*, payload=None, osm_error=None, section=None, wiki_error=None, cache=None):
    overpass = FakeOverpass(payload=payload, error=osm_error)
    wikivoyage = FakeWikivoyage(section=section, error=wiki_error)
    fake_cache = cache or FakeCache()
    return (
        LocalMobilityTool(
            fake_cache,
            overpass=overpass,
            wikivoyage=wikivoyage,
        ),
        overpass,
        wikivoyage,
        fake_cache,
    )


def test_query_counts_each_component_server_side():
    query = build_query(1.0, 2.0)

    assert query.startswith("[out:json][timeout:")
    # Stops and stations are point features, so they are queried as nodes; the
    # nwr form forced Overpass to resolve way/relation geometry for `around`,
    # which is what made this the single most expensive query in the codebase
    # (28,150 elements / 9.6 MiB / ~88s against Berlin).
    assert 'node["highway"="bus_stop"]' in query
    assert 'node["railway"~"^(station|halt|tram_stop)$"]' in query
    # Footpaths and cycleways are inherently ways and stay as ways.
    assert 'way["highway"~"^(pedestrian|footway|living_street)$"]' in query
    assert 'way["cycleway"]' in query
    assert "out tags" not in query
    assert query.count("out count;") == 4, "one counted set per component"


def test_parser_maps_counted_sets_onto_components_in_order():
    counts, invalid, valid = parse_counts(_counted(2, 1, 1, 2))

    assert counts == {
        "bus_stops": 2,
        "rail_metro_tram_stations": 1,
        "pedestrian_ways": 1,
        "cycleways": 2,
    }
    # Counting happens server-side now, so no per-element payload survives to be
    # malformed; `valid` is the total across components.
    assert invalid == 0
    assert valid == 6


def test_a_count_mismatch_raises_rather_than_reporting_zeros():
    """A zero count is real evidence ("none nearby"); a truncated response is not."""
    with pytest.raises(ValueError):
        parse_counts(_counted(1, 2))


def test_context_preserves_every_subsection_when_it_fits():
    rendered_html = (
        "<p>General orientation.</p>"
        "<h3>By metro</h3><p>Metro details.</p>"
        "<h3>By bus</h3><p>Bus details.</p>"
        "<h3>On foot</h3><p>Walking details.</p>"
        "<h3>By bicycle</h3><p>Cycling details.</p>"
    )

    context = build_section_context(
        rendered_html,
        "Get around",
        preview_chars=20,
        max_context_chars=1_000,
        max_chunk_chars=30,
    )

    assert context.truncated is False
    assert context.full_section_chars == context.included_chars
    assert context.included_subsections == (
        "Get around",
        "By metro",
        "By bus",
        "On foot",
        "By bicycle",
    )
    assert context.truncated_subsections == ()
    assert context.omitted_subsections == ()
    assert len(context.preview_excerpt) == 20
    assert all(len(chunk.text) <= 30 for chunk in context.context_chunks)


def test_context_distributes_truncated_budget_across_subsections():
    rendered_html = (
        f"<h3>By metro</h3><p>{'M' * 100}</p>"
        f"<h3>By bus</h3><p>{'B' * 100}</p>"
        f"<h3>On foot</h3><p>{'W' * 100}</p>"
        f"<h3>By bicycle</h3><p>{'C' * 100}</p>"
    )

    context = build_section_context(
        rendered_html,
        "Get around",
        preview_chars=25,
        max_context_chars=120,
        max_chunk_chars=20,
    )

    assert context.truncated is True
    assert context.included_chars == 120
    assert context.included_subsections == ("By metro", "By bus", "On foot", "By bicycle")
    assert context.truncated_subsections == ("By metro", "By bus", "On foot", "By bicycle")
    assert context.omitted_subsections == ()
    assert {chunk.subsection for chunk in context.context_chunks} == {
        "By metro",
        "By bus",
        "On foot",
        "By bicycle",
    }
    assert all(len(chunk.text) <= 20 for chunk in context.context_chunks)


@pytest.mark.asyncio
async def test_returns_raw_counts_and_revision_pinned_context_as_separate_evidence():
    # bus_stops, rail_metro_tram_stations, pedestrian_ways, cycleways
    tool, overpass, wikivoyage, cache = _tool(payload=_counted(1, 0, 1, 0))

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="study"))

    assert result.error is None
    assert len(overpass.queries) == 1
    assert len(wikivoyage.calls) == 1
    assert result.normalized_data["counts_by_component"]["bus_stops"] == 1
    assert result.normalized_data["wikivoyage_context"]["excerpt"].startswith("Frequent buses")
    assert result.normalized_data["wikivoyage_context"]["context_chunks"]
    assert result.normalized_data["wikivoyage_context"]["truncated"] is False
    assert result.normalized_data["scoring_status"] == "unresolved_pending_llm"
    assert "mobility_score" not in result.normalized_data
    assert "car_free_feasibility" not in result.normalized_data
    assert [item.component for item in result.evidence_items] == [
        "osm_local_mobility_counts",
        "wikivoyage_get_around_context",
    ]
    assert {item.source.source_name for item in result.evidence_items} == {
        "OpenStreetMap local mobility infrastructure",
        "Wikivoyage Get around section",
    }
    assert result.evidence_items[1].normalized_data["context_chunks"]
    assert result.evidence_items[1].normalized_data["included_subsections"] == ["Get around"]
    assert cache.get_calls[0][2]["wikivoyage_context_contract_version"] == 1
    assert cache.set_calls


@pytest.mark.asyncio
async def test_missing_wikivoyage_section_keeps_complete_osm_counts():
    tool, _, _, cache = _tool(
        payload=_counted(0, 0, 0, 0),
        wiki_error=WikivoyageSectionNotFound("The article has no Get around section"),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.normalized_data["wikivoyage_status"] == "missing"
    assert result.normalized_data["partial"] is False
    assert len(result.evidence_items) == 1
    assert result.evidence_items[0].component == "osm_local_mobility_counts"
    assert cache.set_calls


@pytest.mark.asyncio
async def test_wikivoyage_provider_failure_keeps_osm_as_uncached_partial_evidence():
    tool, _, _, cache = _tool(
        payload=_counted(0, 0, 0, 0),
        wiki_error=RuntimeError("MediaWiki offline"),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.normalized_data["wikivoyage_status"] == "error"
    assert result.normalized_data["partial"] is True
    assert result.confidence == "low"
    assert len(result.evidence_items) == 1
    assert cache.set_calls == []


@pytest.mark.asyncio
async def test_partial_overpass_response_keeps_counts_with_low_confidence():
    tool, _, _, cache = _tool(
        payload=dict(_counted(1, 0, 0, 0), remark="runtime error: Query timed out")
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.normalized_data["counts_by_component"]["bus_stops"] == 1
    assert result.normalized_data["osm_status"] == "partial"
    assert result.normalized_data["partial"] is True
    assert result.evidence_items[0].source.confidence == "low"
    assert any("partial response" in warning for warning in result.warnings)
    assert cache.set_calls == []


@pytest.mark.asyncio
async def test_overpass_failure_keeps_context_without_zero_counts():
    tool, _, _, cache = _tool(osm_error=RuntimeError("Overpass offline"))

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.normalized_data["counts_by_component"] == {}
    assert result.normalized_data["osm_status"] == "error"
    assert result.normalized_data["partial"] is True
    assert [item.component for item in result.evidence_items] == [
        "wikivoyage_get_around_context"
    ]
    assert cache.set_calls == []


@pytest.mark.asyncio
async def test_osm_and_wikivoyage_requests_start_concurrently():
    osm_started = asyncio.Event()
    wiki_started = asyncio.Event()

    class CoordinatedOverpass:
        async def query(self, query):
            del query
            osm_started.set()
            await asyncio.wait_for(wiki_started.wait(), timeout=0.2)
            return _counted(0, 0, 0, 0)

    class CoordinatedWikivoyage:
        async def fetch(self, title, section_names, **options):
            del title, section_names, options
            wiki_started.set()
            await asyncio.wait_for(osm_started.wait(), timeout=0.2)
            return _section()

    tool = LocalMobilityTool(
        FakeCache(),
        overpass=CoordinatedOverpass(),
        wikivoyage=CoordinatedWikivoyage(),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="study"))

    assert result.error is None
    assert osm_started.is_set()
    assert wiki_started.is_set()


@pytest.mark.asyncio
async def test_missing_coordinates_can_still_return_context():
    tool, overpass, _, _ = _tool()
    candidate = CandidatePlace(place_name="Lisbon", country="Portugal", reason_for_inclusion="test")

    result = await tool.run(candidate, PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert overpass.queries == []
    assert result.normalized_data["osm_status"] == "missing_coordinates"
    assert result.normalized_data["counts_by_component"] == {}
    assert result.normalized_data["wikivoyage_status"] == "available"


@pytest.mark.asyncio
async def test_both_provider_failures_return_stale_cache_with_each_source_marked_stale():
    now = datetime.now(UTC)
    cached_result = ToolResult(
        tool_name="LocalMobilityTool",
        place="Lisboa",
        normalized_data={"counts_by_component": {"bus_stops": 5}},
        source_name="OpenStreetMap and Wikivoyage",
        retrieved_at=now,
        evidence_items=[
            EvidenceItem(
                criterion="transportation",
                component="osm_local_mobility_counts",
                source=EvidenceSource(source_name="OSM", retrieved_at=now),
            ),
            EvidenceItem(
                criterion="transportation",
                component="wikivoyage_get_around_context",
                source=EvidenceSource(source_name="Wikivoyage", retrieved_at=now),
            ),
        ],
    ).model_dump(mode="json")
    cache = FakeCache(cached=cached_result, stale=True)
    tool, _, _, _ = _tool(
        osm_error=RuntimeError("Overpass offline"),
        wiki_error=RuntimeError("MediaWiki offline"),
        cache=cache,
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.stale is True
    assert all(item.source.stale for item in result.evidence_items)
    assert all(any("stale cached" in warning for warning in item.warnings) for item in result.evidence_items)


@pytest.mark.asyncio
async def test_both_provider_failures_without_cache_return_missing_evidence():
    tool, _, _, _ = _tool(
        osm_error=RuntimeError("Overpass offline"),
        wiki_error=RuntimeError("MediaWiki offline"),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error == "No local mobility evidence could be retrieved."
    assert result.normalized_data["counts_by_component"] == {}


@pytest.mark.asyncio
async def test_fresh_cache_avoids_both_providers():
    cached_result = ToolResult(
        tool_name="LocalMobilityTool",
        place="Lisboa",
        normalized_data={"counts_by_component": {"bus_stops": 12}},
        source_name="cached",
        retrieved_at=datetime.now(UTC),
    ).model_dump(mode="json")
    tool, overpass, wikivoyage, cache = _tool(cache=FakeCache(cached=cached_result))

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="study"))

    assert result.normalized_data["counts_by_component"]["bus_stops"] == 12
    assert overpass.queries == []
    assert wikivoyage.calls == []
    assert cache.set_calls == []


@pytest.mark.asyncio
async def test_shared_section_client_resolves_revision_and_returns_preview_plus_reasoning_context():
    mediawiki = FakeMediaWiki(
        [
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 19798,
                            "title": "Lisbon",
                            "revisions": [
                                {"revid": 12345, "timestamp": "2026-07-01T12:00:00Z"}
                            ],
                        }
                    ]
                }
            },
            {
                "parse": {
                    "sections": [
                        {"line": "Understand", "index": "1", "anchor": "Understand"},
                        {"line": "Get around", "index": "4", "anchor": "Get_around"},
                    ]
                }
            },
            {
                "parse": {
                    "revid": 12345,
                    "text": "<p>Frequent buses.</p><p>" + "Walkable centre. " * 20 + "</p>",
                }
            },
        ]
    )
    client = WikivoyageSectionClient(mediawiki)

    section = await client.fetch(
        "Lisboa",
        ("Get around", "Getting around"),
        preview_chars=80,
    )

    assert section.resolved_title == "Lisbon"
    assert section.revision_id == 12345
    assert len(section.excerpt) == 80
    assert section.context.truncated is False
    assert section.context.full_section_chars == section.context.included_chars
    assert section.context.context_chunks
    assert section.source_url.endswith("oldid=12345#Get_around")
    assert mediawiki.calls[0]["redirects"] == 1
    assert mediawiki.calls[1] == {"action": "parse", "oldid": 12345, "prop": "sections"}
    assert mediawiki.calls[2]["section"] == "4"


@pytest.mark.asyncio
async def test_shared_section_client_reports_missing_section_without_fetching_text():
    mediawiki = FakeMediaWiki(
        [
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 1,
                            "title": "Place",
                            "revisions": [{"revid": 2, "timestamp": "2026-01-01T00:00:00Z"}],
                        }
                    ]
                }
            },
            {"parse": {"sections": [{"line": "See", "index": "1"}]}},
        ]
    )
    client = WikivoyageSectionClient(mediawiki)

    with pytest.raises(WikivoyageSectionNotFound, match="Get around"):
        await client.fetch("Place", ("Get around",))

    assert len(mediawiki.calls) == 2

@pytest.mark.asyncio
async def test_stalled_overpass_query_still_returns_wikivoyage_context():
    """The in-tool OSM budget must not take the already-fetched prose down with it."""

    class HangingOverpass:
        async def query(self, query):
            await asyncio.sleep(30)

    tool = LocalMobilityTool(
        FakeCache(),
        overpass=HangingOverpass(),
        wikivoyage=FakeWikivoyage(),
        osm_timeout_seconds=0.01,
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="remote_work"))

    assert result.error is None
    assert result.normalized_data["osm_status"] == "error"
    assert result.normalized_data["wikivoyage_status"] == "available"
    assert result.normalized_data["wikivoyage_context"] is not None
    assert result.normalized_data["counts_by_component"] == {}
    assert any("in-tool budget" in warning for warning in result.warnings)
    assert [item.component for item in result.evidence_items] == ["wikivoyage_get_around_context"]
