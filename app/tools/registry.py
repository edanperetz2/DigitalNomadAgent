"""Tool Registry: concurrent, semaphore-limited dispatch across the tool set.

Individual tool failures never abort the whole request -- they are converted
to a ToolResult with `error` set rather than raised (graceful degradation).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import UTC, datetime

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.core.logging import logger
from app.evidence.models import ToolResult
from app.tools.base import BaseTool


class ToolRegistry:
    def __init__(
        self,
        tools: dict[str, BaseTool],
        max_concurrent_requests: int = 10,
        tool_execution_timeout_seconds: float = 50.0,
    ):
        if tool_execution_timeout_seconds <= 0:
            raise ValueError("tool_execution_timeout_seconds must be positive")
        self._tools = tools
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._tool_execution_timeout_seconds = tool_execution_timeout_seconds

    def get(self, name: str) -> BaseTool:
        return self._tools[name]

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    async def verify_candidates(
        self,
        candidates: list[CandidatePlace],
        profile: PlaceRequestProfile,
        *,
        timeout_seconds: float | None = None,
        request_id: str | None = None,
    ) -> tuple[list[CandidatePlace], list[ToolResult]]:
        """Run GeocodingTool serially; retain completed results at the research cutoff."""
        geocoding = self._tools["GeocodingTool"]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds if timeout_seconds is not None else None

        results = []
        for index, candidate in enumerate(candidates):
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                results.extend(self._timeout_result("GeocodingTool", item) for item in candidates[index:])
                break
            started_at = time.monotonic()
            outcome = "ok"
            try:
                call = geocoding.run(candidate, profile)
                call_timeout = self._tool_execution_timeout_seconds
                if remaining is not None:
                    call_timeout = min(call_timeout, remaining)
                result = await asyncio.wait_for(call, timeout=call_timeout)
                results.append(result)
                outcome = "tool_error" if result.error else "ok"
            except TimeoutError:
                research_budget_expired = remaining is not None and remaining <= self._tool_execution_timeout_seconds
                if research_budget_expired:
                    outcome = "research_cutoff"
                    results.append(self._timeout_result("GeocodingTool", candidate))
                    results.extend(
                        self._timeout_result("GeocodingTool", item) for item in candidates[index + 1 :]
                    )
                else:
                    outcome = "tool_timeout"
                    results.append(self._tool_execution_timeout_result("GeocodingTool", candidate))
            except Exception as exc:  # noqa: BLE001 - one bad candidate must not abort all verification
                outcome = "tool_error"
                results.append(
                    ToolResult(
                        tool_name="GeocodingTool",
                        place=candidate.place_name,
                        source_name="OpenStreetMap Nominatim",
                        source_url="https://nominatim.openstreetmap.org/",
                        retrieved_at=datetime.now(UTC),
                        confidence="low",
                        error=f"Geocoding tool execution failed: {exc}",
                    )
                )
            finally:
                logger.info(
                    "tool_timing request_id=%s tool=GeocodingTool place=%s queue_seconds=0.000 "
                    "run_seconds=%.3f outcome=%s",
                    request_id or "-",
                    candidate.place_name,
                    time.monotonic() - started_at,
                    outcome,
                )
            if outcome == "research_cutoff":
                break

        verified: list[CandidatePlace] = []
        for candidate, result in zip(candidates, results, strict=True):
            if result.error is None and result.normalized_data:
                candidate.verified = True
                candidate.lat = result.normalized_data.get("lat")
                candidate.lon = result.normalized_data.get("lon")
                candidate.canonical_name = result.normalized_data.get("canonical_name")
                candidate.country_code = result.normalized_data.get("country_code")
                candidate.geocoding_importance = result.normalized_data.get("importance")
                verified.append(candidate)
        return verified, list(results)

    @staticmethod
    def _timeout_result(tool_name: str, candidate: CandidatePlace) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            place=candidate.place_name,
            source_name=tool_name,
            retrieved_at=datetime.now(UTC),
            confidence="low",
            error="Tool stopped because the shared research time budget expired.",
        )

    @staticmethod
    def _tool_execution_timeout_result(tool_name: str, candidate: CandidatePlace) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            place=candidate.place_name,
            source_name=tool_name,
            retrieved_at=datetime.now(UTC),
            confidence="low",
            error="Tool stopped after exceeding its per-invocation execution budget.",
        )

    async def run_tools(
        self,
        tool_names: set[str],
        candidates: list[CandidatePlace],
        profile: PlaceRequestProfile,
        *,
        timeout_seconds: float | None = None,
        tool_priorities: dict[str, float] | None = None,
        request_id: str | None = None,
    ) -> dict[str, list[ToolResult]]:
        """Run independent tool/candidate jobs concurrently and retain partial results."""
        names_to_run = tool_names - {"GeocodingTool"}

        async def run_one(tool_name: str, candidate: CandidatePlace) -> ToolResult:
            queued_at = time.monotonic()
            async with self._semaphore:
                started_at = time.monotonic()
                outcome = "ok"
                try:
                    tool = self._tools[tool_name]
                    result = await asyncio.wait_for(
                        tool.run(candidate, profile),
                        timeout=self._tool_execution_timeout_seconds,
                    )
                    outcome = "tool_error" if result.error else "ok"
                    return result
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    raise
                except TimeoutError:
                    outcome = "tool_timeout"
                    return self._tool_execution_timeout_result(tool_name, candidate)
                except Exception as exc:  # noqa: BLE001 - convert any tool crash to a ToolResult
                    outcome = "tool_error"
                    return ToolResult(
                        tool_name=tool_name,
                        place=candidate.place_name,
                        source_name=tool_name,
                        retrieved_at=datetime.now(UTC),
                        confidence="low",
                        error=f"Tool execution failed: {exc}",
                    )
                finally:
                    finished_at = time.monotonic()
                    logger.info(
                        "tool_timing request_id=%s tool=%s place=%s queue_seconds=%.3f "
                        "run_seconds=%.3f outcome=%s",
                        request_id or "-",
                        tool_name,
                        candidate.place_name,
                        started_at - queued_at,
                        finished_at - started_at,
                        outcome,
                    )

        priorities = tool_priorities or {}
        ordered_tool_names = sorted(
            names_to_run,
            key=lambda tool_name: (-priorities.get(tool_name, 0.0), tool_name),
        )

        entries: list[tuple[str, str, CandidatePlace, asyncio.Task[ToolResult]]] = []
        for tool_name in ordered_tool_names:
            for candidate in candidates:
                task = asyncio.create_task(run_one(tool_name, candidate))
                entries.append((candidate.place_name, tool_name, candidate, task))

        if not entries:
            return {}

        tasks = {entry[3] for entry in entries}
        try:
            done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            logger.warning(
                "tool_batch_cutoff request_id=%s completed=%d cancelled=%d timeout_seconds=%s",
                request_id or "-",
                len(done),
                len(pending),
                timeout_seconds,
            )

        grouped: dict[str, list[ToolResult]] = defaultdict(list)
        for key, tool_name, candidate, task in entries:
            result = (
                task.result()
                if task in done and not task.cancelled()
                else self._timeout_result(tool_name, candidate)
            )
            grouped[key].append(result)
        return grouped
