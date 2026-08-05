from datetime import UTC, date, datetime

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.tools.wikivoyage_climate import (
    WikivoyageClimateTool,
    parse_climate_chart,
)

POSITIONAL_CHART = """{{climate chart
|Test City
|1|11|101
|2|12|102
|3|13|103
|4|14|104
|5|15|105
|6|16|106
|18|28|5
|19|29|4
|9|19|109
|10|20|110
|11|21|111
|12|22|112
|description=ignored
}}"""


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


class FakeMediaWiki:
    def __init__(self, responses=(), error=None):
        self.responses = list(responses)
        self.error = error
        self.calls = []

    async def request(self, **params):
        self.calls.append(params)
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError("Unexpected MediaWiki request")
        return self.responses.pop(0)


def _resolve(title="Lisbon", revision_id=12345):
    return {
        "query": {
            "redirects": [{"from": "Lisboa", "to": title}],
            "pages": [
                {
                    "pageid": 19798,
                    "title": title,
                    "revisions": [{"revid": revision_id, "timestamp": "2026-07-01T12:00:00Z"}],
                }
            ],
        }
    }


def _sections(*sections):
    values = list(sections) or [{"line": "Climate", "index": "7", "anchor": "Climate"}]
    return {"parse": {"title": "Lisbon", "sections": values}}


def _section(wikitext=POSITIONAL_CHART, rendered_html=None, revision_id=12345):
    rendered_html = rendered_html or (
        "<table><tr><td>scorching table text must be ignored</td></tr></table>"
        "<p>Summers are very warm and very dry. The city is mostly sunny but not windy.</p>"
        "<p>The weather is perfect.</p>"
    )
    return {
        "parse": {
            "title": "Lisbon",
            "revid": revision_id,
            "wikitext": wikitext,
            "text": rendered_html,
        }
    }


def _candidate():
    return CandidatePlace(
        place_name="Lisboa",
        canonical_name="Lisbon",
        country="Portugal",
        reason_for_inclusion="test",
        verified=True,
        lat=38.72,
        lon=-9.14,
    )


def _profile(**overrides):
    defaults = {
        "purpose": "vacation",
        "target_months": [7, 8],
        "climate_preferences": ["warm", "dry", "sunny", "calm"],
    }
    defaults.update(overrides)
    return PlaceRequestProfile(**defaults)


def _tool(responses=(), *, cache=None, error=None):
    mediawiki = FakeMediaWiki(responses, error=error)
    tool = WikivoyageClimateTool(
        cache or FakeCache(),
        mediawiki=mediawiki,
        today=lambda: date(2026, 7, 14),
    )
    return tool, mediawiki


def test_positional_climate_chart_parses_twelve_months():
    chart = parse_climate_chart(POSITIONAL_CHART)

    assert len(chart) == 12
    assert chart[6] == {"month": 7, "avg_low_c": 18.0, "avg_high_c": 28.0, "precipitation_mm": 5.0}
    assert chart[7] == {"month": 8, "avg_low_c": 19.0, "avg_high_c": 29.0, "precipitation_mm": 4.0}


def test_named_climate_chart_parses_supported_fields():
    chart = parse_climate_chart(
        """{{weather box
| Jan low C = 2
| Jan high C = 10
| Jan precipitation mm = 75
| Feb low C = 3
| Feb high C = 12
| Feb rainfall = 60
}}"""
    )

    assert chart == [
        {"month": 1, "avg_low_c": 2.0, "avg_high_c": 10.0, "precipitation_mm": 75.0},
        {"month": 2, "avg_low_c": 3.0, "avg_high_c": 12.0, "precipitation_mm": 60.0},
    ]


@pytest.mark.asyncio
async def test_resolves_revision_extracts_section_and_scores_requested_components():
    cache = FakeCache()
    tool, mediawiki = _tool([_resolve(), _sections(), _section()], cache=cache)

    result = await tool.run(_candidate(), _profile())

    assert result.error is None
    assert result.confidence == "medium"
    assert result.source_url == "https://en.wikivoyage.org/w/index.php?oldid=12345#Climate"
    assert result.data_date == "Wikivoyage revision 12345 (2026-07-01T12:00:00Z)"
    assert result.normalized_data["resolved_title"] == "Lisbon"
    assert result.normalized_data["revision_id"] == 12345
    assert result.normalized_data["target_months"] == [7, 8]
    assert set(result.normalized_data["component_scores"]) == {"temperature", "rain", "sunshine", "wind"}
    assert result.normalized_data["component_scores"]["rain"] == pytest.approx(0.94)
    assert "perfect" in result.normalized_data["excerpt"]
    assert "scorching table text" not in result.normalized_data["excerpt"]
    assert result.normalized_data["preview_excerpt"] == result.normalized_data["excerpt"]
    assert result.normalized_data["context_chunks"]
    assert result.normalized_data["full_section_chars"] == result.normalized_data["included_chars"]
    assert result.normalized_data["truncated"] is False
    assert all(
        len(item["excerpt"]) <= 240
        for values in result.normalized_data["phrase_signals"].values()
        for item in values
    )
    assert mediawiki.calls[0]["titles"] == "Lisbon"
    assert mediawiki.calls[1] == {"action": "parse", "oldid": 12345, "prop": "sections"}
    assert mediawiki.calls[2]["oldid"] == 12345
    assert mediawiki.calls[2]["section"] == "7"
    assert cache.set_calls


