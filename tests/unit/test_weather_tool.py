from datetime import UTC, date, datetime, timedelta

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.tools.weather import DAILY_VARIABLES, WeatherTool


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
        if self.error is not None:
            raise self.error
        return self.payload


def _candidate(lat=38.7, lon=-9.1):
    return CandidatePlace(
        place_name="Lisbon",
        country="Portugal",
        reason_for_inclusion="test",
        verified=lat is not None and lon is not None,
        lat=lat,
        lon=lon,
    )


def _payload(months, *, omit_years=()):
    days = []
    current = date(2021, 1, 1)
    end = date(2025, 12, 31)
    while current <= end:
        if current.month in months and current.year not in omit_years:
            days.append(current)
        current += timedelta(days=1)

    daily = {"time": [day.isoformat() for day in days]}
    for variable in DAILY_VARIABLES:
        values = []
        for day in days:
            value = {
                "temperature_2m_max": 36.0 if day.month in {7, 8} else 20.0,
                "temperature_2m_min": 10.0,
                "apparent_temperature_max": 38.0 if day.month in {7, 8} else 20.0,
                "relative_humidity_2m_mean": 65.0,
                "precipitation_sum": 5.0 if day.day == 1 else 0.0,
                "rain_sum": 5.0 if day.day == 1 else 0.0,
                "snowfall_sum": 1.0 if day.month == 1 and day.day == 1 else 0.0,
                "sunshine_duration": 8 * 3600.0,
                "daylight_duration": 12 * 3600.0,
                "cloud_cover_mean": 30.0,
                "wind_speed_10m_max": 25.0,
                "wind_gusts_10m_max": 55.0 if day.day == 1 else 40.0,
            }[variable]
            values.append(value)
        daily[variable] = values
    return {"timezone": "Europe/Lisbon", "daily": daily}


def _tool(payload, *, cache=None, error=None):
    http = FakeHttp(payload=payload, error=error)
    tool = WeatherTool(
        cache or FakeCache(),
        http=http,
        today=lambda: date(2026, 7, 14),
    )
    return tool, http


@pytest.mark.asyncio
async def test_requested_months_use_five_previous_calendar_years_and_all_dimensions():
    cache = FakeCache()
    tool, http = _tool(_payload({7, 8}), cache=cache)
    profile = PlaceRequestProfile(purpose="vacation", target_months=[7, 8])

    result = await tool.run(_candidate(), profile)

    assert result.error is None
    assert result.confidence == "high"
    assert result.data_date == "2021-2025 climatology for months 7, 8"
    assert result.normalized_data["data_kind"] == "climatology"
    assert result.normalized_data["target_months"] == [7, 8]
    assert result.normalized_data["years_represented"] == [2021, 2022, 2023, 2024, 2025]
    assert result.normalized_data["temperature_coverage"] == 1.0
    assert result.normalized_data["avg_high_c"] == 36.0
    assert result.normalized_data["avg_apparent_high_c"] == 38.0
    assert result.normalized_data["mean_relative_humidity_pct"] == 65.0
    assert result.normalized_data["avg_monthly_snowfall_cm"] == 0.0
    assert result.normalized_data["sunshine_fraction_of_daylight"] == pytest.approx(0.6667)
    assert result.normalized_data["mean_cloud_cover_pct"] == 30.0
    assert result.normalized_data["p95_daily_max_wind_speed_kmh"] == 25.0
    request_params = http.calls[0][1]["params"]
    assert request_params["start_date"] == "2021-01-01"
    assert request_params["end_date"] == "2025-12-31"
    assert set(request_params["daily"].split(",")) == set(DAILY_VARIABLES)
    assert request_params["timezone"] == "auto"
    assert cache.set_calls


