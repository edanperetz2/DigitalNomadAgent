"""Shared bounded-concurrency client with public Overpass endpoint failover."""

from __future__ import annotations

import asyncio
from typing import Any

from app.tools.http_client import JsonHttpClient

DEFAULT_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


class OverpassClient:
    def __init__(
        self,
        http: JsonHttpClient | None = None,
        *,
        endpoints: tuple[str, ...] = DEFAULT_OVERPASS_ENDPOINTS,
        max_concurrent_requests: int = 2,
    ):
        if not endpoints:
            raise ValueError("At least one Overpass endpoint is required")
        if max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be at least 1")
        self._http = http or JsonHttpClient(timeout=30.0)
        self._endpoints = endpoints
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

    async def query(self, query: str) -> dict[str, Any]:
        last_error: Exception | None = None
        async with self._semaphore:
            for endpoint in self._endpoints:
                try:
                    payload = await self._http.get_json(endpoint, params={"data": query})
                    if not isinstance(payload, dict):
                        raise ValueError("Overpass returned a non-object JSON response")
                    return payload
                except Exception as exc:  # noqa: BLE001 - fail over, then expose the final error
                    last_error = exc
        assert last_error is not None
        raise last_error
