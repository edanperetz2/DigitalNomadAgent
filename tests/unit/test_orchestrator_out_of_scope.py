"""Verifies an out-of-scope prompt (not a travel/relocation request at all) is
declined before any candidate generation or tool research runs, and costs no
extra LLM call beyond the Request Interpreter's single call. Mirrors the
monkeypatch-`interpret_request` pattern in test_execution_deadline.py.
"""

import pytest

from app.agent import orchestrator as orchestrator_module
from app.agent.models import PlaceRequestProfile
from app.agent.orchestrator import Orchestrator


def _orchestrator() -> Orchestrator:
    return Orchestrator(
        tool_registry=object(),
        evidence_memory=object(),
        llm_client=object(),
        budget=object(),
        max_output_tokens=100,
        max_bulk_candidates=30,
        max_finalists=5,
        max_final_recommendations=3,
        max_prompt_length=4000,
        execution_timeout_seconds=5.0,
    )


@pytest.mark.asyncio
async def test_out_of_scope_prompt_declines_without_generating_candidates(monkeypatch):
    async def interpreted(*args, **kwargs):
        return PlaceRequestProfile(purpose="unknown", in_scope=False)

    async def must_not_be_called(*args, **kwargs):
        raise AssertionError("Agentic Research must not run for an out-of-scope request")

    monkeypatch.setattr(orchestrator_module, "interpret_request", interpreted)
    monkeypatch.setattr(orchestrator_module, "generate_candidates", must_not_be_called)

    result = await _orchestrator().run("How do I make a sourdough starter?")

    assert result.status == "ok"
    assert result.error is None
    assert "travel" in result.response.lower() or "relocation" in result.response.lower()


@pytest.mark.asyncio
async def test_out_of_scope_check_costs_no_extra_llm_call(monkeypatch):
    """The decision reuses the Request Interpreter's existing call -- it must
    not trigger a second, dedicated LLM call."""
    calls = {"interpret": 0}

    async def interpreted(*args, execution_trace, **kwargs):
        calls["interpret"] += 1
        execution_trace.append(
            {
                "module": "Request Interpreter",
                "prompt": {"System_prompt": "test", "User_prompt": "test"},
                "response": {"in_scope": False},
            }
        )
        return PlaceRequestProfile(purpose="unknown", in_scope=False)

    monkeypatch.setattr(orchestrator_module, "interpret_request", interpreted)

    result = await _orchestrator().run("Write me a haiku about spreadsheets.")

    assert calls["interpret"] == 1
    assert len(result.steps) == 1
    assert result.steps[0]["module"] == "Request Interpreter"


@pytest.mark.asyncio
async def test_a_vague_but_genuine_travel_request_is_not_treated_as_out_of_scope(monkeypatch):
    """in_scope=False is reserved for prompts unrelated to travel/relocation
    entirely -- vagueness alone routes through clarification, not a decline."""

    async def interpreted(*args, **kwargs):
        return PlaceRequestProfile(purpose="unknown", clarification_required=True, in_scope=True)

    monkeypatch.setattr(orchestrator_module, "interpret_request", interpreted)

    result = await _orchestrator().run("Surprise me.", interactive=True)

    assert result.status == "ok"
    assert "travel, relocation" not in (result.response or "")
