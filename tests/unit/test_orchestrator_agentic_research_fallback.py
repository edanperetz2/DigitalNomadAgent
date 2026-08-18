"""Verifies the orchestrator's actual PLANNING_RESEARCH except-branch (not
just the pure resolve_tool_selection() function) correctly falls back BOTH
the candidate list and the tool selection together when the single Agentic
Research LLM call fails -- since this branch made both come from that one
call, a failure there now degrades two things at once, not one.
"""

from datetime import UTC, datetime

import pytest

from app.agent import orchestrator as orchestrator_module
from app.agent.agentic_research import resolve_tool_selection
from app.agent.models import PlaceRequestProfile
from app.agent.orchestrator import Orchestrator
from app.core.exceptions import LLMOutputError
from app.evidence.models import ToolResult
from app.tools.registry import ToolRegistry


class _Budget:
    async def check_before_call(self, *args, **kwargs):
        return None

    async def record_call(self, *args, **kwargs):
        return None


class _RecordingMemory:
    async def store(self, record):
        return None


class _GeocodingTool:
    async def run(self, candidate, profile):
        return ToolResult(
            tool_name="GeocodingTool",
            place=candidate.place_name,
            normalized_data={"lat": 1.0, "lon": 2.0},
            source_name="test",
            retrieved_at=datetime.now(UTC),
        )


class _BudgetFitTool:
    async def run(self, candidate, profile):
        return ToolResult(
            tool_name="BudgetFitTool",
            place=candidate.place_name,
            normalized_data={"monthly_total_usd": 1000.0},
            source_name="test",
            retrieved_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_agentic_research_failure_falls_back_candidates_and_tool_selection(monkeypatch):
    async def interpreted(*args, **kwargs):
        return PlaceRequestProfile(purpose="remote_work", relevant_criteria=["safety"])

    async def failing_generate_candidates(*args, **kwargs):
        raise LLMOutputError("simulated Agentic Research failure")

    recorded_tool_calls: list[tuple[frozenset, frozenset]] = []
    real_resolve = resolve_tool_selection

    def spying_resolve_tool_selection(profile, llm_tools):
        resolved = real_resolve(profile, llm_tools)
        recorded_tool_calls.append((frozenset(llm_tools), frozenset(resolved)))
        return resolved

    service_notices_seen: list[list[str]] = []

    async def rendered(profile, evaluations, validation, sources, *, service_notices=None, **kwargs):
        service_notices_seen.append(service_notices or [])
        return "## Best matches\n\nFast City"

    monkeypatch.setattr(orchestrator_module, "interpret_request", interpreted)
    monkeypatch.setattr(orchestrator_module, "generate_candidates", failing_generate_candidates)
    monkeypatch.setattr(orchestrator_module, "resolve_tool_selection", spying_resolve_tool_selection)
    monkeypatch.setattr(orchestrator_module, "generate_recommendation", rendered)

    registry = ToolRegistry(
        {"GeocodingTool": _GeocodingTool(), "BudgetFitTool": _BudgetFitTool()},
        max_concurrent_requests=10,
    )
    orchestrator = Orchestrator(
        tool_registry=registry,
        evidence_memory=_RecordingMemory(),
        llm_client=object(),
        budget=_Budget(),
        max_output_tokens=100,
        max_bulk_candidates=30,
        max_finalists=5,
        max_final_recommendations=3,
        max_prompt_length=4000,
        execution_timeout_seconds=15.0,
    )

    result = await orchestrator.run("Find somewhere quiet and safe to work remotely.")

    assert result.status == "ok", result.error
    assert result.response == "## Best matches\n\nFast City"

    # The candidate-generation fallback disclosure fired, proving the except
    # branch ran at all.
    assert any("fixed seed set" in notice for notice in service_notices_seen[0])

    # And the SAME failure also drove tool selection: resolve_tool_selection
    # must have been called with an empty llm_tools set (proving
    # llm_selected_tools was reset to empty in the except branch), and its
    # result must equal what the deterministic rules alone would produce.
    assert len(recorded_tool_calls) == 1
    llm_tools_passed_in, resolved_tools = recorded_tool_calls[0]
    assert llm_tools_passed_in == frozenset()
    assert "SafetyTool" in resolved_tools
    assert "AmenitiesTool" in resolved_tools
