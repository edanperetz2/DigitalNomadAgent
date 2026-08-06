import asyncio

import httpx
import pytest

from app.tools.mediawiki_client import MediaWikiClient
from app.tools.overpass_client import (
    ENDPOINT_COOLDOWN_SECONDS,
    ENDPOINT_FAILURES_BEFORE_COOLDOWN,
    OverpassClient,
)


@pytest.mark.asyncio
async def test_mediawiki_client_adds_standard_response_parameters():
    class RecordingHttp:
        async def get_json(self, url, **kwargs):
            self.url = url
            self.params = kwargs["params"]
            return {"query": {}}

    http = RecordingHttp()
    client = MediaWikiClient("https://en.wikivoyage.org/w/api.php", http=http)

    await client.request(action="query", titles="Lisbon")

    assert http.url == "https://en.wikivoyage.org/w/api.php"
    assert http.params == {
        "format": "json",
        "formatversion": 2,
        "action": "query",
        "titles": "Lisbon",
    }


@pytest.mark.asyncio
async def test_overpass_client_fails_over_to_next_endpoint():
    class FailingThenSuccessfulHttp:
        def __init__(self):
            self.urls = []

        async def get_json(self, url, **kwargs):
            self.urls.append(url)
            if len(self.urls) == 1:
                raise RuntimeError("first endpoint unavailable")
            return {"elements": []}

    http = FailingThenSuccessfulHttp()
    client = OverpassClient(http=http, endpoints=("https://first.example", "https://second.example"))

    result = await client.query("[out:json];node(0,0,1,1);out;")

    assert result == {"elements": []}
    assert http.urls == ["https://first.example", "https://second.example"]


@pytest.mark.asyncio
async def test_overpass_client_fails_over_after_rate_error():
    class RateLimitedThenSuccessfulHttp:
        def __init__(self):
            self.urls = []

        async def get_json(self, url, **kwargs):
            self.urls.append(url)
            if len(self.urls) == 1:
                request = httpx.Request("GET", url)
                response = httpx.Response(429, request=request)
                raise httpx.HTTPStatusError("rate limited", request=request, response=response)
            return {"elements": []}

    http = RateLimitedThenSuccessfulHttp()
    client = OverpassClient(http=http, endpoints=("https://first.example", "https://second.example"))

    assert await client.query("[out:json];node(0,0,1,1);out;") == {"elements": []}
    assert http.urls == ["https://first.example", "https://second.example"]


@pytest.mark.asyncio
async def test_overpass_client_limits_concurrency_to_two():
    class TrackingHttp:
        def __init__(self):
            self.active = 0
            self.maximum_active = 0

        async def get_json(self, url, **kwargs):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return {"elements": []}

    http = TrackingHttp()
    client = OverpassClient(http=http, endpoints=("https://overpass.example",), max_concurrent_requests=2)

    await asyncio.gather(*(client.query(f"query-{index}") for index in range(5)))

    assert http.maximum_active == 2

@pytest.mark.asyncio
async def test_overpass_client_rotates_starting_endpoint_across_queries():
    class RecordingHttp:
        def __init__(self):
            self.urls = []

        async def get_json(self, url, **kwargs):
            self.urls.append(url)
            return {"elements": []}

    http = RecordingHttp()
    client = OverpassClient(http=http, endpoints=("https://first.example", "https://second.example"))

    await client.query("q1")
    await client.query("q2")
    await client.query("q3")

    assert http.urls == ["https://first.example", "https://second.example", "https://first.example"]


@pytest.mark.asyncio
async def test_overpass_client_concurrency_limits_are_per_endpoint():
    class TrackingHttp:
        def __init__(self):
            self.active = {}
            self.maximum_active = {}

        async def get_json(self, url, **kwargs):
            self.active[url] = self.active.get(url, 0) + 1
            self.maximum_active[url] = max(self.maximum_active.get(url, 0), self.active[url])
            await asyncio.sleep(0.01)
            self.active[url] -= 1
            return {"elements": []}

    http = TrackingHttp()
    client = OverpassClient(
        http=http, endpoints=("https://first.example", "https://second.example"), max_concurrent_requests=2
    )

    await asyncio.gather(*(client.query(f"query-{index}") for index in range(8)))

    # Round-robin sends 4 queries to each endpoint; each endpoint's own limit
    # holds independently, so total in-flight work can exceed a single limit.
    assert http.maximum_active["https://first.example"] == 2
    assert http.maximum_active["https://second.example"] == 2


