"""Tool Registry: concurrent, semaphore-limited dispatch across the tool set.

Individual tool failures never abort the whole request -- they are converted
to a ToolResult with `error` set rather than raised (graceful degradation).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.tools.base import BaseTool


class ToolRegistry:
    def __init__(self, tools: dict[str, BaseTool], max_concurrent_requests: int = 5):
        self._tools = tools
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

    def get(self, name: str) -> BaseTool:
        return self._tools[name]

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    async def verify_candidates(
        self, candidates: list[CandidatePlace], profile: PlaceRequestProfile
    ) -> tuple[list[CandidatePlace], list[ToolResult]]:
        """Run GeocodingTool for every candidate; keep only verified ones."""
        geocoding = self._tools["GeocodingTool"]

        results = []
        for candidate in candidates:
            results.append(await geocoding.run(candidate, profile))

        verified: list[CandidatePlace] = []
        for candidate, result in zip(candidates, results, strict=True):
            if result.error is None and result.normalized_data:
                candidate.verified = True
                candidate.lat = result.normalized_data.get("lat")
                candidate.lon = result.normalized_data.get("lon")
                candidate.canonical_name = result.normalized_data.get("canonical_name")
                candidate.country_code = result.normalized_data.get("country_code")
                verified.append(candidate)
        return verified, list(results)

    async def run_tools(
        self,
        tool_names: set[str],
        candidates: list[CandidatePlace],
        profile: PlaceRequestProfile,
    ) -> dict[str, list[ToolResult]]:
        """Run the given (non-geocoding) tools for each candidate concurrently."""
        names_to_run = tool_names - {"GeocodingTool"}

        async def run_one(tool_name: str, candidate: CandidatePlace) -> ToolResult:
            async with self._semaphore:
                tool = self._tools[tool_name]
                try:
                    return await tool.run(candidate, profile)
                except Exception as exc:  # noqa: BLE001 - convert any tool crash to a ToolResult
                    return ToolResult(
                        tool_name=tool_name,
                        place=candidate.place_name,
                        source_name=tool_name,
                        retrieved_at=datetime.now(UTC),
                        confidence="low",
                        error=f"Tool execution failed: {exc}",
                    )

        tasks = []
        keys: list[str] = []
        for candidate in candidates:
            for tool_name in names_to_run:
                tasks.append(run_one(tool_name, candidate))
                keys.append(candidate.place_name)

        results = await asyncio.gather(*tasks) if tasks else []

        grouped: dict[str, list[ToolResult]] = defaultdict(list)
        for key, result in zip(keys, results, strict=True):
            grouped[key].append(result)
        return grouped
