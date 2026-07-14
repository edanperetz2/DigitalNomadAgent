import asyncio

import httpx
import pytest

from app.tools.http_client import AsyncRateLimiter, JsonHttpClient


def _response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", "https://example.com"))


@pytest.mark.asyncio
async def test_json_http_client_retries_retryable_status():
    responses = [_response(503, {"error": "busy"}), _response(200, {"ok": True})]
    sleeps = []

    async def fake_get(*args, **kwargs):
        return responses.pop(0)

    async def fake_sleep(delay):
        sleeps.append(delay)

    client = JsonHttpClient(get=fake_get, sleep=fake_sleep)

    assert await client.get_json("https://example.com") == {"ok": True}
    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_json_http_client_does_not_retry_non_retryable_status():
    calls = 0

    async def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _response(404, {"error": "missing"})

    client = JsonHttpClient(get=fake_get)

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_json("https://example.com")
    assert calls == 1


@pytest.mark.asyncio
async def test_json_http_client_runs_hook_before_every_retry_attempt():
    responses = [_response(503, {"error": "busy"}), _response(200, {"ok": True})]
    hook_calls = 0

    async def fake_get(*args, **kwargs):
        return responses.pop(0)

    async def before_request():
        nonlocal hook_calls
        hook_calls += 1

    async def no_sleep(delay):
        return None

    client = JsonHttpClient(get=fake_get, sleep=no_sleep)

    await client.get_json("https://example.com", before_request=before_request)

    assert hook_calls == 2


@pytest.mark.asyncio
async def test_rate_limiter_serializes_and_spaces_call_starts():
    now = 0.0
    sleeps = []

    def clock():
        return now

    async def fake_sleep(delay):
        nonlocal now
        sleeps.append(delay)
        now += delay
        await asyncio.sleep(0)

    limiter = AsyncRateLimiter(1.0, clock=clock, sleep=fake_sleep)

    await asyncio.gather(limiter.wait(), limiter.wait(), limiter.wait())

    assert sleeps == [1.0, 1.0]
    assert now == 2.0