@pytest.mark.asyncio
async def test_no_target_months_yields_no_seasonal_reading(monkeypatch):
    """Overturns test_current_month_fallback_is_in_cache_identity_and_result (D31).

    A seasonal reading of the Climate section needs a season; substituting
    today's month made the tool answer a question nobody asked.
    """
    cache = FakeCache()
    tool, _ = _tool([_resolve(), _sections(), _section()], cache=cache)

    result = await tool.run(_candidate(), _profile(target_months=[]))

    assert result.error
    assert "did not establish when the stay happens" in result.error
    assert cache.get_calls == []


@pytest.mark.asyncio
async def test_season_filter_and_negation_ignore_irrelevant_winter_claims():
    html = (
        "<p>Winters are very rainy and very windy.</p>"
        "<p>Summers are very dry, not windy, and not humid.</p>"
    )
    tool, _ = _tool([_resolve(), _sections(), _section(wikitext="===Climate===", rendered_html=html)])
    profile = _profile(climate_preferences=["dry", "calm", "low humidity"])

    result = await tool.run(_candidate(), profile)

    assert result.normalized_data["component_scores"] == {"humidity": 0.8, "rain": 0.8, "wind": 0.8}
    assert all(
        "Winters" not in excerpt
        for detail in result.normalized_data["component_details"].values()
        for excerpt in detail["evidence_excerpts"]
    )


@pytest.mark.asyncio
async def test_mixed_season_sentence_uses_only_the_requested_season_clause():
    html = "<p>The city has mild winters and very warm summers.</p>"
    tool, _ = _tool([_resolve(), _sections(), _section(wikitext="===Climate===", rendered_html=html)])

    result = await tool.run(_candidate(), _profile(climate_preferences=["warm"]))

    assert result.normalized_data["phrase_signals"]["temperature"] == [
        {"signal": "warm", "excerpt": "very warm summers."}
    ]
    assert result.normalized_data["component_scores"]["temperature"] == 0.8


@pytest.mark.asyncio
async def test_subjective_prose_does_not_create_a_score():
    html = "<p>The city has a pleasant climate and perfect weather throughout the year.</p>"
    tool, _ = _tool([_resolve(), _sections(), _section(wikitext="===Climate===", rendered_html=html)])

    result = await tool.run(_candidate(), _profile(climate_preferences=["sunny"]))

    assert result.error is None
    assert result.confidence == "low"
    assert result.normalized_data["component_scores"] == {}
    assert any("no evidence relevant" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_missing_article_returns_controlled_error():
    tool, _ = _tool([{"query": {"pages": [{"title": "Missing", "missing": True}]}}])

    result = await tool.run(_candidate(), _profile())

    assert result.error == "Wikivoyage climate lookup failed: No Wikivoyage article was found for this destination"


@pytest.mark.asyncio
async def test_missing_climate_section_returns_controlled_error():
    tool, _ = _tool([_resolve(), _sections({"line": "Understand", "index": "1", "anchor": "Understand"})])

    result = await tool.run(_candidate(), _profile())

    assert result.error == "Wikivoyage climate lookup failed: The Wikivoyage article has no Climate section"
    assert result.source_url == "https://en.wikivoyage.org/w/index.php?oldid=12345"


@pytest.mark.asyncio
async def test_revision_mismatch_is_rejected():
    tool, _ = _tool([_resolve(), _sections(), _section(revision_id=99999)])

    result = await tool.run(_candidate(), _profile())

    assert "unexpected revision" in result.error


@pytest.mark.asyncio
async def test_stale_cache_is_used_when_live_lookup_fails():
    cached = ToolResult(
        tool_name="WikivoyageClimateTool",
        place="Lisboa",
        normalized_data={"component_scores": {"rain": 0.8}},
        source_name="Wikivoyage climate section",
        source_url="https://en.wikivoyage.org/w/index.php?oldid=1#Climate",
        retrieved_at=datetime.now(UTC),
        confidence="medium",
    )
    cache = FakeCache(cached=cached.model_dump(mode="json"), stale=True)
    tool, _ = _tool(cache=cache, error=RuntimeError("offline"))

    result = await tool.run(_candidate(), _profile())

    assert result.error is None
    assert result.stale is True
    assert any("stale cached Wikivoyage climate evidence" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_fresh_cache_avoids_mediawiki_calls():
    cached = ToolResult(
        tool_name="WikivoyageClimateTool",
        place="Lisboa",
        normalized_data={"component_scores": {"rain": 0.8}},
        source_name="Wikivoyage climate section",
        retrieved_at=datetime.now(UTC),
    )
    cache = FakeCache(cached=cached.model_dump(mode="json"), stale=False)
    tool, mediawiki = _tool(cache=cache)

    result = await tool.run(_candidate(), _profile())

    assert result.normalized_data["component_scores"] == {"rain": 0.8}
    assert mediawiki.calls == []
