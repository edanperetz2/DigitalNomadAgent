import asyncio
from datetime import UTC, datetime

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.tools.activities import (
    ActivitiesTool,
    build_query,
    parse_counts,
    select_activity_categories,
)
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
    def __init__(self, payload=None, error=None, delay=0):
        self.payload = payload if payload is not None else {"elements": []}
        self.error = error
        self.delay = delay
        self.queries = []

    async def query(self, query):
        self.queries.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.payload


class FakeWikivoyage:
    def __init__(self, outcomes=None, error=None, delay=0):
        self.outcomes = outcomes or {"see": _section("See", "3"), "do": _section("Do", "4")}
        self.error = error
        self.delay = delay
        self.calls = []

    async def fetch_many(self, title, section_groups):
        self.calls.append((title, section_groups))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.outcomes


class FakeMediaWiki:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, **params):
        self.calls.append(params)
        if not self.responses:
            raise AssertionError("Unexpected MediaWiki request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


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


def _section(title, index):
    text = f"Deterministic {title} context with useful activity information."
    return WikivoyageSection(
        resolved_title="Lisbon",
        page_id=19798,
        revision_id=12345,
        revision_timestamp="2026-07-01T12:00:00Z",
        section_title=title,
        section_index=index,
        section_anchor=title,
        context=build_section_context(f"<p>{text}</p>", title),
        source_url=f"https://en.wikivoyage.org/w/index.php?oldid=12345#{title}",
    )


def _profile(**overrides):
    values = {"purpose": "vacation", "activity_preferences": ["culture", "hiking"]}
    values.update(overrides)
    return PlaceRequestProfile(**values)


def test_structured_categories_normalize_aliases_and_preserve_unsupported():
    profile = _profile(activity_preferences=["museums", "trail", "nightclubs", "surfing", "Museums"])

    supported, unsupported, fallback = select_activity_categories(profile)

    assert supported == ["culture", "hiking", "nightlife"]
    assert unsupported == ["surfing"]
    assert fallback is False


def test_vacation_fallback_is_culture_and_parks_but_non_vacation_has_no_fallback():
    assert select_activity_categories(_profile(activity_preferences=[])) == (["culture", "parks"], [], True)
    assert select_activity_categories(_profile(purpose="study", activity_preferences=[])) == ([], [], False)


def test_query_uses_category_specific_radii_in_one_union():
    query = build_query(["culture", "nightlife", "parks", "beaches", "hiking"], 1.2, 3.4)

    assert query.startswith("[out:json][timeout:15];(")
    assert "around:5000,1.2,3.4" in query
    assert "around:10000,1.2,3.4" in query
    assert "around:20000,1.2,3.4" in query
    assert 'relation["route"="hiking"]' in query
    assert query.endswith("out tags;")


def test_parse_counts_handles_nodes_ways_relations_deduplication_and_hiking_routes():
    counts, invalid, valid = parse_counts(
        {
            "elements": [
                {"type": "node", "id": 1, "tags": {"tourism": "museum"}},
                {"type": "node", "id": 1, "tags": {"tourism": "museum"}},
                {"type": "way", "id": 1, "tags": {"historic": "castle"}},
                {"type": "relation", "id": 2, "tags": {"route": "hiking"}},
                {"type": "node", "id": 3, "tags": {"natural": "peak"}},
                {"type": "way", "id": 4, "tags": {"leisure": "park"}},
                {"type": "relation", "id": 5, "tags": {"amenity": "nightclub"}},
                {"type": "node", "id": 6, "tags": {"natural": "beach"}},
                {"type": "node", "tags": {"tourism": "museum"}},
            ]
        },
        ["culture", "nightlife", "parks", "beaches", "hiking"],
    )

    assert counts == {"culture": 2, "nightlife": 1, "parks": 1, "beaches": 1, "hiking": 2}
    assert invalid == 1
    assert valid == 7


def test_complete_empty_response_returns_observed_zero_counts():
    counts, invalid, valid = parse_counts({"elements": []}, ["culture", "hiking"])

    assert counts == {"culture": 0, "hiking": 0}
    assert invalid == 0
    assert valid == 0


@pytest.mark.asyncio
async def test_returns_independent_category_and_wikivoyage_evidence_without_score():
    cache = FakeCache()
    overpass = FakeOverpass(
        {
            "elements": [
                {"type": "node", "id": 1, "tags": {"tourism": "museum"}},
                {"type": "relation", "id": 2, "tags": {"route": "hiking"}},
            ]
        }
    )
    wikivoyage = FakeWikivoyage()
    tool = ActivitiesTool(cache, overpass=overpass, wikivoyage=wikivoyage)

    result = await tool.run(_candidate(), _profile())

    assert result.error is None
    assert result.normalized_data["counts_by_category"] == {"culture": 1, "hiking": 1}
    assert result.normalized_data["scoring_status"] == "unresolved_pending_llm"
    assert "score" not in result.normalized_data
    assert [item.component for item in result.evidence_items] == [
        "osm_activity_counts",
        "wikivoyage_see_context",
        "wikivoyage_do_context",
    ]
    assert result.evidence_items[1].source.source_url.endswith("#See")
    assert result.evidence_items[2].normalized_data["context_chunks"]
    assert len(overpass.queries) == 1
    assert wikivoyage.calls[0][0] == "Lisbon"
    assert len(cache.set_calls) == 1


@pytest.mark.asyncio
async def test_unsupported_category_remains_visible_without_becoming_zero():
    overpass = FakeOverpass(error=AssertionError("unsupported category must not be queried"))
    tool = ActivitiesTool(FakeCache(), overpass=overpass, wikivoyage=FakeWikivoyage())

    result = await tool.run(_candidate(), _profile(activity_preferences=["surfing"]))

    assert result.error is None
    assert result.normalized_data["unsupported_categories"] == ["surfing"]
    assert result.normalized_data["category_status"] == {"surfing": "unsupported"}
    assert "surfing" not in result.normalized_data["counts_by_category"]
    assert overpass.queries == []


@pytest.mark.asyncio
async def test_partial_overpass_preserves_valid_counts_and_reduces_confidence():
    tool = ActivitiesTool(
        FakeCache(),
        overpass=FakeOverpass(
            {
                "remark": "runtime error: Query timed out",
                "elements": [
                    {"type": "node", "id": 1, "tags": {"tourism": "museum"}},
                    {"bad": "element"},
                ],
            }
        ),
        wikivoyage=FakeWikivoyage(),
    )

    result = await tool.run(_candidate(), _profile(activity_preferences=["culture"]))

    assert result.normalized_data["counts_by_category"] == {"culture": 1}
    assert result.normalized_data["osm_status"] == "partial"
    assert result.normalized_data["category_status"] == {"culture": "partial"}
    assert result.confidence == "low"
    assert any("partial response" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_missing_one_wikivoyage_section_preserves_the_other():
    wikivoyage = FakeWikivoyage(
        outcomes={"see": _section("See", "3"), "do": WikivoyageSectionNotFound("No Do section")}
    )
    tool = ActivitiesTool(FakeCache(), overpass=FakeOverpass(), wikivoyage=wikivoyage)

    result = await tool.run(_candidate(), _profile(activity_preferences=["parks"]))

    assert result.error is None
    assert result.normalized_data["wikivoyage_section_status"] == {"see": "available", "do": "missing"}
    assert result.normalized_data["partial"] is True
    assert result.confidence == "low"
    assert [item.component for item in result.evidence_items] == [
        "osm_activity_counts",
        "wikivoyage_see_context",
    ]


@pytest.mark.asyncio
async def test_slow_osm_becomes_partial_while_context_is_retained():
    tool = ActivitiesTool(
        FakeCache(),
        overpass=FakeOverpass(delay=1),
        wikivoyage=FakeWikivoyage(),
        sublookup_timeout=0.01,
    )

    result = await tool.run(_candidate(), _profile())

    assert result.error is None
    assert result.normalized_data["osm_status"] == "error"
    assert result.normalized_data["wikivoyage_section_status"] == {"see": "available", "do": "available"}
    assert result.normalized_data["partial"] is True


@pytest.mark.asyncio
async def test_stale_cache_is_used_when_every_live_source_fails():
    cached_result = ToolResult(
        tool_name="ActivitiesTool",
        place="Lisboa",
        normalized_data={"scoring_status": "unresolved_pending_llm"},
        source_name="cached",
        retrieved_at=datetime.now(UTC),
    ).model_dump(mode="json")
    tool = ActivitiesTool(
        FakeCache(cached=cached_result, stale=True),
        overpass=FakeOverpass(error=ValueError("down")),
        wikivoyage=FakeWikivoyage(error=ValueError("down")),
    )

    result = await tool.run(_candidate(), _profile())

    assert result.stale is True
    assert any("stale cached activity evidence" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_fresh_cache_avoids_osm_and_wikivoyage_lookups():
    cached_result = ToolResult(
        tool_name="ActivitiesTool",
        place="Lisboa",
        normalized_data={"scoring_status": "unresolved_pending_llm", "marker": "cached"},
        source_name="cached",
        retrieved_at=datetime.now(UTC),
    ).model_dump(mode="json")
    overpass = FakeOverpass(error=AssertionError("network should not run"))
    wikivoyage = FakeWikivoyage(error=AssertionError("network should not run"))
    tool = ActivitiesTool(FakeCache(cached=cached_result), overpass=overpass, wikivoyage=wikivoyage)

    result = await tool.run(_candidate(), _profile())

    assert result.normalized_data["marker"] == "cached"
    assert overpass.queries == []
    assert wikivoyage.calls == []


@pytest.mark.asyncio
async def test_fetch_many_resolves_revision_once_and_fetches_both_sections():
    mediawiki = FakeMediaWiki(
        [
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 1,
                            "title": "Lisbon",
                            "revisions": [{"revid": 22, "timestamp": "2026-07-01T00:00:00Z"}],
                        }
                    ]
                }
            },
            {
                "parse": {
                    "sections": [
                        {"line": "See", "index": "3", "anchor": "See"},
                        {"line": "Do", "index": "4", "anchor": "Do"},
                    ]
                }
            },
            {"parse": {"revid": 22, "text": "<p>See sights.</p>"}},
            {"parse": {"revid": 22, "text": "<p>Do activities.</p>"}},
        ]
    )
    client = WikivoyageSectionClient(mediawiki)

    sections = await client.fetch_many("Lisboa", {"see": ("See",), "do": ("Do",)})

    assert sections["see"].revision_id == sections["do"].revision_id == 22
    assert len(mediawiki.calls) == 4
    assert sum(call.get("action") == "query" for call in mediawiki.calls) == 1
    assert mediawiki.calls[2]["section"] == "3"
    assert mediawiki.calls[3]["section"] == "4"
