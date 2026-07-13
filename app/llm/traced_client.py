"""TracedLLMClient: the ONLY path any module may use to call an LLM.

Every module that needs an LLM call must go through `traced_llm_call`. It:
  1. checks the local budget/call-count cap before calling,
  2. invokes the underlying BaseLLMClient (real or mock),
  3. parses + validates the JSON response (one repair attempt on failure),
  4. appends a sanitized {module, prompt, response} entry to `execution_trace`
     in chronological order,
  5. records the call in the SQLite usage ledger.

No module may bypass this wrapper -- tests assert this by static analysis.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.exceptions import LLMOutputError
from app.core.logging import redact
from app.llm.base import BaseLLMClient
from app.llm.budget import BudgetManager

T = TypeVar("T", bound=BaseModel)


def _estimate_tokens(messages: list[dict]) -> int:
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return max(1, total_chars // 4)


def _sanitize_prompt(messages: list[dict]) -> dict:
    """Compact, secret-free representation of what was sent to the LLM."""
    system = next((m["content"] for m in messages if m.get("role") == "system"), "")
    user = next((m["content"] for m in messages if m.get("role") == "user"), "")
    return {"system": redact(system), "user": redact(user)}


async def traced_llm_call(
    *,
    module_name: str,
    messages: list[dict],
    execution_trace: list[dict],
    client: BaseLLMClient,
    budget: BudgetManager,
    request_id: str,
    max_output_tokens: int,
    response_model: type[T] | None = None,
    max_repair_attempts: int = 1,
) -> dict[str, Any]:
    """Make one traced, budget-checked, validated LLM call.

    Returns the validated response as a plain dict. Raises LLMOutputError if
    the response cannot be parsed/validated even after the repair attempt, or
    BudgetExceededError if the call is refused before it is made.
    """
    prompt_record = _sanitize_prompt(messages)
    est_input_tokens = _estimate_tokens(messages)

    await budget.check_before_call(request_id, module_name, est_input_tokens, max_output_tokens)

    attempt_messages = list(messages)
    last_error: Exception | None = None

    for attempt in range(max_repair_attempts + 1):
        metadata = {"module": module_name, "repair": attempt > 0}
        try:
            raw = await client.complete(
                attempt_messages, max_output_tokens=max_output_tokens, metadata=metadata
            )
        except Exception as exc:  # noqa: BLE001 - provider failure is recorded then re-raised
            await budget.record_call(
                request_id, module_name, None, est_input_tokens, max_output_tokens, None, success=False
            )
            raise LLMOutputError(f"The LLM call for {module_name} failed: {exc}") from exc

        await budget.record_call(
            request_id,
            module_name,
            raw.model,
            raw.input_tokens or est_input_tokens,
            raw.output_tokens or max_output_tokens,
            raw.provider_cost_usd,
            success=True,
        )

        try:
            parsed = json.loads(raw.text)
        except json.JSONDecodeError as exc:
            last_error = exc
            execution_trace.append(
                {
                    "module": module_name,
                    "prompt": prompt_record,
                    "response": {"error": "malformed_json", "raw": redact(raw.text[:500])},
                }
            )
            attempt_messages = messages + [
                {"role": "assistant", "content": raw.text},
                {
                    "role": "user",
                    "content": "Your previous response was not valid JSON. Reply again with ONLY valid JSON.",
                },
            ]
            continue

        if response_model is not None:
            try:
                validated = response_model.model_validate(parsed)
                response_dict = validated.model_dump(mode="json")
            except ValidationError as exc:
                last_error = exc
                execution_trace.append(
                    {
                        "module": module_name,
                        "prompt": prompt_record,
                        "response": {"error": "schema_validation_failed", "raw": redact(str(parsed)[:500])},
                    }
                )
                attempt_messages = messages + [
                    {"role": "assistant", "content": raw.text},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response did not match the required schema "
                            f"({exc.error_count()} error(s)). Reply again with ONLY valid JSON "
                            "matching the requested schema."
                        ),
                    },
                ]
                continue
        else:
            response_dict = parsed

        execution_trace.append(
            {"module": module_name, "prompt": prompt_record, "response": response_dict}
        )
        return response_dict

    raise LLMOutputError(
        f"The response from {module_name} could not be parsed or validated, "
        f"even after {max_repair_attempts} repair attempt(s): {last_error}"
    )
