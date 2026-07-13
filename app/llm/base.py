"""Abstract LLM client interface shared by the real and mock providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class LLMRawResponse(BaseModel):
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_cost_usd: float | None = None
    model: str | None = None


class BaseLLMClient(ABC):
    """Common interface for any LLM backend (real or mock).

    `metadata` carries call-site context (module name, task hint, repair flag)
    that never leaves the process -- it is not sent to a real provider by
    LLModClient, but MockLLMClient uses it to select deterministic fixtures.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        *,
        max_output_tokens: int,
        metadata: dict | None = None,
    ) -> LLMRawResponse: ...
