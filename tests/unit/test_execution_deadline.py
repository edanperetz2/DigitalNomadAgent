import asyncio
import time

import pytest
from pydantic import ValidationError

from app.agent import orchestrator as orchestrator_module
from app.agent.models import PlaceRequestProfile
from app.agent.orchestrator import Orchestrator
from app.core.config import (
    DEFAULT_RECOMMENDATION_RESERVE_SECONDS,
    MAX_AGENT_EXECUTION_TIMEOUT_SECONDS,
    Settings,
)


def _orchestrator(*, timeout_seconds: float) -> Orchestrator:
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
        execution_timeout_seconds=timeout_seconds,
    )


def test_the_backend_ceiling_leaves_real_margin_under_vercels_hard_kill():
    """Vercel kills at 300s with a platform error, not our degraded answer.

    The gap is not spare time -- it has to cover rendering the best-effort
    result, serializing a response carrying the full step trace, and the network
    hop. At 285 that was ~15 seconds. The slowest measured deployed run was P09
    at 247.7s (2026-08-07), so the ceiling has never actually been hit; this
    guards the case where it is.
    """
    assert 300.0 - MAX_AGENT_EXECUTION_TIMEOUT_SECONDS >= 30.0


def test_execution_deadline_default_and_maximum_are_below_300_seconds():
    settings = Settings(_env_file=None)

    assert settings.agent_execution_timeout_seconds == MAX_AGENT_EXECUTION_TIMEOUT_SECONDS
    assert MAX_AGENT_EXECUTION_TIMEOUT_SECONDS == 270.0
    assert settings.recommendation_reserve_seconds == DEFAULT_RECOMMENDATION_RESERVE_SECONDS == 60.0
    assert settings.tool_execution_timeout_seconds == 50.0
    assert settings.max_concurrent_tool_requests == 10

    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_execution_timeout_seconds=270.01)

    with pytest.raises(ValidationError, match="UPSTREAM_REQUEST_TIMEOUT_SECONDS"):
        Settings(_env_file=None, upstream_request_timeout_seconds=270.0)

    aligned = Settings(_env_file=None, upstream_request_timeout_seconds=290.0)
    assert aligned.upstream_request_timeout_seconds == 290.0


