"""B. WeatherTool -- climate/seasonal suitability via Open-Meteo.

Uses the historical archive API to build a same-month-last-year climate
snapshot. This is explicitly labeled as historical/seasonal evidence, never
presented as a live forecast, per the spec's requirement to distinguish
forecasts from climate averages.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

import httpx

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult
from app.tools.http_client import JsonHttpClient

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


class WeatherTool:
    name = "WeatherTool"

    def __init__(self, cache: ToolCache, timeout: float = 10.0, http: JsonHttpClient | None = None):
        self._cache = cache
        self._http = http or JsonHttpClient(timeout=timeout)

    async def _fetch(self, lat: float, lon: float, start: str, end: str) -> dict:
        payload = await self._http.get_json(
            ARCHIVE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start,
                "end_date": end,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "timezone": "UTC",
            },
        )
        if not isinstance(payload, dict):
            raise ValueError("Open-Meteo returned a non-object JSON response")
        return payload

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        if candidate.lat is None or candidate.lon is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="Open-Meteo",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="Cannot fetch weather without verified coordinates.",
            )

        now = datetime.now(UTC)
        last_year = now.year - 1
        month = now.month
        last_day = calendar.monthrange(last_year, month)[1]
        start = f"{last_year}-{month:02d}-01"
        end = f"{last_year}-{month:02d}-{last_day:02d}"
        period_label = f"{calendar.month_name[month]} {last_year} (historical)"

        params = {"lat": candidate.lat, "lon": candidate.lon, "start": start, "end": end}
        cached, stale = await self._cache.get(self.name + ":climate", candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        try:
            data = await self._fetch(candidate.lat, candidate.lon, start, end)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached weather data; live lookup failed.")
                return stale_result
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="Open-Meteo",
                retrieved_at=now,
                confidence="low",
                error=f"Weather lookup failed: {exc}",
            )

        daily = data.get("daily") or {}
        highs = [t for t in daily.get("temperature_2m_max", []) if t is not None]
        lows = [t for t in daily.get("temperature_2m_min", []) if t is not None]
        precip = [p for p in daily.get("precipitation_sum", []) if p is not None]

        if not highs or not lows:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="Open-Meteo",
                retrieved_at=now,
                confidence="low",
                error="No historical weather data was returned for this location.",
            )

        avg_high = sum(highs) / len(highs)
        avg_low = sum(lows) / len(lows)
        avg_precip = sum(precip) / len(precip) if precip else None

        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "avg_high_c": round(avg_high, 1),
                "avg_low_c": round(avg_low, 1),
                "avg_daily_precip_mm": round(avg_precip, 1) if avg_precip is not None else None,
                "data_kind": "climate_normal",
                "period": period_label,
            },
            source_name="Open-Meteo (historical archive)",
            source_url="https://open-meteo.com/",
            retrieved_at=now,
            data_date=period_label,
            confidence="medium",
            warnings=["Based on a single past month, not a multi-year climate normal."],
        )
        await self._cache.set(self.name + ":climate", candidate.place_name, params, result.model_dump(mode="json"))
        return result
