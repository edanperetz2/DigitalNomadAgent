from datetime import UTC, date, datetime

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.tools.timezone_fit import GEOCODING_PARAMS, ORIGIN_CACHE_TOOL, TimezoneFitTool


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


class FakeHttp:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    async def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.payload


def _candidate(lat=38.72, lon=-9.14):
    return CandidatePlace(
        place_name="Lisbon",
        country="Portugal",
        reason_for_inclusion="test",
        verified=True,
        lat=lat,
        lon=lon,
    )


def _provider_result(name="Reykjavik", country="Iceland", timezone="Atlantic/Reykjavik", country_code=None):
    return {
        "results": [
            {"name": name, "country": country, "country_code": country_code, "timezone": timezone}
        ]
    }


def _tool(*, payload=None, error=None, cache=None, destination_timezone="Europe/Lisbon", today=date(2026, 7, 14)):
    fake_http = FakeHttp(payload=payload, error=error)
    fake_cache = cache or FakeCache()
    tool = TimezoneFitTool(
        fake_cache,
        http=fake_http,
        timezone_at=lambda lat, lon: destination_timezone,
        today=lambda: today,
    )
    return tool, fake_http, fake_cache


@pytest.mark.asyncio
async def test_local_city_alias_and_comma_form_avoid_network_and_use_requested_month():
    tool, http, _ = _tool()
    profile = PlaceRequestProfile(purpose="remote_work", origin="Tel Aviv, Israel", target_months=[1])

    result = await tool.run(_candidate(), profile)

    assert result.error is None
    assert result.normalized_data["resolved_origin_name"] == "Tel Aviv"
    assert result.normalized_data["resolved_origin_country"] == "Israel"
    assert result.normalized_data["origin_resolution_method"] == "local_alias"
    assert result.normalized_data["representative_date"] == "2027-01-15"
    assert result.normalized_data["estimated_workday_overlap_hours"] == 6.0
    assert http.calls == []


@pytest.mark.asyncio
async def test_provider_city_uses_top_match_and_records_independent_source():
    tool, http, cache = _tool(payload=_provider_result())
    profile = PlaceRequestProfile(purpose="remote_work", origin="Reykjavik suburb", target_months=[8])

    result = await tool.run(_candidate(), profile)

    assert result.normalized_data["resolved_origin_name"] == "Reykjavik"
    assert result.normalized_data["resolved_origin_country"] == "Iceland"
    assert result.normalized_data["origin_resolution_method"] == "open_meteo"
    assert len(http.calls) == 1
    assert http.calls[0][1]["params"] == {"name": "Reykjavik suburb", **GEOCODING_PARAMS}
    assert cache.set_calls[0][0] == ORIGIN_CACHE_TOOL
    assert [item.component for item in result.evidence_items] == ["origin_resolution", "workday_overlap"]
    assert result.evidence_items[0].source.source_name == "Open-Meteo Geocoding API"


@pytest.mark.asyncio
async def test_provider_country_result_is_accepted():
    tool, _, _ = _tool(payload=_provider_result("Georgia", "Georgia", "Asia/Tbilisi"))

    result = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="Georgia country", target_months=[9])
    )

    assert result.error is None
    assert result.normalized_data["resolved_origin_name"] == "Georgia"
    assert result.normalized_data["origin_timezone"] == "Asia/Tbilisi"


@pytest.mark.asyncio
async def test_provider_search_strips_comma_qualifier_but_preserves_visible_input():
    tool, http, _ = _tool(payload=_provider_result("Nuuk", None, "America/Nuuk", "GL"))

    result = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="Nuuk, Greenland", target_months=[1])
    )

    assert http.calls[0][1]["params"]["name"] == "Nuuk"
    assert result.normalized_data["origin_input"] == "Nuuk, Greenland"
    assert result.normalized_data["resolved_origin_name"] == "Nuuk"
    assert result.normalized_data["resolved_origin_country"] is None
    assert result.normalized_data["resolved_origin_country_code"] == "GL"


@pytest.mark.asyncio
async def test_country_spanning_timezones_uses_visible_fast_default():
    tool, http, _ = _tool()

    result = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="Australia", target_months=[10])
    )

    assert result.normalized_data["resolved_origin_name"] == "Sydney"
    assert any("spans multiple timezones" in warning for warning in result.warnings)
    assert http.calls == []


