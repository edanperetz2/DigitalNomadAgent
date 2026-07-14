"""H. AccessibilityTool -- practical access to a destination via OpenStreetMap.

Distinguishes infrastructure presence (airports/stations nearby) from actual
live connection availability, which this tool never claims to know.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult
from app.tools.http_client import JsonHttpClient
from app.tools.overpass_client import OverpassClient

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
AIRPORT_RADIUS_M = 50_000
STATION_RADIUS_M = 5_000


class AccessibilityTool:
    name = "AccessibilityTool"

    def __init__(self, cache: ToolCache, timeout: float = 15.0, overpass: OverpassClient | None = None):
        self._cache = cache
        self._overpass = overpass or OverpassClient(JsonHttpClient(timeout=timeout))

    async def _fetch(self, query: str) -> dict:
        return await self._overpass.query(query)

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        if candidate.lat is None or candidate.lon is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Overpass",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="Cannot evaluate accessibility without verified coordinates.",
            )

        params = {"lat": candidate.lat, "lon": candidate.lon}
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        query = (
            "[out:json][timeout:15];"
            f'(node["aeroway"="aerodrome"](around:{AIRPORT_RADIUS_M},{candidate.lat},{candidate.lon}););'
            "out count;"
            f'(node["railway"="station"](around:{STATION_RADIUS_M},{candidate.lat},{candidate.lon}););'
            "out count;"
        )

        try:
            data = await self._fetch(query)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached accessibility data; live lookup failed.")
                return stale_result
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Overpass",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error=f"Accessibility lookup failed: {exc}",
            )

        elements = data.get("elements", [])
        counts = [int(e["tags"].get("total", 0)) for e in elements if "tags" in e]
        airports = counts[0] if len(counts) > 0 else 0
        stations = counts[1] if len(counts) > 1 else 0

        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "airports_within_50km": airports,
                "train_stations_within_5km": stations,
                "likely_car_dependent": stations == 0 and airports == 0,
            },
            source_name="OpenStreetMap Overpass",
            source_url="https://overpass-api.de/",
            retrieved_at=datetime.now(UTC),
            confidence="medium",
            warnings=[
                "Reflects infrastructure presence only, not live flight/train availability, "
                "schedules, or ticket prices."
            ],
        )
        await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
