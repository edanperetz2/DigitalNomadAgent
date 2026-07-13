from app.agent.state import MAX_STATE_TRANSITIONS, TERMINAL_STATES, AgentState

EXPECTED_STATES = {
    "received",
    "interpreting",
    "clarification_required",
    "planning_research",
    "executing_tools",
    "evaluating",
    "validating",
    "researching_gap",
    "generating_response",
    "completed",
    "failed",
}


def test_all_required_states_present():
    values = {s.value for s in AgentState}
    assert values == EXPECTED_STATES


def test_terminal_states():
    assert TERMINAL_STATES == {AgentState.COMPLETED, AgentState.FAILED}


def test_iteration_cap_is_positive():
    assert MAX_STATE_TRANSITIONS > 0
