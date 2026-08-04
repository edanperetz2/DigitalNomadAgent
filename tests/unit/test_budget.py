import pytest

from app.core.exceptions import BudgetExceededError
from app.evidence.database import Database
from app.llm.budget import BudgetManager


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "budget_test.db")
    await database.connect()
    yield database
    await database.close()


def _manager(db, **overrides) -> BudgetManager:
    defaults = dict(
        max_project_budget_usd=13.0,
        max_llm_calls_per_request=4,
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    )
    defaults.update(overrides)
    return BudgetManager(db, **defaults)


@pytest.mark.asyncio
async def test_call_recorded_with_estimated_cost(db):
    manager = _manager(db, input_cost_per_1m=1.0, output_cost_per_1m=2.0)
    await manager.record_call("req1", "Request Interpreter", "mock-llm", 1000, 500, None, success=True)
    remaining = await manager.get_local_estimated_remaining_budget()
    assert remaining < 13.0


@pytest.mark.asyncio
async def test_provider_cost_preferred_over_estimate(db):
    manager = _manager(db, input_cost_per_1m=100.0, output_cost_per_1m=100.0)
    await manager.record_call("req1", "Request Interpreter", "mock-llm", 1000, 500, 0.01, success=True)
    remaining = await manager.get_local_estimated_remaining_budget()
    assert remaining == pytest.approx(13.0 - 0.01)


@pytest.mark.asyncio
async def test_per_request_call_cap_enforced(db):
    manager = _manager(db, max_llm_calls_per_request=2)
    await manager.check_before_call("req1", "Request Interpreter", 10, 10)
    await manager.record_call("req1", "Request Interpreter", "m", 10, 10, 0.0, True)
    await manager.check_before_call("req1", "Agentic Research", 10, 10)
    await manager.record_call("req1", "Agentic Research", "m", 10, 10, 0.0, True)
    with pytest.raises(BudgetExceededError):
        await manager.check_before_call("req1", "Recommendation Generator", 10, 10)


@pytest.mark.asyncio
async def test_refuses_call_exceeding_project_budget(db):
    manager = _manager(db, max_project_budget_usd=0.001, input_cost_per_1m=1000.0, output_cost_per_1m=1000.0)
    with pytest.raises(BudgetExceededError):
        await manager.check_before_call("req1", "Request Interpreter", 1_000_000, 1_000_000)


@pytest.mark.asyncio
async def test_guard_fires_from_provider_costs_even_with_zero_configured_pricing(db):
    """The $13 cap must still stop runaway spend when local pricing is unset.

    LLM_INPUT/OUTPUT_COST_PER_1M default to 0, so the pre-call worst-case
    estimate is $0. Provider-reported costs (read from the response cost
    header) still accumulate in the ledger, so the cap engages once the
    running total passes it.
    """
    manager = _manager(db, max_project_budget_usd=1.0, max_llm_calls_per_request=100)
    await manager.record_call("req1", "Request Interpreter", "m", 10, 10, 0.60, True)
    # Still under the cap: this call is permitted.
    await manager.check_before_call("req2", "Agentic Research", 10, 10)

    await manager.record_call("req2", "Agentic Research", "m", 10, 10, 0.60, True)
    # Running total is now $1.20 against a $1.00 cap.
    with pytest.raises(BudgetExceededError):
        await manager.check_before_call("req3", "Recommendation Generator", 10, 10)


@pytest.mark.asyncio
async def test_zero_pricing_cannot_pre_empt_the_call_that_crosses_the_cap(db):
    """Documents the cost of leaving pricing at 0: the guard lags by one call.

    With real per-1M pricing configured, `check_before_call` refuses *before*
    the overshooting call is made (see
    test_refuses_call_exceeding_project_budget). With pricing at 0 the
    worst-case estimate is 0, so a call sitting exactly at the cap is still
    allowed through and the overshoot only surfaces afterwards.
    """
    manager = _manager(db, max_project_budget_usd=1.0, max_llm_calls_per_request=100)
    await manager.record_call("req1", "Request Interpreter", "m", 10, 10, 1.0, True)

    # Exactly at the cap, and an arbitrarily expensive call is still permitted.
    await manager.check_before_call("req2", "Agentic Research", 1_000_000, 1_000_000)

    manager_with_pricing = _manager(
        db, max_project_budget_usd=1.0, max_llm_calls_per_request=100,
        input_cost_per_1m=1000.0, output_cost_per_1m=1000.0,
    )
    with pytest.raises(BudgetExceededError):
        await manager_with_pricing.check_before_call("req2", "Agentic Research", 1_000_000, 1_000_000)


@pytest.mark.asyncio
async def test_failed_calls_still_count_against_the_budget(db):
    """A failed call can still have cost money upstream; it must be recorded."""
    manager = _manager(db, max_project_budget_usd=1.0, max_llm_calls_per_request=100)
    await manager.record_call("req1", "Request Interpreter", "m", 10, 10, 1.5, success=False)

    with pytest.raises(BudgetExceededError):
        await manager.check_before_call("req2", "Agentic Research", 10, 10)


@pytest.mark.asyncio
async def test_different_requests_have_independent_call_caps(db):
    manager = _manager(db, max_llm_calls_per_request=1)
    await manager.check_before_call("req1", "Request Interpreter", 10, 10)
    await manager.record_call("req1", "Request Interpreter", "m", 10, 10, 0.0, True)
    # A different request_id should not be blocked by req1's usage.
    await manager.check_before_call("req2", "Request Interpreter", 10, 10)
