"""I. ActivitiesTool -- requested activities/lifestyle fit via OpenStreetMap.

Only queries the specific activity categories mentioned by the user, within a
bounded radius. Never claims live event schedules.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.core.security import safe_get
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RADIUS_M = 5000
MAX_CATEGORIES = 4

ACTIVITY_TAGS: dict[str, str] = {
    "beach": 'node["natural"="beach"]',
    "hiking": 'node["natural"="peak"]',
    "museum": 'node["tourism"="museum"]',
    "nightlife": 'node["amenity"="nightclub"]',
    "historical": 'node["historic"]',
    "park": 'node["leisure"="park"]',
}


def select_activity_categories(profile: PlaceRequestProfile) -> list[str]:
    haystack = " ".join(
        profile.soft_preferences
        + profile.hard_constraints
        + profile.deal_breakers
        + profile.climate_preferences
        + profile.relevant_criteria
    ).lower()
    categories = [cat for cat in ACTIVITY_TAGS if cat in haystack]
    if not categories and profile.purpose in ("vacation", "mixed"):
        categories = ["beach", "museum"]
    return categories[:MAX_CATEGORIES]


class ActivitiesTool:
    name = "ActivitiesTool"

    def __init__(self, cache: ToolCache, timeout: float = 15.0):
        self._cache = cache
        self._timeout = timeout

    @retry(reraise=True, stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, max=5))
    async def _fetch(self, query: str) -> dict:
        response = await safe_get(OVERPASS_URL, params={"data": query}, timeout=self._timeout)
        response.raise_for_status()
        return response.json()

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        if candidate.lat is None or candidate.lon is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Overpass",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="Cannot query activities without verified coordinates.",
            )

        categories = select_activity_categories(profile)
        if not categories:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Overpass",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="No specific activity categories were requested.",
            )

        params = {"lat": candidate.lat, "lon": candidate.lon, "categories": categories}
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        clauses = "".join(
            f'{ACTIVITY_TAGS[c]}(around:{RADIUS_M},{candidate.lat},{candidate.lon});' for c in categories
        )
        query = f"[out:json][timeout:15];({clauses});out count;"

        try:
            data = await self._fetch(query)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached activities data; live lookup failed.")
                return stale_result
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Overpass",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error=f"Activities lookup failed: {exc}",
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
            warnings=["Reflects mapped point-of-interest density, not live event schedules."],
        )
        await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
