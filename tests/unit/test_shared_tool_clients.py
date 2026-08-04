import asyncio

import httpx
import pytest

from app.tools.mediawiki_client import MediaWikiClient
from app.tools.overpass_client import OverpassClient


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
async def test_overpass_client_default_endpoints_get_documented_limits():
    from app.tools.overpass_client import DEFAULT_OVERPASS_ENDPOINTS, ENDPOINT_CONCURRENCY

    client = OverpassClient(http=object())
    for endpoint in DEFAULT_OVERPASS_ENDPOINTS:
        semaphore = client._semaphores[endpoint]
        assert semaphore._value == ENDPOINT_CONCURRENCY[endpoint]