@pytest.mark.asyncio
async def test_orchestrator_cancels_active_work_and_preserves_completed_steps(monkeypatch):
    cancellation_observed = asyncio.Event()

    async def interpreted(*args, execution_trace, **kwargs):
        execution_trace.append(
            {
                "module": "Request Interpreter",
                "prompt": {"System_prompt": "test", "User_prompt": "test"},
                "response": {"purpose": "vacation"},
            }
        )
        return PlaceRequestProfile(purpose="vacation")

    async def slow_candidate_generation(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancellation_observed.set()

    monkeypatch.setattr(orchestrator_module, "interpret_request", interpreted)
    monkeypatch.setattr(orchestrator_module, "generate_candidates", slow_candidate_generation)

    started = time.monotonic()
    result = await _orchestrator(timeout_seconds=0.02).run("Find a beach destination.")
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert cancellation_observed.is_set()
    assert result.status == "ok"
    assert result.error is None
    assert "## Best matches" in result.response
    assert "incomplete research was cancelled" in result.response
    assert [step["module"] for step in result.steps] == ["Request Interpreter"]


@pytest.mark.asyncio
async def test_research_cutoff_keeps_fast_evidence_and_returns_recommendations(monkeypatch):
    from datetime import UTC, datetime

    from app.agent.models import CandidatePlace
    from app.evidence.models import ToolResult
    from app.tools.registry import ToolRegistry

    slow_call_cancelled = asyncio.Event()

    class GeocodingTool:
        async def run(self, candidate, profile):
            return ToolResult(
                tool_name="GeocodingTool",
                place=candidate.place_name,
                normalized_data={"lat": 1.0, "lon": 2.0},
                source_name="test",
                retrieved_at=datetime.now(UTC),
            )

    class FastActivitiesTool:
        async def run(self, candidate, profile):
            await asyncio.sleep(0.001)
            return ToolResult(
                tool_name="ActivitiesTool",
                place=candidate.place_name,
                normalized_data={"count": 8},
                source_name="test",
                retrieved_at=datetime.now(UTC),
            )

    class SlowWeatherTool:
        async def run(self, candidate, profile):
            try:
                await asyncio.sleep(10)
            finally:
                slow_call_cancelled.set()

    class RecordingMemory:
        async def store(self, record):
            return None

    async def interpreted(*args, **kwargs):
        return PlaceRequestProfile(purpose="vacation", relevant_criteria=["activities", "climate"])

    async def generated(*args, **kwargs):
        return [CandidatePlace(place_name="Fast City", country="X", reason_for_inclusion="test")], set()

    async def rendered(profile, evaluations, validation, sources, **kwargs):
        assert any("partial evidence" in issue for issue in validation.issues)
        return "## Best matches\n\nFast City"

    monkeypatch.setattr(orchestrator_module, "interpret_request", interpreted)
    monkeypatch.setattr(orchestrator_module, "generate_candidates", generated)
    monkeypatch.setattr(
        orchestrator_module,
        "resolve_tool_selection",
        lambda profile, llm_tools: {"GeocodingTool", "ActivitiesTool", "WeatherTool"},
    )
    monkeypatch.setattr(orchestrator_module, "generate_recommendation", rendered)

    registry = ToolRegistry(
        {
            "GeocodingTool": GeocodingTool(),
            "ActivitiesTool": FastActivitiesTool(),
            "WeatherTool": SlowWeatherTool(),
        },
        max_concurrent_requests=10,
    )
    orchestrator = Orchestrator(
        tool_registry=registry,
        evidence_memory=RecordingMemory(),
        llm_client=object(),
        budget=object(),
        max_output_tokens=100,
        max_bulk_candidates=30,
        max_finalists=5,
        max_final_recommendations=3,
        max_prompt_length=4000,
        execution_timeout_seconds=0.2,
        recommendation_reserve_seconds=0.12,
    )

    result = await orchestrator.run("Find activities and good weather.")

    assert slow_call_cancelled.is_set()
    assert result.status == "ok"
    assert result.error is None
    assert "Fast City" in result.response


@pytest.mark.asyncio
async def test_slow_recommendation_llm_uses_deterministic_renderer(monkeypatch):
    from app.agent import recommendation_generator as generator_module
    from app.agent.models import CandidateEvaluation, ValidationResult

    async def slow_llm_call(**kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(generator_module, "traced_llm_call", slow_llm_call)

    response = await generator_module.generate_recommendation(
        PlaceRequestProfile(purpose="vacation"),
        [
            CandidateEvaluation(
                place="Fast City",
                country="X",
                advantages=["Completed evidence supports this option."],
                drawbacks=["Some evidence is missing."],
                confidence_score=0.5,
            )
        ],
        ValidationResult(approved=True),
        [],
        client=object(),
        budget=object(),
        request_id="test",
        execution_trace=[],
        max_output_tokens=100,
        llm_timeout_seconds=0.01,
    )

    assert "## Best matches" in response
    assert "Fast City" in response
    assert "did not finish in time" in response
    # The disclosure must name the actual cause (timeout), never the budget --
    # a reader has no way to tell them apart from a generic message, and this
    # run never touched the budget cap.
    assert "budget" not in response.lower()


@pytest.mark.asyncio
async def test_budget_exceeded_recommendation_fallback_names_the_budget(monkeypatch):
    from app.agent import recommendation_generator as generator_module
    from app.agent.models import CandidateEvaluation, ValidationResult
    from app.core.exceptions import BudgetExceededError

    async def refused_llm_call(**kwargs):
        raise BudgetExceededError("project budget exhausted")

    monkeypatch.setattr(generator_module, "traced_llm_call", refused_llm_call)

    response = await generator_module.generate_recommendation(
        PlaceRequestProfile(purpose="vacation"),
        [
            CandidateEvaluation(
                place="Fast City",
                country="X",
                advantages=["Completed evidence supports this option."],
                drawbacks=["Some evidence is missing."],
                confidence_score=0.5,
            )
        ],
        ValidationResult(approved=True),
        [],
        client=object(),
        budget=object(),
        request_id="test",
        execution_trace=[],
        max_output_tokens=100,
    )

    assert "## Best matches" in response
    assert "budget limit was reached" in response
    # This is the one cause where the word *should* appear -- unlike the
    # timeout and malformed-output cases, this genuinely is a budget problem.
    assert "did not finish in time" not in response


@pytest.mark.asyncio
async def test_malformed_output_recommendation_fallback_never_blames_the_budget(monkeypatch):
    from app.agent import recommendation_generator as generator_module
    from app.agent.models import CandidateEvaluation, ValidationResult
    from app.core.exceptions import LLMOutputError

    async def unparseable_llm_call(**kwargs):
        raise LLMOutputError("could not be parsed even after 2 repair attempt(s)")

    monkeypatch.setattr(generator_module, "traced_llm_call", unparseable_llm_call)

    response = await generator_module.generate_recommendation(
        PlaceRequestProfile(purpose="vacation"),
        [
            CandidateEvaluation(
                place="Fast City",
                country="X",
                advantages=["Completed evidence supports this option."],
                drawbacks=["Some evidence is missing."],
                confidence_score=0.5,
            )
        ],
        ValidationResult(approved=True),
        [],
        client=object(),
        budget=object(),
        request_id="test",
        execution_trace=[],
        max_output_tokens=100,
    )

    assert "## Best matches" in response
    assert "could not be used, even after a repair attempt" in response
    # A malformed/truncated model response is not a budget problem -- the
    # disclosure must not let a reader conflate the two.
    assert "budget" not in response.lower()
    assert "did not finish in time" not in response


@pytest.mark.asyncio
async def test_unreachable_provider_fallback_does_not_claim_a_repair_was_attempted(monkeypatch):
    from app.agent import recommendation_generator as generator_module
    from app.agent.models import CandidateEvaluation, ValidationResult
    from app.core.exceptions import LLMOutputError

    async def unreachable_llm_call(**kwargs):
        # Matches traced_client.py's wording for a raw provider/connection
        # failure (app/llm/traced_client.py:182) -- distinct from the
        # repair-exhausted message above, since no response ever existed to
        # repair in the first place.
        raise LLMOutputError("The LLM call for Recommendation Generator failed: Connection refused")

    monkeypatch.setattr(generator_module, "traced_llm_call", unreachable_llm_call)

    response = await generator_module.generate_recommendation(
        PlaceRequestProfile(purpose="vacation"),
        [
            CandidateEvaluation(
                place="Fast City",
                country="X",
                advantages=["Completed evidence supports this option."],
                drawbacks=["Some evidence is missing."],
                confidence_score=0.5,
            )
        ],
        ValidationResult(approved=True),
        [],
        client=object(),
        budget=object(),
        request_id="test",
        execution_trace=[],
        max_output_tokens=100,
    )

    assert "## Best matches" in response
    assert "could not be reached" in response
    # No repair loop runs for a connection failure -- the wording must not
    # claim one was attempted, and this is still not a budget problem.
    assert "repair attempt" not in response
    assert "budget" not in response.lower()
    assert "did not finish in time" not in response
