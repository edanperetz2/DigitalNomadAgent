"""C. AmenitiesTool -- infrastructure/amenity density via OpenStreetMap Overpass.

Only queries categories relevant to the current request, within a bounded
radius, as a single lightweight request (count-only, not full geometry).
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
RADIUS_M = 3000
MAX_CATEGORIES = 4

CATEGORY_TAGS: dict[str, str] = {
    "coworking": 'node["office"="coworking"]',
    "cafe": 'node["amenity"="cafe"]',
    "university": 'node["amenity"="university"]',
    "library": 'node["amenity"="library"]',
    "hospital": 'node["amenity"="hospital"]',
    "park": 'node["leisure"="park"]',
    "beach": 'node["natural"="beach"]',
    "museum": 'node["tourism"="museum"]',
    "public_transit": 'node["highway"="bus_stop"]',
    "train_station": 'node["railway"="station"]',
}

_CRITERION_TO_CATEGORIES: dict[str, list[str]] = {
    "work_infrastructure": ["coworking", "cafe"],
    "student_life": ["university", "library"],
    "activities": ["park", "beach", "museum"],
    "transportation": ["public_transit", "train_station"],
    "culture": ["museum"],
}


def select_categories(profile: PlaceRequestProfile) -> list[str]:
    categories: list[str] = []
    for criterion in profile.relevant_criteria:
        for cat in _CRITERION_TO_CATEGORIES.get(criterion, []):
            if cat not in categories:
                categories.append(cat)
    if not categories:
        categories = ["cafe", "public_transit"]
    return categories[:MAX_CATEGORIES]


class AmenitiesTool:
    name = "AmenitiesTool"

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
                error="Cannot query amenities without verified coordinates.",
            )

        categories = select_categories(profile)
        params = {"lat": candidate.lat, "lon": candidate.lon, "categories": categories}
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        clauses = "".join(
            f'{CATEGORY_TAGS[c]}(around:{RADIUS_M},{candidate.lat},{candidate.lon});' for c in categories
        )
        query = f"[out:json][timeout:15];({clauses});out count;"

        try:
            data = await self._fetch(query)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached amenities data; live lookup failed.")
                return stale_result
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Overpass",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error=f"Amenities lookup failed: {exc}",
            )

        elements = data.get("elements", [])
        count = 0
        if elements and "tags" in elements[0]:
            count = int(elements[0]["tags"].get("total", 0))

        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={"categories": categories, "count": count, "radius_m": RADIUS_M},
            source_name="OpenStreetMap Overpass",
            source_url="https://overpass-api.de/",
            retrieved_at=datetime.now(UTC),
            confidence="medium",
        )
        await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
