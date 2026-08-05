"""Real LLMod.ai client.

Assumes an OpenAI-compatible chat-completions request/response shape, which is
the documented default for the course's LLMod.ai integration. If the official
LLMod.ai contract differs, only this file needs to change -- the rest of the
application is provider-agnostic via BaseLLMClient.

Startup behavior: validates configuration eagerly (missing key/model, invalid
base URL) without making any network request. No paid call is ever made at
import or construction time.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.exceptions import ConfigurationError, LLMOutputError
from app.core.logging import logger, redact, register_secret
from app.llm.base import BaseLLMClient, LLMRawResponse

# LiteLLM-family proxies (which LLMod.ai presents as, via its OpenAI-compatible
# surface) report the authoritative per-request cost in this response *header*,
# not in the JSON body. Without reading it, every ledger row records $0.00 and
# the MAX_PROJECT_BUDGET_USD guard can never fire.
COST_RESPONSE_HEADER = "x-litellm-response-cost"


def _coerce_cost(value: object, *, source: str) -> float | None:
    """Parse a provider-reported cost, tolerating strings and junk.

    Returns None (meaning "unknown, fall back to a local estimate") rather than
    raising: a malformed cost field must never fail an otherwise good response.
    """
    if value is None:
        return None
    try:
        cost = float(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring unparseable provider cost from %s: %r", source, value)
        return None
    if cost < 0:
        logger.warning("Ignoring negative provider cost from %s: %r", source, value)
        return None
    return cost


# Long enough to carry a provider's own explanation ("content_filter",
# "context_length_exceeded"), short enough not to dump a page into the trace.
MAX_ERROR_DETAIL_CHARS = 300


def _error_detail(response: httpx.Response) -> str:
    """The provider's own explanation for a refusal, however it is shaped."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:MAX_ERROR_DETAIL_CHARS] or "no response body"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or error.get("type")
            if message:
                return str(message)[:MAX_ERROR_DETAIL_CHARS]
        if isinstance(error, str) and error:
            return error[:MAX_ERROR_DETAIL_CHARS]
    return str(body)[:MAX_ERROR_DETAIL_CHARS]


class LLModClient(BaseLLMClient):
    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        chat_completions_path: str,
        model: str | None,
        auth_header: str,
        auth_scheme: str,
        timeout_seconds: float,
        temperature: float | None = None,
    ):
        if not api_key:
            raise ConfigurationError(
                "LLMod.ai is not configured. Set LLMOD_API_KEY and LLMOD_MODEL, "
                "or set MOCK_LLM=true for offline development."
            )
        if not model:
            raise ConfigurationError(
                "LLMod.ai is not configured. Set LLMOD_MODEL (and LLMOD_API_KEY), "
                "or set MOCK_LLM=true for offline development."
            )
        if not api_key.isascii():
            # Caught eagerly because httpx raises a bare UnicodeEncodeError deep in
            # header encoding, which gives no hint about the cause. In practice this
            # means a *masked* value was copied out of a UI ("sk-abc<bullets>")
            # instead of the real key.
            raise ConfigurationError(
                "LLMOD_API_KEY contains non-ASCII characters and cannot be sent as an "
                "HTTP header. This usually means a masked or truncated value was copied "
                "instead of the real key -- check for bullet or ellipsis characters."
            )
        parsed = urlsplit(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ConfigurationError(f"LLMOD_BASE_URL is not a valid URL: {redact(base_url)}")

        register_secret(api_key)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._chat_completions_path = chat_completions_path
        self._model = model
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature

    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, max=4),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    )
    async def _post(self, payload: dict) -> httpx.Response:
        url = f"{self._base_url}{self._chat_completions_path}"
        headers = {
            self._auth_header: f"{self._auth_scheme} {self._api_key}".strip(),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code >= 500:
                response.raise_for_status()
            return response

    async def complete(
        self,
        messages: list[dict],
        *,
        max_output_tokens: int,
        metadata: dict | None = None,
    ) -> LLMRawResponse:
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_output_tokens,
        }
        # Omitted unless explicitly configured. gpt-5 family deployments reject
        # any value other than 1.0 outright (400 UnsupportedParamsError), so
        # sending a default here would break them.
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        try:
            response = await self._post(payload)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            logger.error("LLMod.ai request failed: %s", redact(str(exc)))
            raise LLMOutputError("LLMod.ai request failed after retries.") from exc

        if response.status_code >= 400:
            # The body was discarded, so a 400 was unattributable after the fact.
            # P10's Request Interpreter call failed this way on a real run and the
            # deterministic parser silently took over; without the provider's own
            # message there was no way to tell a content filter from a malformed
            # payload from an expired key (D32).
            raise LLMOutputError(
                f"LLMod.ai returned an error status ({response.status_code}): "
                f"{redact(_error_detail(response))}"
            )

        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMOutputError("LLMod.ai returned an unexpected response shape.") from exc

        usage = body.get("usage") or {}
        provider_cost = self._extract_provider_cost(response, body)

        return LLMRawResponse(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            provider_cost_usd=provider_cost,
            model=self._model,
        )

    @staticmethod
    def _extract_provider_cost(response: httpx.Response, body: dict) -> float | None:
        """Authoritative per-request cost, preferring the proxy's cost header.

        Falls back to body fields for providers that report cost inline. Each
        candidate is checked for presence explicitly rather than truthiness, so
        a genuine $0.00 is recorded as zero instead of being mistaken for
        "unknown" and replaced by a local estimate.
        """
        header_cost = _coerce_cost(response.headers.get(COST_RESPONSE_HEADER), source="response header")
        if header_cost is not None:
            return header_cost
        for field in ("cost_usd", "cost"):
            if field in body:
                body_cost = _coerce_cost(body[field], source=f"body field {field!r}")
                if body_cost is not None:
                    return body_cost
        return None
