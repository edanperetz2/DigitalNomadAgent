"""Orchestrator: drives the conditional agent state machine end-to-end.

This is the only place that wires the 7 canonical modules together. It never
lets an unhandled exception escape -- every failure path is converted into an
AgentResult with status="error" while preserving whatever LLM-call trace
entries were already recorded, per the spec's error-handling requirements.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from app.agent.agentic_research import generate_candidates, select_tools
from app.agent.candidate_funnel import select_finalists
from app.agent.dynamic_evaluation import apply_llm_scores, evaluate_candidates, score_unresolved_criteria
from app.agent.models import CandidateEvaluation, CandidatePlace, PlaceRequestProfile, ValidationResult
from app.agent.recommendation_generator import generate_recommendation, render_recommendation_fallback
from app.agent.recommendation_validator import validate_recommendations
from app.agent.request_interpreter import interpret_request
from app.agent.state import MAX_STATE_TRANSITIONS, TERMINAL_STATES, AgentState
from app.core.exceptions import BudgetExceededError, LLMOutputError, PlaceMatchError
from app.core.logging import logger
from app.evidence.memory import EvidenceMemory
from app.evidence.models import EvidenceRecord, ToolResult
from app.llm.base import BaseLLMClient
from app.llm.budget import BudgetManager
from app.llm.mock import generate_candidates as generate_fallback_candidates
from app.llm.mock import interpret_prompt as interpret_prompt_fallback
from app.tools.registry import ToolRegistry

_CRITERION_TO_TOOLS: dict[str, set[str]] = {
    "climate": {"WeatherTool", "WikivoyageClimateTool"},
    "work_infrastructure": {"AmenitiesTool"},
    "cost": {"BudgetFitTool"},
    "timezone": {"TimezoneFitTool"},
    "transportation": {"LocalMobilityTool"},
    "activities": {"ActivitiesTool"},
    "student_life": {"AmenitiesTool"},
    "education": {"AmenitiesTool"},
    "accessibility": {"TransportAccessTool"},
    "safety": {"SafetyTool"},
}

_PRIMARY_VALUE_FIELDS = (
    "avg_high_c",
    "count",
    "estimated_workday_overlap_hours",
)


@dataclass
class AgentResult:
    status: str
    response: str | None
    error: str | None
    steps: list[dict] = field(default_factory=list)


@dataclass
class _RunCheckpoint:
    profile: PlaceRequestProfile | None = None
    candidates: list[CandidatePlace] = field(default_factory=list)
    evidence_by_place: dict[str, list[ToolResult]] = field(default_factory=dict)
    evaluations: list = field(default_factory=list)
    validation: ValidationResult | None = None


def _resolve_ambiguous_profile(profile: PlaceRequestProfile) -> PlaceRequestProfile:
    """Turn a clarification-worthy profile into one the pipeline can still run with.

    Used whenever a request must resolve to a final answer in one call (the
    default, automated-safe mode) instead of stopping to ask a question. Never
    mutates in place -- callers hold the original checkpoint profile too.
    """
    disclosure = (
        "This request was ambiguous enough that clarification would normally be requested "
        f"({profile.clarification_question or 'the purpose was unclear'}); proceeding with a "
        "broad default so a complete recommendation could still be returned in one call."
    )
    updated = profile.model_copy(deep=True)
    updated.assumptions.append(disclosure)
    if updated.purpose == "unknown":
        updated.purpose = "mixed"
    return updated


class Orchestrator:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        evidence_memory: EvidenceMemory,
        llm_client: BaseLLMClient,
        budget: BudgetManager,
        *,
        max_output_tokens: int,
        max_bulk_candidates: int,
        max_finalists: int,
        max_final_recommendations: int,
        max_prompt_length: int,
        execution_timeout_seconds: float,
        recommendation_reserve_seconds: float = 60.0,
    ):
        self._tools = tool_registry
        self._evidence = evidence_memory
        self._llm = llm_client
        self._budget = budget
        self._max_output_tokens = max_output_tokens
        self._max_bulk_candidates = max_bulk_candidates
        self._max_finalists = max_finalists
        self._max_final_recommendations = max_final_recommendations
        self._max_prompt_length = max_prompt_length
        self._execution_timeout_seconds = execution_timeout_seconds
        self._recommendation_reserve_seconds = recommendation_reserve_seconds

    async def _persist_evidence(self, place: str, results: list[ToolResult]) -> None:
        for r in results:
            if r.error:
                continue
            for item in r.resolved_evidence_items():
                value = item.value
                if value is None:
                    for field_name in _PRIMARY_VALUE_FIELDS:
                        if field_name in item.normalized_data:
                            raw = item.normalized_data[field_name]
                            value = float(raw) if isinstance(raw, (int, float)) else None
                            break
                await self._evidence.store(
                    EvidenceRecord(
                        place=place,
                        criterion=item.criterion,
                        value=value,
                        raw_value={**item.normalized_data, "component": item.component},
                        source_name=item.source.source_name,
                        source_url=item.source.source_url,
                        retrieved_at=item.source.retrieved_at,
                        data_date=item.source.data_date,
                        confidence=item.source.confidence,
                        warnings=item.warnings,
                        stale=item.source.stale,
                    )
                )

    @staticmethod
    def _collect_sources(evidence_by_place: dict[str, list[ToolResult]]) -> list[dict]:
        sources: dict[tuple[str, str | None], dict] = {}
        for results in evidence_by_place.values():
            for r in results:
                if r.error:
                    continue
                for item in r.resolved_evidence_items():
                    key = (item.source.source_name, item.source.source_url)
                    if key not in sources:
                        sources[key] = {
                            "source_name": item.source.source_name,
                            "source_url": item.source.source_url,
                            "retrieved_at": item.source.retrieved_at.date().isoformat(),
                            "data_date": item.source.data_date,
                            "confidence": item.source.confidence,
                            "stale": item.source.stale,
                        }
        return list(sources.values())

    @staticmethod
    def _tool_priorities(profile: PlaceRequestProfile, tool_names: set[str]) -> dict[str, float]:
        """Prioritize user-weighted evidence; ties remain deterministic by tool name."""
        priorities = {tool_name: 0.1 for tool_name in tool_names}
        for criterion, criterion_tools in _CRITERION_TO_TOOLS.items():
            weight = profile.inferred_weights.get(
                criterion,
                0.5 if criterion in profile.relevant_criteria else 0.0,
            )
            for tool_name in criterion_tools & tool_names:
                priorities[tool_name] = max(priorities[tool_name], weight)

        hard_text = " ".join(profile.hard_constraints + profile.deal_breakers).lower()
        hard_tool_keywords = {
            "BudgetFitTool": ("budget", "cost", "afford"),
            "TimezoneFitTool": ("timezone", "time zone", "working hours", "overlap"),
            "AmenitiesTool": (
                "student life",
                "cowork",
                "cafe",
                "fitness",
                "gym",
                "library",
                "park",
                "pharmacy",
                "supermarket",
                "university",
                "study",
                "education",
            ),
            "TransportAccessTool": ("airport", "distance", "remote", "arrival", "get there"),
            "WeatherTool": ("weather", "climate", "temperature", "rain", "snow"),
            "ActivitiesTool": ("activit", "hiking", "beach", "culture", "nightlife"),
            "SafetyTool": ("safety", "safe", "crime", "danger", "security"),
        }
        for tool_name, keywords in hard_tool_keywords.items():
            if tool_name in priorities and any(keyword in hard_text for keyword in keywords):
                priorities[tool_name] = max(priorities[tool_name], 2.0)
        return priorities

    async def _score_unresolved_criteria(
        self,
        evaluations: list[CandidateEvaluation],
        candidates: list[CandidatePlace],
        profile: PlaceRequestProfile,
        evidence_by_place: dict[str, list[ToolResult]],
        request_id: str,
        execution_trace: list[dict],
    ) -> list[CandidateEvaluation]:
        """The single Dynamic Evaluation LLM call, fired exactly once per request
        from VALIDATING's approved branch -- after any gap-research round, so it
        never runs twice even if RESEARCHING_GAP looped. On failure, evaluations
        are returned unchanged (cost/transportation/accessibility/activities stay
        in unscored_evidence) rather than aborting the whole request.
        """
        try:
            llm_scores = await score_unresolved_criteria(
                evaluations,
                profile,
                evidence_by_place,
                client=self._llm,
                budget=self._budget,
                request_id=request_id,
                execution_trace=execution_trace,
                max_output_tokens=self._max_output_tokens,
            )
        except (BudgetExceededError, LLMOutputError):
            return evaluations
        if not llm_scores:
            return evaluations
        return apply_llm_scores(evaluations, candidates, profile, evidence_by_place, llm_scores)

    async def run(self, prompt: str, *, interactive: bool = False) -> AgentResult:
        request_id = uuid.uuid4().hex
        execution_trace: list[dict] = []
        checkpoint = _RunCheckpoint()
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        hard_deadline = started_at + self._execution_timeout_seconds
        effective_reserve = min(
            self._recommendation_reserve_seconds,
            self._execution_timeout_seconds / 2,
        )
        research_deadline = hard_deadline - effective_reserve

        hard_fallback_used = False
        try:
            result = await asyncio.wait_for(
                self._run_state_machine(
                    prompt,
                    request_id,
                    execution_trace,
                    checkpoint,
                    research_deadline,
                    hard_deadline,
                    interactive,
                ),
                timeout=self._execution_timeout_seconds,
            )
        except TimeoutError:
            hard_fallback_used = True
            result = self._best_effort_result(
                prompt,
                checkpoint,
                execution_trace,
                "The hard execution deadline was reached; unfinished work was stopped and the "
                "ranking uses only evidence available at that point.",
                interactive,
            )
        elapsed = loop.time() - started_at
        timed_out_tools = sum(
            1
            for results in checkpoint.evidence_by_place.values()
            for tool_result in results
            if tool_result.error and "budget" in tool_result.error
        )
        logger.info(
            "agent_timing request_id=%s total_seconds=%.3f status=%s hard_fallback=%s "
            "timed_out_tools=%d",
            request_id,
            elapsed,
            result.status,
            hard_fallback_used,
            timed_out_tools,
        )
        return result

    def _best_effort_result(
        self,
        prompt: str,
        checkpoint: _RunCheckpoint,
        execution_trace: list[dict],
        limitation: str,
        interactive: bool,
    ) -> AgentResult:
        """Build a usable response synchronously after cancellation; never start more I/O."""
        try:
            profile = checkpoint.profile or PlaceRequestProfile.model_validate(
                interpret_prompt_fallback(prompt)
            )
            if profile.clarification_required:
                if interactive:
                    response = profile.clarification_question or "Could you clarify your request?"
                    return AgentResult(status="ok", response=response, error=None, steps=execution_trace)
                profile = _resolve_ambiguous_profile(profile)

            candidates = checkpoint.candidates
            if not candidates:
                candidates = [
                    CandidatePlace.model_validate(candidate)
                    for candidate in generate_fallback_candidates(profile.model_dump(mode="json"))[
                        : self._max_finalists
                    ]
                ]

            evaluations = checkpoint.evaluations or evaluate_candidates(
                candidates,
                profile,
                checkpoint.evidence_by_place,
            )
            validation = checkpoint.validation or validate_recommendations(
                evaluations,
                profile,
                gap_iteration_used=True,
                max_final_recommendations=self._max_final_recommendations,
            )
            validation = validation.model_copy(deep=True)
            validation.approved = True
            validation.should_research_again = False
            if limitation not in validation.issues:
                validation.issues.append(limitation)

            response = render_recommendation_fallback(
                profile,
                evaluations,
                validation,
                self._collect_sources(checkpoint.evidence_by_place),
                max_final_recommendations=self._max_final_recommendations,
            )
            response += "\n\n**Timing note:** incomplete research was cancelled so this response could arrive on time."
            return AgentResult(status="ok", response=response, error=None, steps=execution_trace)
        except Exception as exc:  # noqa: BLE001 - final no-I/O fallback must preserve the API contract
            return AgentResult(
                status="error",
                response=None,
                error=f"The agent could not build a safe partial recommendation: {exc}",
                steps=execution_trace,
            )

    async def _run_state_machine(
        self,
        prompt: str,
        request_id: str,
        execution_trace: list[dict],
        checkpoint: _RunCheckpoint,
        research_deadline: float,
        hard_deadline: float,
        interactive: bool,
    ) -> AgentResult:

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
                current_state = state
                phase_started_at = asyncio.get_running_loop().time()
                transitions += 1
                if transitions > MAX_STATE_TRANSITIONS:
                    raise PlaceMatchError(
                        "The agent exceeded its internal iteration limit while processing this request."
                    )

                if state == AgentState.RECEIVED:
                    state = AgentState.INTERPRETING

                elif state == AgentState.INTERPRETING:
                    try:
                        profile = await interpret_request(
                            prompt,
                            client=self._llm,
                            budget=self._budget,
                            request_id=request_id,
                            execution_trace=execution_trace,
                            max_output_tokens=self._max_output_tokens,
                        )
                    except (BudgetExceededError, LLMOutputError):
                        profile = PlaceRequestProfile.model_validate(interpret_prompt_fallback(prompt))
                        profile.assumptions.append(
                            "The request-interpreter model was unavailable; a deterministic parser was used."
                        )
                    checkpoint.profile = profile
                    state = (
                        AgentState.CLARIFICATION_REQUIRED
                        if profile.clarification_required
                        else AgentState.PLANNING_RESEARCH
                    )

                elif state == AgentState.CLARIFICATION_REQUIRED:
                    if interactive:
                        response_text = profile.clarification_question or (
                            "Could you clarify your request? I need a bit more information to "
                            "make a reliable recommendation."
                        )
                        logger.info(
                            "agent_phase request_id=%s phase=%s duration_seconds=%.3f next_state=returned",
                            request_id,
                            current_state,
                            asyncio.get_running_loop().time() - phase_started_at,
                        )
                        return AgentResult(status="ok", response=response_text, error=None, steps=execution_trace)
                    profile = _resolve_ambiguous_profile(profile)
                    checkpoint.profile = profile
                    state = AgentState.PLANNING_RESEARCH

                elif state == AgentState.PLANNING_RESEARCH:
                    try:
                        candidates = await generate_candidates(
                            profile,
                            client=self._llm,
                            budget=self._budget,
                            request_id=request_id,
                            execution_trace=execution_trace,
                            max_output_tokens=self._max_output_tokens,
                            max_bulk_candidates=self._max_bulk_candidates,
                        )
                    except (BudgetExceededError, LLMOutputError):
                        candidates = [
                            CandidatePlace.model_validate(candidate)
                            for candidate in generate_fallback_candidates(profile.model_dump(mode="json"))[
                                : self._max_bulk_candidates
                            ]
                        ]
                        profile.assumptions.append(
                            "The candidate-generation model was unavailable; a curated seed set was used."
                        )
                    if not candidates:
                        raise PlaceMatchError("No candidate destinations could be generated for this request.")
                    checkpoint.candidates = list(candidates)
                    state = AgentState.EXECUTING_TOOLS

                elif state == AgentState.EXECUTING_TOOLS:
                    remaining_research = max(0.0, research_deadline - asyncio.get_running_loop().time())
                    verified, geocoding_results = await self._tools.verify_candidates(
                        candidates,
                        profile,
                        timeout_seconds=remaining_research,
                        request_id=request_id,
                    )
                    research_timed_out = any(
                        result.error and "research time budget expired" in result.error
                        for result in geocoding_results
                    )
                    if not verified:
                        if not research_timed_out:
                            raise PlaceMatchError(
                                "None of the candidate destinations could be reliably verified."
                            )
                    else:
                        candidates = verified
                        checkpoint.candidates = list(candidates)

                    # Stage 2 of the candidate-discovery funnel: a cheap, zero-LLM filter/rank
                    # over all geocoded survivors (up to max_bulk_candidates), narrowing down to
                    # max_finalists before the expensive full tool suite (Stage 3) ever runs.
                    remaining_research = max(0.0, research_deadline - asyncio.get_running_loop().time())
                    budget_grouped = await self._tools.run_tools(
                        {"BudgetFitTool"},
                        candidates,
                        profile,
                        timeout_seconds=remaining_research,
                        request_id=request_id,
                    )
                    finalists = select_finalists(
                        candidates, profile, budget_grouped, max_finalists=self._max_finalists
                    )
                    if not finalists:
                        raise PlaceMatchError(
                            "All candidate destinations were eliminated by region constraints "
                            "before research began."
                        )
                    candidates = finalists
                    checkpoint.candidates = list(candidates)
                    finalist_names = {c.place_name for c in candidates}

                    tool_names = select_tools(profile) - {"BudgetFitTool"}
                    tool_priorities = self._tool_priorities(profile, tool_names)
                    evidence_by_place = {}
                    for result in geocoding_results:
                        if result.place in finalist_names:
                            evidence_by_place.setdefault(result.place, []).append(result)
                    for place in finalist_names:
                        evidence_by_place.setdefault(place, []).extend(budget_grouped.get(place, []))
                    checkpoint.evidence_by_place = evidence_by_place

                    remaining_research = max(0.0, research_deadline - asyncio.get_running_loop().time())
                    grouped = await self._tools.run_tools(
                        tool_names,
                        candidates,
                        profile,
                        timeout_seconds=remaining_research,
                        tool_priorities=tool_priorities,
                        request_id=request_id,
                    )
                    for place, results in grouped.items():
                        evidence_by_place.setdefault(place, []).extend(results)
                    checkpoint.evidence_by_place = evidence_by_place
                    for place, results in evidence_by_place.items():
                        await self._persist_evidence(place, results)
                    state = AgentState.EVALUATING

                elif state == AgentState.EVALUATING:
                    evaluations = evaluate_candidates(candidates, profile, evidence_by_place)
                    checkpoint.evaluations = list(evaluations)
                    if all(e.eliminated for e in evaluations):
                        raise PlaceMatchError(
                            "All candidate destinations were eliminated by hard constraints."
                        )
                    state = AgentState.VALIDATING

                elif state == AgentState.VALIDATING:
                    validation = validate_recommendations(
                        evaluations, profile, gap_iteration_used, self._max_final_recommendations
                    )
                    research_time_exhausted = asyncio.get_running_loop().time() >= research_deadline
                    if validation.should_research_again and not gap_iteration_used and not research_time_exhausted:
                        state = AgentState.RESEARCHING_GAP
                    else:
                        if validation.should_research_again:
                            gap_iteration_used = True
                            validation = validate_recommendations(
                                evaluations, profile, gap_iteration_used, self._max_final_recommendations
                            )
                            validation.issues.append(
                                "The optional gap-research round was skipped because the research time budget expired."
                            )
                        evaluations = await self._score_unresolved_criteria(
                            evaluations, candidates, profile, evidence_by_place, request_id, execution_trace
                        )
                        checkpoint.evaluations = list(evaluations)
                        if all(e.eliminated for e in evaluations):
                            raise PlaceMatchError(
                                "All candidate destinations were eliminated by hard constraints."
                            )
                        checkpoint.validation = validation
                        state = AgentState.GENERATING_RESPONSE

                elif state == AgentState.RESEARCHING_GAP:
                    gap_iteration_used = True
                    gap_places = {m.place for m in validation.missing_research}
                    gap_tool_names = set().union(
                        *(
                            _CRITERION_TO_TOOLS[item.criterion]
                            for item in validation.missing_research
                            if item.criterion in _CRITERION_TO_TOOLS
                        )
                    )
                    gap_candidates = [c for c in candidates if c.place_name in gap_places]
                    if gap_tool_names and gap_candidates:
                        remaining_research = max(0.0, research_deadline - asyncio.get_running_loop().time())
                        gap_results = await self._tools.run_tools(
                            gap_tool_names,
                            gap_candidates,
                            profile,
                            timeout_seconds=remaining_research,
                            tool_priorities=self._tool_priorities(profile, gap_tool_names),
                            request_id=request_id,
                        )
                        for place, results in gap_results.items():
                            evidence_by_place.setdefault(place, []).extend(results)
                            await self._persist_evidence(place, results)
                        checkpoint.evidence_by_place = evidence_by_place
                    state = AgentState.EVALUATING

                elif state == AgentState.GENERATING_RESPONSE:
                    timed_out_evidence = any(
                        result.error
                        and (
                            "research time budget expired" in result.error
                            or "per-invocation execution budget" in result.error
                        )
                        for results in evidence_by_place.values()
                        for result in results
                    )
                    if timed_out_evidence:
                        validation = validation.model_copy(deep=True)
                        validation.issues.append(
                            "Unfinished research calls were stopped by their time budgets; "
                            "the ranking uses partial evidence."
                        )
                        checkpoint.validation = validation
                    sources = self._collect_sources(evidence_by_place)
                    remaining_hard_time = hard_deadline - asyncio.get_running_loop().time()
                    if remaining_hard_time <= 1.0:
                        response_text = render_recommendation_fallback(
                            profile,
                            evaluations,
                            validation,
                            sources,
                            max_final_recommendations=self._max_final_recommendations,
                        )
                    else:
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
                            llm_timeout_seconds=remaining_hard_time - 1.0,
                        )
                    state = AgentState.COMPLETED

                logger.info(
                    "agent_phase request_id=%s phase=%s duration_seconds=%.3f next_state=%s",
                    request_id,
                    current_state,
                    asyncio.get_running_loop().time() - phase_started_at,
                    state,
                )

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
