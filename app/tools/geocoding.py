"""A. GeocodingTool -- verifies destination identity via OpenStreetMap Nominatim."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.core.security import safe_get
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MIN_IMPORTANCE_FOR_HIGH_CONFIDENCE = 0.5
MIN_IMPORTANCE_TO_ACCEPT = 0.15


class GeocodingTool:
    name = "GeocodingTool"

    def __init__(self, cache: ToolCache, timeout: float = 10.0):
        self._cache = cache
        self._timeout = timeout

    @retry(reraise=True, stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, max=3))
    async def _fetch(self, query: str) -> list[dict]:
        response = await safe_get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "addressdetails": 1},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        query = f"{candidate.place_name}, {candidate.country}"
        cached, stale = await self._cache.get(self.name, query, {})
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        try:
            results = await self._fetch(query)
        except (httpx.HTTPError, ValueError) as exc:
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached geocoding data; live lookup failed.")
                return stale_result
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Nominatim",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error=f"Geocoding lookup failed: {exc}",
            )

        if not results:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Nominatim",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="This destination could not be reliably verified.",
            )

        top = results[0]
        importance = float(top.get("importance", 0.0))
        if importance < MIN_IMPORTANCE_TO_ACCEPT:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Nominatim",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="This destination could not be reliably verified (ambiguous or low-confidence match).",
            )

        normalized_data = {
            "lat": float(top["lat"]),
            "lon": float(top["lon"]),
            "canonical_name": top.get("display_name", candidate.place_name),
            "country_code": (top.get("address") or {}).get("country_code"),
        }
        confidence = "high" if importance >= MIN_IMPORTANCE_FOR_HIGH_CONFIDENCE else "medium"

        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data=normalized_data,
            source_name="OpenStreetMap Nominatim",
            source_url="https://nominatim.openstreetmap.org/",
            retrieved_at=datetime.now(UTC),
            confidence=confidence,
        )
        await self._cache.set(self.name, query, {}, result.model_dump(mode="json"))
        return result
