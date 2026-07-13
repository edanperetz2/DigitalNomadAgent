"""Orchestrator: drives the conditional agent state machine end-to-end.

This is the only place that wires the 7 canonical modules together. It never
lets an unhandled exception escape -- every failure path is converted into an
AgentResult with status="error" while preserving whatever LLM-call trace
entries were already recorded, per the spec's error-handling requirements.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.agent.agentic_research import generate_candidates, select_tools
from app.agent.dynamic_evaluation import evaluate_candidates
from app.agent.models import CandidatePlace, PlaceRequestProfile, ValidationResult
from app.agent.recommendation_generator import generate_recommendation
from app.agent.recommendation_validator import validate_recommendations
from app.agent.request_interpreter import interpret_request
from app.agent.state import MAX_STATE_TRANSITIONS, TERMINAL_STATES, AgentState
from app.core.exceptions import PlaceMatchError
from app.evidence.memory import EvidenceMemory
from app.evidence.models import EvidenceRecord, ToolResult
from app.llm.base import BaseLLMClient
from app.llm.budget import BudgetManager
from app.tools.registry import ToolRegistry

_CRITERION_TO_TOOL: dict[str, str] = {
    "climate": "WeatherTool",
    "work_infrastructure": "AmenitiesTool",
    "cost": "BudgetFitTool",
    "timezone": "TimezoneFitTool",
    "transportation": "AmenitiesTool",
    "activities": "ActivitiesTool",
    "student_life": "AmenitiesTool",
    "education": "EducationOptionsTool",
    "accessibility": "AccessibilityTool",
}

_PRIMARY_VALUE_FIELDS = (
    "avg_high_c",
    "count",
    "match_score",
    "estimated_workday_overlap_hours",
    "lower_monthly_estimate",
)


@dataclass
class AgentResult:
    status: str
    response: str | None
    error: str | None
    steps: list[dict] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        evidence_memory: EvidenceMemory,
        llm_client: BaseLLMClient,
        budget: BudgetManager,
        *,
        max_output_tokens: int,
        max_candidates: int,
        max_final_recommendations: int,
        max_prompt_length: int,
    ):
        self._tools = tool_registry
        self._evidence = evidence_memory
        self._llm = llm_client
        self._budget = budget
        self._max_output_tokens = max_output_tokens
        self._max_candidates = max_candidates
        self._max_final_recommendations = max_final_recommendations
        self._max_prompt_length = max_prompt_length

    async def _persist_evidence(self, place: str, results: list[ToolResult]) -> None:
        for r in results:
            if r.error:
                continue
            value = None
            for field_name in _PRIMARY_VALUE_FIELDS:
                if field_name in r.normalized_data:
                    raw = r.normalized_data[field_name]
                    value = float(raw) if isinstance(raw, (int, float)) else None
                    break
            await self._evidence.store(
                EvidenceRecord(
                    place=place,
                    criterion=r.tool_name,
                    value=value,
                    raw_value=r.normalized_data,
                    source_name=r.source_name,
                    source_url=r.source_url,
                    retrieved_at=r.retrieved_at,
                    data_date=r.data_date,
                    confidence=r.confidence,
                    warnings=r.warnings,
                    stale=r.stale,
                )
            )

    @staticmethod
    def _collect_sources(evidence_by_place: dict[str, list[ToolResult]]) -> list[dict]:
        sources: dict[tuple[str, str | None], dict] = {}
        for results in evidence_by_place.values():
            for r in results:
                if r.error:
                    continue
                key = (r.source_name, r.source_url)
                if key not in sources:
                    sources[key] = {
                        "source_name": r.source_name,
                        "source_url": r.source_url,
                        "retrieved_at": r.retrieved_at.date().isoformat(),
                    }
        return list(sources.values())

    async def run(self, prompt: str) -> AgentResult:
        request_id = uuid.uuid4().hex
        execution_trace: list[dict] = []

        state = AgentState.RECEIVED
        transitions = 0
        gap_iteration_used = False

        profile: PlaceRequestProfile | None = None
        candidates: list[CandidatePlace] = []
        evidence_by_place: dict[str, list[ToolResult]] = {}
        evaluations = []
        validation: ValidationResult | None = None

        try:
            while state not in TERMINAL_STATES:
                transitions += 1
                if transitions > MAX_STATE_TRANSITIONS:
                    raise PlaceMatchError(
                        "The agent exceeded its internal iteration limit while processing this request."
                    )

                if state == AgentState.RECEIVED:
                    state = AgentState.INTERPRETING

                elif state == AgentState.INTERPRETING:
                    profile = await interpret_request(
                        prompt,
                        client=self._llm,
                        budget=self._budget,
                        request_id=request_id,
                        execution_trace=execution_trace,
                        max_output_tokens=self._max_output_tokens,
                    )
                    state = (
                        AgentState.CLARIFICATION_REQUIRED
                        if profile.clarification_required
                        else AgentState.PLANNING_RESEARCH
                    )

                elif state == AgentState.CLARIFICATION_REQUIRED:
                    response_text = profile.clarification_question or (
                        "Could you clarify your request? I need a bit more information to "
                        "make a reliable recommendation."
                    )
                    return AgentResult(status="ok", response=response_text, error=None, steps=execution_trace)

                elif state == AgentState.PLANNING_RESEARCH:
                    candidates = await generate_candidates(
                        profile,
                        client=self._llm,
                        budget=self._budget,
                        request_id=request_id,
                        execution_trace=execution_trace,
                        max_output_tokens=self._max_output_tokens,
                        max_candidates=self._max_candidates,
                    )
                    if not candidates:
                        raise PlaceMatchError("No candidate destinations could be generated for this request.")
                    state = AgentState.EXECUTING_TOOLS

                elif state == AgentState.EXECUTING_TOOLS:
                    verified, _geocoding_results = await self._tools.verify_candidates(candidates, profile)
                    if not verified:
                        raise PlaceMatchError(
                            "None of the candidate destinations could be reliably verified."
                        )
                    candidates = verified
                    tool_names = select_tools(profile)
                    grouped = await self._tools.run_tools(tool_names, candidates, profile)
                    evidence_by_place = grouped
                    for place, results in grouped.items():
                        await self._persist_evidence(place, results)
                    state = AgentState.EVALUATING

                elif state == AgentState.EVALUATING:
                    evaluations = evaluate_candidates(candidates, profile, evidence_by_place)
                    if all(e.eliminated for e in evaluations):
                        raise PlaceMatchError(
                            "All candidate destinations were eliminated by hard constraints."
                        )
                    state = AgentState.VALIDATING

                elif state == AgentState.VALIDATING:
                    validation = validate_recommendations(evaluations, profile, gap_iteration_used)
                    if validation.should_research_again and not gap_iteration_used:
                        state = AgentState.RESEARCHING_GAP
                    else:
                        state = AgentState.GENERATING_RESPONSE

                elif state == AgentState.RESEARCHING_GAP:
                    gap_iteration_used = True
                    gap_places = {m.place for m in validation.missing_research}
                    gap_tool_names = {
                        _CRITERION_TO_TOOL[m.criterion]
                        for m in validation.missing_research
                        if m.criterion in _CRITERION_TO_TOOL
                    }
                    gap_candidates = [c for c in candidates if c.place_name in gap_places]
                    if gap_tool_names and gap_candidates:
                        gap_results = await self._tools.run_tools(gap_tool_names, gap_candidates, profile)
                        for place, results in gap_results.items():
                            evidence_by_place.setdefault(place, []).extend(results)
                            await self._persist_evidence(place, results)
                    state = AgentState.EVALUATING

                elif state == AgentState.GENERATING_RESPONSE:
                    sources = self._collect_sources(evidence_by_place)
                    response_text = await generate_recommendation(
                        profile,
                        evaluations,
                        validation,
                        sources,
                        client=self._llm,
                        budget=self._budget,
                        request_id=request_id,
                        execution_trace=execution_trace,
                        max_output_tokens=self._max_output_tokens,
                        max_final_recommendations=self._max_final_recommendations,
                    )
                    state = AgentState.COMPLETED

            return AgentResult(status="ok", response=response_text, error=None, steps=execution_trace)

        except PlaceMatchError as exc:
            return AgentResult(status="error", response=None, error=str(exc), steps=execution_trace)
        except Exception as exc:  # noqa: BLE001 - last-resort safety net, contract must never leak raw tracebacks
            return AgentResult(
                status="error",
                response=None,
                error=f"An unexpected internal error occurred: {exc}",
                steps=execution_trace,
            )
