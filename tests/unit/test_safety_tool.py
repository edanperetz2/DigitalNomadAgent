import asyncio
from datetime import UTC, datetime

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.safety import (
    COMPONENT_WEIGHTS,
    SafetyTool,
    analyze_stay_safe,
    fcdo_slug,
    homicide_score,
    score_fcdo_statuses,
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
        self.set_calls = []

    async def get(self, tool_name, place, params, ttl_key=None):
        return self.cached, self.stale

    async def set(self, tool_name, place, params, response, ttl_key=None):
        self.set_calls.append((tool_name, place, params, response))


class FakeHttp:
    def __init__(self, fcdo=None, world_bank=None, error_urls=None):
        self.fcdo = fcdo if fcdo is not None else _fcdo_payload([])
        self.world_bank = world_bank if world_bank is not None else _world_bank_payload(3.0)
        self.error_urls = error_urls or {}
        self.calls = []

    async def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        for fragment, error in self.error_urls.items():
            if fragment in url:
                raise error
        return self.world_bank if "worldbank" in url else self.fcdo


class FakeWikivoyage:
    def __init__(self, section=None, error=None):
        self.section = section or _section("Lisbon is generally safe. Pickpocketing occurs in crowded trams.")
        self.error = error
        self.calls = []

    async def fetch(self, title, section_names):
        self.calls.append((title, section_names))
        if self.error:
            raise self.error
        return self.section


def _candidate(**overrides):
    defaults = dict(
        place_name="Lisbon",
        country="Portugal",
        reason_for_inclusion="test",
        verified=True,
        canonical_name="Lisbon",
        country_code="PT",
        lat=38.72,
        lon=-9.14,
    )
    defaults.update(overrides)
    return CandidatePlace(**defaults)


def _fcdo_payload(statuses):
    return {
        "public_updated_at": "2026-07-14T12:00:00Z",
        "details": {
            "alert_status": statuses,
            "reviewed_at": "2026-07-01T09:00:00Z",
            "country": {"name": "Portugal", "slug": "portugal"},
        },
    }


def _world_bank_payload(rate, year="2023"):
    return [
        {"page": 1},
        [
            {
                "indicator": {"id": "VC.IHR.PSRC.P5"},
                "country": {"value": "Portugal"},
                "date": "2021",
                "value": 9.0,
            },
            {
                "indicator": {"id": "VC.IHR.PSRC.P5"},
                "country": {"value": "Portugal"},
                "date": year,
                "value": rate,
            },
        ],
    ]


def _section(text):
    context = WikivoyageSectionContext(
        preview_excerpt=text[:600],
        context_chunks=(WikivoyageContextChunk(subsection="Stay safe", text=text),),
        full_section_chars=len(text),
        included_chars=len(text),
        truncated=False,
        included_subsections=("Stay safe",),
        truncated_subsections=(),
        omitted_subsections=(),
    )
    return WikivoyageSection(
        resolved_title="Lisbon",
        page_id=123,
        revision_id=456,
        revision_timestamp="2026-07-10T10:00:00Z",
        section_title="Stay safe",
        section_index="12",
        section_anchor="Stay_safe",
        context=context,
        source_url="https://en.wikivoyage.org/w/index.php?oldid=456#Stay_safe",
    )


def _cached_result():
    now = datetime.now(UTC)
    item = EvidenceItem(
        criterion="safety",
        component="fcdo_advisory",
        value=1.0,
        normalized_data={"score": 1.0},
        source=EvidenceSource(source_name="cached FCDO", retrieved_at=now),
    )
    return ToolResult(
        tool_name="SafetyTool",
        place="Lisbon",
        normalized_data={"composite_score": 0.8},
        source_name="cached safety",
        retrieved_at=now,
        evidence_items=[item],
    ).model_dump(mode="json")


def test_fcdo_uses_most_severe_status_and_country_slug_aliases():
    score, status = score_fcdo_statuses(
        ["avoid_all_but_essential_travel_to_parts", "avoid_all_travel_to_whole_country"]
    )

    assert score == 0.0
    assert status == "avoid_all_travel_to_whole_country"
    assert score_fcdo_statuses([]) == (1.0, "no_active_warning")
    assert fcdo_slug("United States", "US") == "usa"
    assert fcdo_slug("Côte d'Ivoire", None) == "cote-d-ivoire"


def test_unknown_fcdo_status_is_not_silently_scored():
    with pytest.raises(ValueError, match="unknown travel-advice"):
        score_fcdo_statuses(["new_unmapped_warning"])


@pytest.mark.parametrize(
    ("rate", "expected"),
    [(0.5, 1.0), (1.0, 1.0), (2.0, 0.925), (3.0, 0.85), (6.0, 0.70), (10.0, 0.55), (20.0, 0.35), (21.0, 0.15)],
)
def test_homicide_thresholds_and_interpolation(rate, expected):
    assert homicide_score(rate) == pytest.approx(expected)


def test_wikivoyage_lexicon_is_negation_aware_and_caps_repetition():
    score, matches, adjustments = analyze_stay_safe(
        "Violent crime is rare. The city is generally safe. "
        "Pickpocketing, scams, theft, harassment and petty crime can occur. "
        "Scams and theft are repeated warnings."
    )

    assert matches["serious_risk"] == []
    assert matches["reassurance"] == ["generally safe"]
    assert adjustments["petty_crime"] == -0.24
    assert score == pytest.approx(0.56)


@pytest.mark.asyncio
async def test_three_components_are_visible_and_weighted():
    cache = FakeCache()
    tool = SafetyTool(cache, http=FakeHttp(), wikivoyage=FakeWikivoyage())

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.confidence == "medium"
    assert result.normalized_data["component_scores"] == {
        "fcdo_advisory": 1.0,
        "homicide_rate": 0.85,
        "wikivoyage_stay_safe": 0.72,
    }
    expected = 0.40 * 1.0 + 0.35 * 0.85 + 0.25 * 0.72
    assert result.normalized_data["composite_score"] == pytest.approx(expected)
    assert result.normalized_data["configured_weights"] == COMPONENT_WEIGHTS
    assert {item.component for item in result.evidence_items} == {
        "fcdo_advisory",
        "homicide_rate",
        "wikivoyage_stay_safe",
    }
    wikivoyage_item = next(item for item in result.evidence_items if item.component == "wikivoyage_stay_safe")
    assert wikivoyage_item.normalized_data["context_chunks"][0]["text"].startswith("Lisbon")
    assert wikivoyage_item.source.data_date == "Wikivoyage revision 456 (2026-07-10T10:00:00Z)"
    assert len(cache.set_calls) == 1


@pytest.mark.asyncio
async def test_two_components_renormalize_and_cap_confidence_low():
    world_bank_error = RuntimeError("provider unavailable")
    tool = SafetyTool(
        FakeCache(),
        http=FakeHttp(error_urls={"worldbank": world_bank_error}),
        wikivoyage=FakeWikivoyage(_section("Generally safe.")),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.confidence == "low"
    assert result.normalized_data["available_component_count"] == 2
    assert result.normalized_data["component_status"]["homicide_rate"] == "error"
    assert result.normalized_data["renormalized_weights"] == {
        "fcdo_advisory": pytest.approx(0.6154),
        "wikivoyage_stay_safe": pytest.approx(0.3846),
    }
    assert result.normalized_data["composite_score"] is not None


@pytest.mark.asyncio
async def test_one_component_is_preserved_but_not_composite_scored():
    tool = SafetyTool(
        FakeCache(),
        http=FakeHttp(error_urls={"gov.uk": RuntimeError("down"), "worldbank": RuntimeError("down")}),
        wikivoyage=FakeWikivoyage(_section("Generally safe.")),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert len(result.evidence_items) == 1
    assert result.normalized_data["composite_score"] is None
    assert any("At least two" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_known_missing_components_are_cached_without_becoming_positive():
    cache = FakeCache()
    tool = SafetyTool(
        cache,
        http=FakeHttp(world_bank=[{"page": 1}, []]),
        wikivoyage=FakeWikivoyage(error=WikivoyageSectionNotFound("no Stay safe section")),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.normalized_data["component_status"] == {
        "fcdo_advisory": "available",
        "homicide_rate": "missing",
        "wikivoyage_stay_safe": "missing",
    }
    assert result.normalized_data["composite_score"] is None
    assert len(cache.set_calls) == 1


@pytest.mark.asyncio
async def test_expired_cache_is_used_only_when_all_live_sources_fail():
    cache = FakeCache(cached=_cached_result(), stale=True)
    tool = SafetyTool(
        cache,
        http=FakeHttp(error_urls={"gov.uk": RuntimeError("down"), "worldbank": RuntimeError("down")}),
        wikivoyage=FakeWikivoyage(error=RuntimeError("down")),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.stale is True
    assert result.confidence == "low"
    assert all(item.source.stale for item in result.evidence_items)


@pytest.mark.asyncio
async def test_fresh_cache_skips_all_providers():
    cache = FakeCache(cached=_cached_result(), stale=False)
    http = FakeHttp()
    wikivoyage = FakeWikivoyage()
    tool = SafetyTool(cache, http=http, wikivoyage=wikivoyage)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.normalized_data["composite_score"] == 0.8
    assert http.calls == []
    assert wikivoyage.calls == []


@pytest.mark.asyncio
async def test_component_calls_run_concurrently():
    active = 0
    maximum_active = 0

    async def delayed_component(score):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return score

    class ConcurrentSafetyTool(SafetyTool):
        async def _fcdo(self, candidate):
            await delayed_component(1.0)
            return await SafetyTool._fcdo(self, candidate)

        async def _homicide(self, candidate):
            await delayed_component(0.8)
            return await SafetyTool._homicide(self, candidate)

        async def _stay_safe(self, title):
            await delayed_component(0.7)
            return await SafetyTool._stay_safe(self, title)

    tool = ConcurrentSafetyTool(FakeCache(), http=FakeHttp(), wikivoyage=FakeWikivoyage())
    await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert maximum_active == 3


@pytest.mark.asyncio
async def test_malformed_provider_data_degrades_without_hiding_other_sources():
    tool = SafetyTool(
        FakeCache(),
        http=FakeHttp(fcdo={"details": "wrong"}, world_bank={"wrong": "shape"}),
        wikivoyage=FakeWikivoyage(_section("Generally safe.")),
    )

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.normalized_data["component_status"] == {
        "fcdo_advisory": "error",
        "homicide_rate": "error",
        "wikivoyage_stay_safe": "available",
    }
    assert result.normalized_data["composite_score"] is None
