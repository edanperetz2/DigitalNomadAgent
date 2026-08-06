"""Shared bounded-concurrency client with public Overpass endpoint failover."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from app.tools.http_client import JsonHttpClient

DEFAULT_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

# overpass-api.de asks for at most 2 concurrent requests per IP; the
# kumi.systems mirror advertises no such limit and tolerates more. A single
# global Semaphore(2) made every Overpass-backed tool/candidate pair queue for
# the same two slots, so with ~16-32 jobs per request most spent their entire
# 50s execution budget waiting (verification run, 2026-08-04: AmenitiesTool and
# LocalMobilityTool timed out on essentially every invocation). Per-endpoint
# limits plus round-robin dispatch triple the usable slots while staying polite
# to each endpoint individually.
ENDPOINT_CONCURRENCY: dict[str, int] = {
    "https://overpass-api.de/api/interpreter": 2,
    "https://overpass.kumi.systems/api/interpreter": 4,
}

# Overpass needs its own HTTP budget. The shared JsonHttpClient is tuned for fast
# REST lookups (Nominatim, Open-Meteo) at 10s, but a counted city-radius query
# legitimately takes 3-25s, so that client aborted nearly every request -- and
# with 2 retries across 2 endpoints a single tool call burned ~83s and still
# failed, which is why no Overpass-backed tool ever completed inside its 50s cap.
#
# The client budget sits just above the server-side one so Overpass's own timeout
# fires first and returns a partial result with a `remark` (useful evidence)
# rather than the client aborting blind. One attempt only: a query that exhausted
# its server budget will not succeed on immediate retry, and retrying just
# consumes the caller's cap. Worst case is now 2 endpoints x 22s = 44s, inside
# the 50s per-invocation cap.
COUNTED_QUERY_TIMEOUT_SECONDS = 20
OVERPASS_HTTP_TIMEOUT_SECONDS = 22
OVERPASS_HTTP_ATTEMPTS = 1

# Failover with no memory re-pays the full timeout for a dead endpoint on every
# single query. Measured 2026-08-06: overpass-api.de answered a counted query in
# 4.0s while overpass.kumi.systems timed out on every attempt (25.4s, 30.4s,
# 32.2s) -- and because dispatch is round-robin, half of all queries went to the
# dead mirror *first*. At ~22s burned per attempt against a 50s per-invocation
# tool cap, two such attempts exhaust the budget before the working endpoint is
# ever reached, which is why `counts_by_category` came back empty for nearly
# every candidate across a full ten-prompt run while Overpass itself was up.
#
# So the client remembers. An endpoint that fails this many times in a row is
# skipped for the cooldown, and any success clears the count immediately. It
# only ever *reorders*: when every endpoint is in cooldown they are all still
# tried, because a stale health record must never be the reason a query is not
# attempted at all.
ENDPOINT_FAILURES_BEFORE_COOLDOWN = 2
ENDPOINT_COOLDOWN_SECONDS = 300.0


def build_counted_query(
    selector_groups: list[list[str]], *, timeout_seconds: int = COUNTED_QUERY_TIMEOUT_SECONDS
) -> str:
    """One Overpass request returning a count per selector group.

    Every tool here only ever needs "how many of X are near this place", yet the
    original queries ended in `out tags;`, which serializes and ships every
    matching element. Measured against overpass-api.de for a 3 km radius on
    Berlin: `out tags` returned 504 Gateway Timeout for amenities and 9.6 MiB /
    28,150 elements for local mobility, while the identical selectors ending in
    `out count;` answered in a few seconds with a 0.5 KiB body. The spatial
    search was never the bottleneck -- serializing the results was.

    Each group becomes its own named set so counts come back per category, in
    the order the groups were supplied.
    """
    if not selector_groups:
        raise ValueError("At least one selector group is required")

    parts: list[str] = []
    for index, clauses in enumerate(selector_groups):
        if not clauses:
            raise ValueError(f"Selector group {index} is empty")
        parts.append("(" + "".join(clauses) + f")->.g{index};")
    parts.extend(f".g{index} out count;" for index in range(len(selector_groups)))
    return f"[out:json][timeout:{timeout_seconds}];" + "".join(parts)


def parse_counts(payload: dict[str, Any], expected_groups: int) -> list[int]:
    """Read one integer per counted set, in group order.

    Overpass answers `out count` with elements of type "count" whose tags carry
    the totals. Raises rather than silently returning zeros: a zero count means
    "none nearby", which is real evidence, and must never be confused with a
    failed lookup.
    """
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise ValueError("Overpass returned no usable elements list.")

    counts: list[int] = []
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "count":
            continue
        tags = element.get("tags")
        if not isinstance(tags, dict):
            continue
        try:
            counts.append(int(tags.get("total", 0)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Overpass returned a non-numeric count: {tags.get('total')!r}") from exc

    if len(counts) != expected_groups:
        raise ValueError(
            f"Overpass returned {len(counts)} count(s) for {expected_groups} selector group(s)."
        )
    return counts


class OverpassClient:
    def __init__(
        self,
        http: JsonHttpClient | None = None,
        *,
        endpoints: tuple[str, ...] = DEFAULT_OVERPASS_ENDPOINTS,
        max_concurrent_requests: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not endpoints:
            raise ValueError("At least one Overpass endpoint is required")
        if max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be at least 1")
        self._http = http or JsonHttpClient(
            timeout=OVERPASS_HTTP_TIMEOUT_SECONDS, attempts=OVERPASS_HTTP_ATTEMPTS
        )
        self._endpoints = endpoints
        # `max_concurrent_requests` is the per-endpoint limit for endpoints not
        # listed in ENDPOINT_CONCURRENCY (custom endpoints in tests).
        self._semaphores = {
            endpoint: asyncio.Semaphore(ENDPOINT_CONCURRENCY.get(endpoint, max_concurrent_requests))
            for endpoint in endpoints
        }
        self._next_start_index = 0
        self._clock = clock
        self._consecutive_failures = dict.fromkeys(endpoints, 0)
        self._cooldown_until = dict.fromkeys(endpoints, 0.0)

    def _in_cooldown(self, endpoint: str) -> bool:
        return self._clock() < self._cooldown_until[endpoint]

    def _record_failure(self, endpoint: str) -> None:
        self._consecutive_failures[endpoint] += 1
        if self._consecutive_failures[endpoint] >= ENDPOINT_FAILURES_BEFORE_COOLDOWN:
            self._cooldown_until[endpoint] = self._clock() + ENDPOINT_COOLDOWN_SECONDS

    def _record_success(self, endpoint: str) -> None:
        self._consecutive_failures[endpoint] = 0
        self._cooldown_until[endpoint] = 0.0

    def _dispatch_order(self) -> tuple[str, ...]:
        """Healthy endpoints in rotation, then any in cooldown as a last resort.

        Rotation advances over the healthy set only, so traffic spreads across
        the endpoints that are actually answering rather than being handed to a
        dead one every other query.
        """
        healthy = tuple(e for e in self._endpoints if not self._in_cooldown(e))
        cooling = tuple(e for e in self._endpoints if self._in_cooldown(e))
        if not healthy:
            # Everything is in cooldown. Try them all anyway, oldest-cooldown
            # first: a health record is a routing hint, never a veto.
            return tuple(sorted(self._endpoints, key=lambda e: self._cooldown_until[e]))
        start = self._next_start_index % len(healthy)
        self._next_start_index = start + 1
        return healthy[start:] + healthy[:start] + cooling

    async def query(self, query: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for endpoint in self._dispatch_order():
            async with self._semaphores[endpoint]:
                try:
                    payload = await self._http.get_json(endpoint, params={"data": query})
                    if not isinstance(payload, dict):
                        raise ValueError("Overpass returned a non-object JSON response")
                except Exception as exc:  # noqa: BLE001 - fail over, then expose the final error
                    self._record_failure(endpoint)
                    last_error = exc
                else:
                    self._record_success(endpoint)
                    return payload
        assert last_error is not None
        raise last_error