@pytest.mark.asyncio
async def test_current_month_fallback_is_explicit():
    tool, _ = _tool(_payload({7}))

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation"))

    assert result.normalized_data["target_months"] == [7]
    assert any("current calendar month as a fallback" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_cross_year_season_preserves_requested_month_order():
    tool, _ = _tool(_payload({12, 1, 2}))
    profile = PlaceRequestProfile(purpose="vacation", target_months=[12, 1, 2])

    result = await tool.run(_candidate(), profile)

    assert result.normalized_data["target_months"] == [12, 1, 2]
    assert [item["month"] for item in result.normalized_data["monthly_climatology"]] == [12, 1, 2]


@pytest.mark.asyncio
async def test_february_expected_days_include_leap_year():
    tool, _ = _tool(_payload({2}))
    profile = PlaceRequestProfile(purpose="vacation", target_months=[2])

    result = await tool.run(_candidate(), profile)

    assert result.normalized_data["expected_days"] == 141
    assert result.normalized_data["usable_temperature_days"] == 141


@pytest.mark.asyncio
async def test_missing_date_reduces_coverage_without_becoming_zero_data():
    payload = _payload({7})
    for values in payload["daily"].values():
        values.pop()
    tool, _ = _tool(payload)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation", target_months=[7]))

    assert result.error is None
    assert result.normalized_data["temperature_coverage"] < 1.0
    assert result.normalized_data["usable_temperature_days"] == result.normalized_data["expected_days"] - 1


@pytest.mark.asyncio
async def test_insufficient_year_and_day_coverage_returns_missing_evidence():
    tool, _ = _tool(_payload({7}, omit_years={2021, 2022}))

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation", target_months=[7]))

    assert result.error is not None
    assert "coverage is insufficient" in result.error
    assert result.normalized_data == {}


@pytest.mark.asyncio
async def test_misaligned_required_array_is_rejected():
    payload = _payload({7})
    payload["daily"]["temperature_2m_max"].pop()
    tool, _ = _tool(payload)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation", target_months=[7]))

    assert "misaligned temperature_2m_max" in result.error


@pytest.mark.asyncio
async def test_insufficient_optional_coverage_is_reported_as_unavailable_not_zero():
    payload = _payload({7})
    payload["daily"]["relative_humidity_2m_mean"] = [None] * len(payload["daily"]["time"])
    tool, _ = _tool(payload)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation", target_months=[7]))

    assert result.error is None
    assert result.normalized_data["mean_relative_humidity_pct"] is None
    assert any("relative_humidity_2m_mean coverage" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_monthly_summary_omits_a_locally_sparse_optional_metric():
    payload = _payload({1, 7})
    for index, raw_day in enumerate(payload["daily"]["time"]):
        parsed = date.fromisoformat(raw_day)
        if parsed.month == 7 and parsed.day <= 8:
            payload["daily"]["relative_humidity_2m_mean"][index] = None
    tool, _ = _tool(payload)

    result = await tool.run(
        _candidate(),
        PlaceRequestProfile(purpose="vacation", target_months=[1, 7]),
    )

    assert result.error is None
    assert result.normalized_data["mean_relative_humidity_pct"] == 65.0
    summaries = {
        summary["month"]: summary for summary in result.normalized_data["monthly_climatology"]
    }
    assert summaries[1]["mean_relative_humidity_pct"] == 65.0
    assert "mean_relative_humidity_pct" not in summaries[7]


@pytest.mark.asyncio
async def test_stale_cache_is_used_when_live_lookup_fails():
    cached_result = ToolResult(
        tool_name="WeatherTool",
        place="Lisbon",
        normalized_data={"data_kind": "climatology", "avg_high_c": 25.0},
        source_name="Open-Meteo historical weather archive",
        retrieved_at=datetime.now(UTC),
        confidence="medium",
    )
    cache = FakeCache(cached=cached_result.model_dump(mode="json"), stale=True)
    tool, _ = _tool(None, cache=cache, error=RuntimeError("offline"))

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation", target_months=[7]))

    assert result.error is None
    assert result.stale is True
    assert any("stale cached climatology" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_fresh_cache_avoids_live_lookup():
    cached_result = ToolResult(
        tool_name="WeatherTool",
        place="Lisbon",
        normalized_data={"data_kind": "climatology", "avg_high_c": 25.0},
        source_name="Open-Meteo historical weather archive",
        retrieved_at=datetime.now(UTC),
    )
    cache = FakeCache(cached=cached_result.model_dump(mode="json"), stale=False)
    tool, http = _tool(None, cache=cache)

    result = await tool.run(_candidate(), PlaceRequestProfile(purpose="vacation", target_months=[7]))

    assert result.normalized_data["avg_high_c"] == 25.0
    assert http.calls == []


@pytest.mark.asyncio
async def test_missing_coordinates_returns_controlled_error():
    tool, http = _tool(_payload({7}))

    result = await tool.run(_candidate(lat=None, lon=None), PlaceRequestProfile(purpose="vacation"))

    assert result.error == "Cannot fetch climatology without verified coordinates."
    assert http.calls == []
