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


# A response within this fraction of the ceiling was probably stopped by it.
_TRUNCATION_RATIO = 0.95


def _estimate_tokens(messages: list[dict]) -> int:
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return max(1, total_chars // 4)


def _looks_truncated(raw: Any, max_output_tokens: int) -> bool:
    """Whether the answer plausibly ran into the output ceiling.

    Asked rather than assumed. The repair used to tell the model its answer had
    been "cut off before it finished" and demand a shorter one on every parse
    failure -- but P13 and P14 failed on 2026-08-07 at 3,542 and 3,234 output
    tokens against a ceiling of 8,000. Length was not remotely the problem, and
    the repair spent its one attempt fixing the wrong thing.
    """
    used = getattr(raw, "output_tokens", None)
    if isinstance(used, int) and max_output_tokens > 0 and used >= max_output_tokens * _TRUNCATION_RATIO:
        return True
    # Either signal is enough. A response can stop early for reasons other than
    # the ceiling, and an object that never closes its brace is incomplete
    # whatever the token count says -- shortening helps it finish either way.
    # Text that *does* close and still will not parse is a syntax problem, and
    # asking for it shorter would waste the one repair attempt.
    text = (getattr(raw, "text", "") or "").rstrip()
    return bool(text) and not text.endswith(("}", "```"))


def _parse_response_text(text: str) -> Any:
    """Read the JSON object out of a response, tolerating what models add around it.

    Strict `json.loads` is right for the happy path and wrong as the only path:
    a complete, correct answer wrapped in a ```json fence, or with a sentence in
    front of it, was thrown away and the request fell back to the template. The
    salvage is deliberately narrow -- it finds the outermost object and parses
    *that* strictly, so genuinely malformed JSON still fails and still gets a
    repair attempt. It never guesses at content.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    stripped = text.strip()
    if stripped.startswith("```"):
        fenced = stripped.split("```")
        if len(fenced) >= 3:
            body = fenced[1]
            if body.lstrip().lower().startswith("json"):
                body = body.lstrip()[4:]
            return json.loads(body.strip())

    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        return json.loads(stripped[start : end + 1])

    raise json.JSONDecodeError("No JSON object found in the response", text, 0)


def _malformed_json_repair(raw: Any, max_output_tokens: int, error: Exception) -> str:
    """Ask for the repair the failure actually calls for."""
    if _looks_truncated(raw, max_output_tokens):
        return (
            "Your previous response was not valid JSON -- it stopped before it finished. "
            "Reply again with ONLY valid JSON, and make it substantially shorter so it "
            "completes: keep every section and every place, but write them more briefly."
        )
    return (
        f"Your previous response could not be parsed as JSON ({error}). It was not too long, "
        "so do not shorten it -- fix the syntax. Reply with ONLY a JSON object and nothing "
        "around it: no prose before or after, no markdown code fence. Every newline, quote "
        "and backslash inside a string value must be escaped (\\n, \\\", \\\\)."
    )


def _sanitize_prompt(messages: list[dict]) -> dict:
    """Compact, secret-free representation of what was sent to the LLM.

    Key names (System_prompt/User_prompt) match the course spec's required
    step object schema verbatim -- do not rename to snake_case.
    """
    system = next((m["content"] for m in messages if m.get("role") == "system"), "")
    user = next((m["content"] for m in messages if m.get("role") == "user"), "")
    return {"System_prompt": redact(system), "User_prompt": redact(user)}


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

    def record_failure(reason: str, detail: str) -> None:
        """Keep a failed module visible in `steps`.

        Parse and schema failures below already append a step, so the module
        still shows up. A refused or failed *call* previously appended nothing
        at all, so the module disappeared from the trace entirely -- observed on
        a real run where the Request Interpreter failed, the deterministic
        parser took over, and the response looked like an ordinary complete run.
        That breaks the course contract (every module must be documented in
        steps) and, worse, hides the degradation from the reader.
        """
        execution_trace.append(
            {
                "module": module_name,
                "prompt": prompt_record,
                "response": {
                    "error": reason,
                    "detail": redact(detail)[:300],
                    "note": "No model response was produced; a deterministic fallback was used.",
                },
            }
        )

    # A budget refusal deliberately leaves the trace empty: no call is made, so
    # there is no prompt/response interaction to document (see
    # test_budget_refusal_prevents_call_and_leaves_trace_empty). That differs
    # from the failure below, where the call *was* made and did produce a
    # billable, recorded attempt.
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
            record_failure("provider_call_failed", str(exc))
            raise LLMOutputError(
                f"The LLM call for {module_name} failed: {redact(str(exc))[:300]}"
            ) from exc

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
            parsed = _parse_response_text(raw.text)
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
                {"role": "user", "content": _malformed_json_repair(raw, max_output_tokens, exc)},
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
        f"even after {max_repair_attempts} repair attempt(s): {redact(str(last_error))[:300]}"
    )
