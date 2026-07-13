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
async def test_different_requests_have_independent_call_caps(db):
    manager = _manager(db, max_llm_calls_per_request=1)
    await manager.check_before_call("req1", "Request Interpreter", 10, 10)
    await manager.record_call("req1", "Request Interpreter", "m", 10, 10, 0.0, True)
    # A different request_id should not be blocked by req1's usage.
    await manager.check_before_call("req2", "Request Interpreter", 10, 10)
