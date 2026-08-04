"""Contract tests for the real LLMod.ai client.

No network: `_post` is replaced per-test with a stub returning a constructed
httpx.Response, so `complete()`'s parsing, cost extraction, and payload shape
are exercised without a key or a paid call.
"""

import httpx
import pytest

from app.core.exceptions import ConfigurationError, LLMOutputError
from app.llm.llmod import COST_RESPONSE_HEADER, LLModClient

_MESSAGES = [{"role": "user", "content": "hi"}]


def _client(**overrides) -> LLModClient:
    defaults = dict(
        api_key="test-key",
        base_url="https://api.llmod.ai",
        chat_completions_path="/v1/chat/completions",
        model="test-model",
        auth_header="Authorization",
        auth_scheme="Bearer",
        timeout_seconds=60.0,
    )
    defaults.update(overrides)
    return LLModClient(**defaults)


def _response(*, body: dict | None = None, headers: dict | None = None, status_code: int = 200) -> httpx.Response:
    payload = body if body is not None else {"choices": [{"message": {"content": "hello"}}]}
    return httpx.Response(
        status_code,
        json=payload,
        headers=headers or {},
        request=httpx.Request("POST", "https://api.llmod.ai/v1/chat/completions"),
    )


def _stub_post(client: LLModClient, response: httpx.Response) -> list[dict]:
    """Replace _post, capturing the payloads it was called with."""
    sent: list[dict] = []

    async def fake_post(payload):
        sent.append(payload)
        return response

    client._post = fake_post
    return sent


# --- cost extraction (the reason the ledger recorded $0.00) -------------------


@pytest.mark.asyncio
async def test_provider_cost_read_from_response_header():
    client = _client()
    _stub_post(client, _response(headers={COST_RESPONSE_HEADER: "0.00123"}))

    result = await client.complete(_MESSAGES, max_output_tokens=100)

    assert result.provider_cost_usd == pytest.approx(0.00123)


@pytest.mark.asyncio
async def test_cost_header_takes_precedence_over_body_field():
    client = _client()
    _stub_post(
        client,
        _response(
            body={"choices": [{"message": {"content": "hello"}}], "cost_usd": 9.99},
            headers={COST_RESPONSE_HEADER: "0.25"},
        ),
    )

    result = await client.complete(_MESSAGES, max_output_tokens=100)

    assert result.provider_cost_usd == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_falls_back_to_body_cost_when_header_absent():
    client = _client()
    _stub_post(client, _response(body={"choices": [{"message": {"content": "hello"}}], "cost": 0.5}))

    result = await client.complete(_MESSAGES, max_output_tokens=100)

    assert result.provider_cost_usd == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_zero_cost_is_recorded_as_zero_not_unknown():
    """A genuine $0.00 must not be mistaken for "unknown" and locally estimated."""
    client = _client()
    _stub_post(client, _response(headers={COST_RESPONSE_HEADER: "0"}))

    result = await client.complete(_MESSAGES, max_output_tokens=100)

    assert result.provider_cost_usd == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["not-a-number", "", "-1.0"])
async def test_unparseable_or_negative_cost_falls_back_to_unknown(bad):
    """A malformed cost must never fail an otherwise good response."""
    client = _client()
    _stub_post(client, _response(headers={COST_RESPONSE_HEADER: bad}))

    result = await client.complete(_MESSAGES, max_output_tokens=100)

    assert result.provider_cost_usd is None
    assert result.text == "hello"


@pytest.mark.asyncio
async def test_missing_cost_everywhere_is_none():
    client = _client()
    _stub_post(client, _response())

    result = await client.complete(_MESSAGES, max_output_tokens=100)

    assert result.provider_cost_usd is None


# --- payload shape -----------------------------------------------------------


@pytest.mark.asyncio
async def test_temperature_is_omitted_when_not_configured():
    """gpt-5 deployments 400 on any temperature except 1.0, so the default
    request must not carry the parameter at all."""
    client = _client()
    sent = _stub_post(client, _response())

    await client.complete(_MESSAGES, max_output_tokens=100)

    assert "temperature" not in sent[0]


@pytest.mark.asyncio
async def test_configured_temperature_is_forwarded():
    client = _client(temperature=0.7)
    sent = _stub_post(client, _response())

    await client.complete(_MESSAGES, max_output_tokens=100)

    assert sent[0]["temperature"] == 0.7


@pytest.mark.asyncio
async def test_explicit_zero_temperature_is_still_sent():
    """0.0 is falsy -- it must not be dropped by an accidental truthiness check."""
    client = _client(temperature=0.0)
    sent = _stub_post(client, _response())

    await client.complete(_MESSAGES, max_output_tokens=100)

    assert sent[0]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_payload_carries_model_messages_and_token_cap():
    client = _client()
    sent = _stub_post(client, _response())

    await client.complete(_MESSAGES, max_output_tokens=1234)

    assert sent[0]["model"] == "test-model"
    assert sent[0]["messages"] == _MESSAGES
    assert sent[0]["max_tokens"] == 1234


# --- usage + response parsing ------------------------------------------------


@pytest.mark.asyncio
async def test_token_usage_is_read_from_body():
    client = _client()
    _stub_post(
        client,
        _response(
            body={
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 111, "completion_tokens": 222},
            }
        ),
    )

    result = await client.complete(_MESSAGES, max_output_tokens=100)

    assert (result.input_tokens, result.output_tokens) == (111, 222)


@pytest.mark.asyncio
async def test_unexpected_response_shape_raises_llm_output_error():
    client = _client()
    _stub_post(client, _response(body={"unexpected": True}))

    with pytest.raises(LLMOutputError):
        await client.complete(_MESSAGES, max_output_tokens=100)


@pytest.mark.asyncio
async def test_error_status_raises_without_leaking_the_key():
    client = _client(api_key="super-secret-key")
    _stub_post(client, _response(body={"error": "nope"}, status_code=400))

    with pytest.raises(LLMOutputError) as exc_info:
        await client.complete(_MESSAGES, max_output_tokens=100)

    assert "super-secret-key" not in str(exc_info.value)


# --- eager configuration validation (no paid call at construction) -----------


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": None},
        {"api_key": ""},
        {"model": None},
        {"model": ""},
        {"base_url": "not-a-url"},
    ],
)
def test_invalid_configuration_raises_before_any_request(overrides):
    with pytest.raises(ConfigurationError):
        _client(**overrides)


def test_masked_api_key_is_rejected_with_an_actionable_message():
    """A key copied from a masked UI field would otherwise raise a bare
    UnicodeEncodeError from deep inside httpx header encoding."""
    with pytest.raises(ConfigurationError) as exc_info:
        _client(api_key="sk-lAGYw" + "•" * 17)

    message = str(exc_info.value)
    assert "non-ASCII" in message
    assert "masked" in message
