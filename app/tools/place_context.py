"""D. PlaceContextTool -- concise destination context via the Wikivoyage MediaWiki API.

Only a short, cleaned plain-text excerpt is ever retrieved or stored -- never
a full page.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.core.security import safe_get
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult

WIKIVOYAGE_API = "https://en.wikivoyage.org/w/api.php"
MAX_EXCERPT_CHARS = 600


class PlaceContextTool:
    name = "PlaceContextTool"

    def __init__(self, cache: ToolCache, timeout: float = 10.0):
        self._cache = cache
        self._timeout = timeout

    @retry(reraise=True, stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, max=3))
    async def _fetch(self, title: str) -> dict:
        response = await safe_get(
            WIKIVOYAGE_API,
            params={
                "action": "query",
                "prop": "extracts",
                "exintro": 1,
                "explaintext": 1,
                "format": "json",
                "titles": title,
                "redirects": 1,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        title = candidate.canonical_name or candidate.place_name
        cached, stale = await self._cache.get(self.name, candidate.place_name, {"title": title})
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        try:
            data = await self._fetch(candidate.place_name)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached place-context data; live lookup failed.")
                return stale_result
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="Wikivoyage",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error=f"Place-context lookup failed: {exc}",
            )

        pages = (data.get("query") or {}).get("pages") or {}
        extract = ""
        for page in pages.values():
            extract = (page.get("extract") or "").strip()
            if extract:
                break

        if not extract:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="Wikivoyage",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="No Wikivoyage context article was found for this destination.",
            )

        excerpt = " ".join(extract.split())[:MAX_EXCERPT_CHARS]
        source_url = f"https://en.wikivoyage.org/wiki/{candidate.place_name.replace(' ', '_')}"

        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={"excerpt": excerpt},
            source_name="Wikivoyage",
            source_url=source_url,
            retrieved_at=datetime.now(UTC),
            confidence="medium",
        )
        await self._cache.set(self.name, candidate.place_name, {"title": title}, result.model_dump(mode="json"))
        return result