@pytest.mark.asyncio
async def test_fresh_provider_cache_avoids_network():
    cached = {
        "name": "Cambridge",
        "country": "United Kingdom",
        "timezone": "Europe/London",
        "retrieved_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
    }
    tool, http, cache = _tool(cache=FakeCache(cached=cached))

    result = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="Cambridge", target_months=[7])
    )

    assert result.normalized_data["resolved_origin_name"] == "Cambridge"
    assert result.normalized_data["resolved_origin_country"] == "United Kingdom"
    assert result.normalized_data["origin_resolution_was_cached"] is True
    assert http.calls == []
    assert cache.set_calls == []


@pytest.mark.asyncio
async def test_stale_cache_falls_back_when_provider_fails_and_marks_sources_stale():
    cached = {
        "name": "Cambridge",
        "country": "United Kingdom",
        "timezone": "Europe/London",
        "retrieved_at": datetime(2026, 6, 1, tzinfo=UTC).isoformat(),
    }
    tool, _, _ = _tool(error=RuntimeError("offline"), cache=FakeCache(cached=cached, stale=True))

    result = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="Cambridge", target_months=[7])
    )

    assert result.error is None
    assert result.stale is True
    assert result.evidence_items[0].source.stale is True
    assert any("stale cached" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_provider_failure_leaves_overlap_missing_instead_of_positive():
    tool, _, _ = _tool(error=RuntimeError("offline"))

    result = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="Unknownville", target_months=[7])
    )

    assert result.error == "Could not resolve the origin timezone."
    assert "estimated_workday_overlap_hours" not in result.normalized_data
    assert result.confidence == "low"


@pytest.mark.asyncio
async def test_requested_month_uses_dst_on_representative_date():
    tool, _, _ = _tool(destination_timezone="Europe/London")

    winter = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="US Eastern", target_months=[1])
    )
    march = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="US Eastern", target_months=[3])
    )

    assert winter.normalized_data["utc_offset_diff_hours"] == 5.0
    assert march.normalized_data["utc_offset_diff_hours"] == 4.0


@pytest.mark.asyncio
async def test_no_requested_month_uses_current_date_with_warning():
    tool, _, _ = _tool()

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="remote_work", origin="Israel"))

    assert result.normalized_data["representative_date"] == "2026-07-14"
    assert any("current date" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_half_hour_timezone_is_preserved():
    tool, _, _ = _tool(destination_timezone="UTC")

    result = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="St. John's", target_months=[1])
    )

    assert result.normalized_data["origin_utc_offset_hours"] == -3.5
    assert result.normalized_data["estimated_workday_overlap_hours"] == 4.5


@pytest.mark.asyncio
async def test_date_line_uses_circular_difference():
    tool, _, _ = _tool(destination_timezone="Pacific/Honolulu")

    result = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="Pacific/Kiritimati", target_months=[1])
    )

    assert result.normalized_data["raw_utc_offset_diff_hours"] == 24.0
    assert result.normalized_data["utc_offset_diff_hours"] == 0.0
    assert result.normalized_data["estimated_workday_overlap_hours"] == 8.0


@pytest.mark.asyncio
async def test_direct_iana_timezone_avoids_provider():
    tool, http, _ = _tool()

    result = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="America/Chicago", target_months=[2])
    )

    assert result.normalized_data["origin_resolution_method"] == "direct_iana"
    assert result.normalized_data["origin_timezone"] == "America/Chicago"
    assert http.calls == []


@pytest.mark.asyncio
async def test_missing_origin_returns_missing_overlap_without_network():
    tool, http, _ = _tool()

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="remote_work", target_months=[2]))

    assert result.error is None
    assert "estimated_workday_overlap_hours" not in result.normalized_data
    assert any("origin timezone is unknown" in warning.lower() for warning in result.warnings)
    assert http.calls == []


@pytest.mark.asyncio
async def test_missing_destination_coordinates_is_an_error():
    tool, _, _ = _tool()
    candidate = CandidatePlace(place_name="Unverified", country="X", reason_for_inclusion="test")

    result = await tool.run(candidate, PlaceRequestProfile(purpose="remote_work", origin="Israel"))

    assert result.error == "Cannot compute timezone fit without verified coordinates."


@pytest.mark.asyncio
async def test_malformed_provider_results_are_skipped_until_a_usable_match():
    payload = {
        "results": [
            {"name": "Broken", "timezone": "Not/AZone"},
            {"name": "Cambridge", "country": "United Kingdom", "timezone": "Europe/London"},
        ]
    }
    tool, _, _ = _tool(payload=payload)

    result = await tool.run(
        _candidate(), PlaceRequestProfile(purpose="remote_work", origin="Cambridge area", target_months=[7])
    )

    assert result.normalized_data["resolved_origin_name"] == "Cambridge"
