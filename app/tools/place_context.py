"""D. PlaceContextTool -- concise destination context via the Wikivoyage MediaWiki API.

Only a short, cleaned plain-text excerpt is ever retrieved or stored -- never
a full page.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult
from app.tools.http_client import JsonHttpClient
from app.tools.mediawiki_client import WIKIVOYAGE_API, MediaWikiClient

MAX_EXCERPT_CHARS = 600


class PlaceContextTool:
    name = "PlaceContextTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 10.0,
        mediawiki: MediaWikiClient | None = None,
    ):
        self._cache = cache
        self._mediawiki = mediawiki or MediaWikiClient(WIKIVOYAGE_API, JsonHttpClient(timeout=timeout))

    async def _fetch(self, title: str) -> dict:
        return await self._mediawiki.request(
            action="query",
            prop="extracts",
            exintro=1,
            explaintext=1,
            titles=title,
            redirects=1,
            formatversion=1,
        )

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        title = candidate.canonical_name or candidate.place_name
        cached, stale = await self._cache.get(self.name, candidate.place_name, {"title": title})
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        try:
            data = await self._fetch(title)
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
        source_url = f"https://en.wikivoyage.org/wiki/{title.replace(' ', '_')}"

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
