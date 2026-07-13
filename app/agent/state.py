"""The PlaceMatch agent state machine states, exactly per the course spec."""

from __future__ import annotations

from enum import StrEnum


class AgentState(StrEnum):
    RECEIVED = "received"
    INTERPRETING = "interpreting"
    CLARIFICATION_REQUIRED = "clarification_required"
    PLANNING_RESEARCH = "planning_research"
    EXECUTING_TOOLS = "executing_tools"
    EVALUATING = "evaluating"
    VALIDATING = "validating"
    RESEARCHING_GAP = "researching_gap"
    GENERATING_RESPONSE = "generating_response"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_STATES = {AgentState.COMPLETED, AgentState.FAILED}
MAX_STATE_TRANSITIONS = 30