@pytest.mark.asyncio
async def test_overpass_client_stops_dispatching_to_a_dead_endpoint():
    """A mirror that keeps timing out must stop costing the caller its budget.

    Measured 2026-08-06: overpass.kumi.systems timed out on every attempt while
    overpass-api.de answered in 4s. Round-robin still sent it every other query,
    at ~22s burned each time, so a 50s per-invocation tool cap was gone before
    the working endpoint was reached.
    """

    class OneDeadEndpointHttp:
        def __init__(self):
            self.urls = []

        async def get_json(self, url, **kwargs):
            self.urls.append(url)
            if url == "https://dead.example":
                raise httpx.ReadTimeout("timed out")
            return {"elements": []}

    http = OneDeadEndpointHttp()
    client = OverpassClient(http=http, endpoints=("https://live.example", "https://dead.example"))

    for _ in range(6):
        assert await client.query("q") == {"elements": []}

    # Two failures trip the cooldown; every query after that skips it entirely.
    assert http.urls.count("https://dead.example") == ENDPOINT_FAILURES_BEFORE_COOLDOWN
    assert http.urls[-1] == "https://live.example"


@pytest.mark.asyncio
async def test_overpass_client_still_tries_every_endpoint_when_all_are_cooling():
    """A health record is a routing hint, never a veto.

    If the cooldown could suppress an attempt outright, a transient wobble that
    tripped both endpoints would leave the client permanently refusing to ask
    anyone -- turning a recoverable outage into a total one.
    """

    class DownThenUpHttp:
        def __init__(self):
            self.urls = []
            self.fail = True

        async def get_json(self, url, **kwargs):
            self.urls.append(url)
            if self.fail:
                raise httpx.ReadTimeout("timed out")
            return {"elements": []}

    http = DownThenUpHttp()
    client = OverpassClient(http=http, endpoints=("https://first.example", "https://second.example"))

    for _ in range(2):
        with pytest.raises(httpx.ReadTimeout):
            await client.query("q")

    http.urls.clear()
    http.fail = False
    assert await client.query("q") == {"elements": []}
    assert http.urls, "a client with every endpoint in cooldown must still attempt one"


@pytest.mark.asyncio
async def test_overpass_client_success_clears_a_previous_failure():
    """One blip must not sideline an endpoint that is working."""

    class BlipThenHealthyHttp:
        def __init__(self):
            self.urls = []

        async def get_json(self, url, **kwargs):
            self.urls.append(url)
            if len(self.urls) == 1:
                raise httpx.ReadTimeout("one blip")
            return {"elements": []}

    http = BlipThenHealthyHttp()
    client = OverpassClient(http=http, endpoints=("https://only.example",))

    with pytest.raises(httpx.ReadTimeout):
        await client.query("q1")
    assert await client.query("q2") == {"elements": []}
    assert await client.query("q3") == {"elements": []}

    # The blip left no residue: the counter reset on the first success, so a
    # later single failure would start counting from zero again.
    assert client._consecutive_failures["https://only.example"] == 0


@pytest.mark.asyncio
async def test_overpass_client_cooldown_expires():
    """Cooldown is a pause, not a permanent removal."""

    class AlwaysDeadHttp:
        def __init__(self):
            self.urls = []

        async def get_json(self, url, **kwargs):
            self.urls.append(url)
            raise httpx.ReadTimeout("timed out")

    now = [0.0]
    http = AlwaysDeadHttp()
    client = OverpassClient(
        http=http, endpoints=("https://only.example",), clock=lambda: now[0]
    )

    for _ in range(ENDPOINT_FAILURES_BEFORE_COOLDOWN):
        with pytest.raises(httpx.ReadTimeout):
            await client.query("q")

    assert client._in_cooldown("https://only.example")
    now[0] += ENDPOINT_COOLDOWN_SECONDS + 1
    assert not client._in_cooldown("https://only.example")


@pytest.mark.asyncio
async def test_overpass_client_default_endpoints_get_documented_limits():
    from app.tools.overpass_client import DEFAULT_OVERPASS_ENDPOINTS, ENDPOINT_CONCURRENCY

    client = OverpassClient(http=object())
    for endpoint in DEFAULT_OVERPASS_ENDPOINTS:
        semaphore = client._semaphores[endpoint]
        assert semaphore._value == ENDPOINT_CONCURRENCY[endpoint]
