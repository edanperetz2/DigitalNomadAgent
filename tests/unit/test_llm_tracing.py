import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.core.exceptions import BudgetExceededError, LLMOutputError
from app.core.module_names import ALL_MODULES
from app.llm.base import BaseLLMClient, LLMRawResponse
from app.llm.traced_client import traced_llm_call

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeBudget:
    def __init__(self, refuse: bool = False):
        self.refuse = refuse
        self.calls_recorded = []

    async def check_before_call(self, request_id, module, est_input, est_output):
        if self.refuse:
            raise BudgetExceededError("refused for test")

    async def record_call(self, request_id, module, model, input_tokens, output_tokens, cost, success):
        self.calls_recorded.append((module, success))


class _EchoClient(BaseLLMClient):
    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0

    async def complete(self, messages, *, max_output_tokens, metadata=None):
        text = self._responses[self.call_count]
        self.call_count += 1
        return LLMRawResponse(text=text, input_tokens=10, output_tokens=10, provider_cost_usd=0.0)


class _Schema(BaseModel):
    value: int


class _FailingClient(BaseLLMClient):
    """A provider that refuses or errors before producing any text."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def complete(self, messages, *, max_output_tokens, metadata=None):
        raise self._exc


@pytest.mark.asyncio
async def test_a_failed_provider_call_still_appears_in_the_trace():
    """Observed on a real run: the Request Interpreter call failed, the
    deterministic parser took over, and the module vanished from `steps`
    entirely -- the response looked like an ordinary complete run."""
    trace: list[dict] = []
    with pytest.raises(LLMOutputError):
        await traced_llm_call(
            module_name="Request Interpreter",
            messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            execution_trace=trace,
            client=_FailingClient(RuntimeError("provider exploded")),
            budget=_FakeBudget(),
            request_id="r1",
            max_output_tokens=100,
        )

    assert [s["module"] for s in trace] == ["Request Interpreter"]
    assert trace[0]["response"]["error"] == "provider_call_failed"
    # Course spec: every step carries exactly these prompt keys.
    assert set(trace[0]["prompt"]) == {"System_prompt", "User_prompt"}


@pytest.mark.asyncio
async def test_a_failed_call_does_not_leak_the_api_key_into_the_trace():
    from app.core.logging import register_secret

    register_secret("sk-trace-secret-value")
    trace: list[dict] = []
    with pytest.raises(LLMOutputError):
        await traced_llm_call(
            module_name="Agentic Research",
            messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            execution_trace=trace,
            client=_FailingClient(RuntimeError("401 for key sk-trace-secret-value")),
            budget=_FakeBudget(),
            request_id="r1",
            max_output_tokens=100,
        )

    assert "sk-trace-secret-value" not in json.dumps(trace)


@pytest.mark.asyncio
async def test_traced_call_appends_one_step_on_success():
    trace: list[dict] = []
    client = _EchoClient([json.dumps({"value": 1})])
    budget = _FakeBudget()
    result = await traced_llm_call(
        module_name="Request Interpreter",
        messages=[{"role": "user", "content": "hi"}],
        execution_trace=trace,
        client=client,
        budget=budget,
        request_id="r1",
        max_output_tokens=50,
        response_model=_Schema,
    )
    assert result == {"value": 1}
    assert len(trace) == 1
    assert set(trace[0].keys()) == {"module", "prompt", "response"}
    assert trace[0]["module"] == "Request Interpreter"
    # Course spec's required step schema uses these exact key names -- do not
    # rename to snake_case, even though the rest of the codebase uses it.
    assert set(trace[0]["prompt"].keys()) == {"System_prompt", "User_prompt"}


@pytest.mark.asyncio
async def test_traced_call_repairs_malformed_json_once():
    trace: list[dict] = []
    client = _EchoClient(["not json", json.dumps({"value": 2})])
    budget = _FakeBudget()
    result = await traced_llm_call(
        module_name="Agentic Research",
        messages=[{"role": "user", "content": "hi"}],
        execution_trace=trace,
        client=client,
        budget=budget,
        request_id="r1",
        max_output_tokens=50,
        response_model=_Schema,
        max_repair_attempts=1,
    )
    assert result == {"value": 2}
    assert len(trace) == 2
    assert trace[0]["response"]["error"] == "malformed_json"
    assert trace[1]["response"] == {"value": 2}


@pytest.mark.asyncio
async def test_traced_call_raises_after_exhausting_repair_attempts():
    from app.core.exceptions import LLMOutputError

    trace: list[dict] = []
    client = _EchoClient(["not json", "still not json"])
    budget = _FakeBudget()
    with pytest.raises(LLMOutputError):
        await traced_llm_call(
            module_name="Agentic Research",
            messages=[{"role": "user", "content": "hi"}],
            execution_trace=trace,
            client=client,
            budget=budget,
            request_id="r1",
            max_output_tokens=50,
            response_model=_Schema,
            max_repair_attempts=1,
        )


@pytest.mark.asyncio
async def test_budget_refusal_prevents_call_and_leaves_trace_empty():
    trace: list[dict] = []
    client = _EchoClient([json.dumps({"value": 1})])
    budget = _FakeBudget(refuse=True)
    with pytest.raises(BudgetExceededError):
        await traced_llm_call(
            module_name="Request Interpreter",
            messages=[{"role": "user", "content": "hi"}],
            execution_trace=trace,
            client=client,
            budget=budget,
            request_id="r1",
            max_output_tokens=50,
            response_model=_Schema,
        )
    assert trace == []
    assert client.call_count == 0


def test_no_module_bypasses_traced_client():
    """Static check: only traced_client.py / mock.py / llmod.py / main.py / the
    connection-check script may reference the LLM client classes directly."""
    allowed_files = {
        "app/llm/traced_client.py",
        "app/llm/base.py",
        "app/llm/mock.py",
        "app/llm/llmod.py",
        "app/main.py",
        "scripts/check_llmod_connection.py",
    }
    offenders = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in allowed_files:
            continue
        text = path.read_text(encoding="utf-8")
        if "MockLLMClient(" in text or "LLModClient(" in text:
            offenders.append(rel)
    assert offenders == []


def test_canonical_module_names_are_unique_and_nonempty():
    assert len(ALL_MODULES) == len(set(ALL_MODULES)) == 7
    assert all(name.strip() for name in ALL_MODULES)
