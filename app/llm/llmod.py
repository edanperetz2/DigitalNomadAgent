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
        try:
            response = await self._post(payload)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            logger.error("LLMod.ai request failed: %s", redact(str(exc)))
            raise LLMOutputError("LLMod.ai request failed after retries.") from exc

        if response.status_code >= 400:
            raise LLMOutputError(
                f"LLMod.ai returned an error status ({response.status_code})."
            )

        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMOutputError("LLMod.ai returned an unexpected response shape.") from exc

        usage = body.get("usage") or {}
        provider_cost = body.get("cost_usd") or body.get("cost")

        return LLMRawResponse(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            provider_cost_usd=provider_cost,
            model=self._model,
        )
