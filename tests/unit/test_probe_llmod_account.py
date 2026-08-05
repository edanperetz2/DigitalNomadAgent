"""Tests for the read-only LLMod.ai account probe.

The probe must never make a paid call. These assert it only ever issues GETs,
never touches the chat-completions path, tolerates missing endpoints, and does
not leak the API key.
"""

import httpx
import pytest

from scripts.probe_llmod_account import PROBE_ENDPOINTS, format_report, probe


class _Settings:
    llmod_api_key = "secret-key-abc"
    llmod_base_url = "https://api.llmod.ai"
    llmod_auth_header = "Authorization"
    llmod_auth_scheme = "Bearer"
    llmod_model = "some-model"
    http_timeout_seconds = 10.0


@pytest.fixture
def captured(monkeypatch):
    """Record every request the probe makes and serve canned responses.

    Patches `AsyncClient.send` rather than installing a MockTransport, because
    conftest's autouse `_block_real_network` fixture already patches `send` at
    class level -- a transport would never be reached. This fixture is
    requested explicitly, so it is set up after the autouse one and wins.
    """
    calls: list[httpx.Request] = []
    responses: dict[str, tuple[int, dict]] = {}

    async def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        calls.append(request)
        status, body = responses.get(request.url.path, (404, {"error": "not found"}))
        return httpx.Response(status, json=body, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    return calls, responses


@pytest.mark.asyncio
async def test_probe_makes_only_get_requests(captured):
    calls, _ = captured
    await probe(_Settings())
    assert calls, "probe issued no requests"
    assert {c.method for c in calls} == {"GET"}


@pytest.mark.asyncio
async def test_probe_never_calls_chat_completions(captured):
    """The whole point: this must cost $0."""
    calls, _ = captured
    await probe(_Settings())
    assert all("chat/completions" not in str(c.url) for c in calls)


@pytest.mark.asyncio
async def test_probe_sends_the_configured_auth_header(captured):
    calls, _ = captured
    await probe(_Settings())
    assert calls[0].headers["Authorization"] == "Bearer secret-key-abc"


@pytest.mark.asyncio
async def test_probe_visits_every_declared_endpoint(captured):
    calls, _ = captured
    await probe(_Settings())
    assert {c.url.path for c in calls} == {path for path, _ in PROBE_ENDPOINTS}


@pytest.mark.asyncio
async def test_missing_endpoints_are_reported_not_raised(captured):
    """A 404 on an endpoint this proxy doesn't implement is information."""
    _, responses = captured
    responses["/v1/models"] = (200, {"data": [{"id": "gpt-4o-mini"}]})

    report = await probe(_Settings())

    by_path = {e["path"]: e for e in report["endpoints"]}
    assert by_path["/v1/models"]["ok"] is True
    assert by_path["/key/info"]["ok"] is False


@pytest.mark.asyncio
async def test_key_info_spend_and_budget_are_summarized(captured):
    _, responses = captured
    responses["/key/info"] = (200, {"info": {"spend": 2.5, "max_budget": 13.0}})

    report = await probe(_Settings())
    rendered = format_report(report)

    assert "Spend (this key)" in rendered
    assert "2.5" in rendered
    assert "10.5000" in rendered  # remaining


@pytest.mark.asyncio
async def test_the_account_budget_is_reported_when_the_key_carries_none(captured):
    """The real shape, and the one D19 got wrong.

    The key has no cap while the account carries the $13 one, so a probe that
    summarizes /key/info alone shows no budget at all and concludes none exists.
    Account spend also runs ahead of the key's, so it is the number to quote.
    """
    _, responses = captured
    responses["/key/info"] = (200, {"info": {"spend": 0.8622, "max_budget": None}})
    responses["/user/info"] = (200, {"user_info": {"spend": 0.9985, "max_budget": 13.0}})

    rendered = format_report(await probe(_Settings()))

    assert "Spend (this key)" in rendered and "0.8622" in rendered
    assert "Spend (account)" in rendered and "0.9985" in rendered
    assert "Account budget" in rendered and "13.0" in rendered
    assert "12.0015" in rendered  # remaining, against the account cap


@pytest.mark.asyncio
async def test_model_list_is_rendered(captured):
    _, responses = captured
    responses["/v1/models"] = (200, {"data": [{"id": "gpt-4o-mini"}, {"id": "claude-sonnet-4"}]})

    rendered = format_report(await probe(_Settings()))

    assert "gpt-4o-mini" in rendered
    assert "claude-sonnet-4" in rendered


@pytest.mark.asyncio
async def test_report_does_not_leak_the_api_key(captured):
    _, responses = captured
    responses["/key/info"] = (200, {"info": {"spend": 1.0, "max_budget": 13.0}})

    rendered = format_report(await probe(_Settings()))

    assert "secret-key-abc" not in rendered


@pytest.mark.asyncio
async def test_missing_api_key_exits_with_guidance():
    class NoKey(_Settings):
        llmod_api_key = None

    with pytest.raises(SystemExit) as exc_info:
        await probe(NoKey())

    assert "LLMOD_API_KEY" in str(exc_info.value)
