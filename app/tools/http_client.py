"""Reusable, SSRF-safe JSON HTTP helpers for external data tools."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.security import safe_get

JsonGet = Callable[..., Awaitable[httpx.Response]]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]
BeforeRequest = Callable[[], Awaitable[None]]

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class AsyncRateLimiter:
    """Serialize calls and enforce a minimum interval between their starts."""

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ):
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_started_at: float | None = None

    async def wait(self) -> None:
        async with self._lock:
            now = self._clock()
            if self._last_started_at is not None:
                delay = self._min_interval - (now - self._last_started_at)
                if delay > 0:
                    await self._sleep(delay)
            self._last_started_at = self._clock()


class JsonHttpClient:
    """Fetch JSON with bounded retries while retaining the outbound allow-list."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        attempts: int = 2,
        get: JsonGet = safe_get,
        sleep: Sleep = asyncio.sleep,
    ):
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self._timeout = timeout
        self._attempts = attempts
        self._get = get
        self._sleep = sleep

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        extra_allowed_domains: set[str] | None = None,
        before_request: BeforeRequest | None = None,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(self._attempts):
            try:
                if before_request is not None:
                    await before_request()
                response = await self._get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self._timeout,
                    extra_allowed_domains=extra_allowed_domains,
                )
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.TransportError, ValueError) as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in RETRYABLE_STATUS_CODES:
                    raise
                last_error = exc

            if attempt + 1 < self._attempts:
                await self._sleep(0.5 * (2**attempt))

        assert last_error is not None
        raise last_error
