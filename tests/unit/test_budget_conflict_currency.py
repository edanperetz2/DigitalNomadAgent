"""The shortfall has to be stated in one currency, not two.

P03 stated a 700 EUR budget and the cheapest researched city came back at 1,031
USD. The conflict line divided one by the other and announced "roughly 1.5x
that", setting a euro figure against a dollar figure as though they were the
same unit. The real multiple is about 1.36x.

The conversion already exists: BudgetFitTool compares in the estimate's
currency, so `remaining` is the converted budget minus the estimate and the two
sum back to it.
"""

from datetime import UTC, datetime

from app.agent.models import PlaceRequestProfile
from app.agent.orchestrator import _unmeetable_budget_conflict
from app.tools.base import ToolResult


def _budget_evidence(place, monthly_total, remaining, currency):
    """A BudgetFitTool result shaped the way budget_comparison reads it."""
    return ToolResult(
        tool_name="BudgetFitTool",
        place=place,
        source_name="WhereNext",
        source_url=None,
        retrieved_at=datetime.now(UTC),
        normalized_data={
            "budget_context": {"status": "converted_to_usd"},
            "fixed_cost_scenarios": {
                "center": {
                    "monthly_total_usd": monthly_total,
                    "budget_remaining_after_named_items": {"amount": remaining},
                }
            },
        },
    )


def _conflict(amount, currency, places):
    profile = PlaceRequestProfile(
        purpose="study",
        budget={"amount": amount, "currency": currency, "period": "monthly",
                "includes_accommodation": True, "confidence": "high"},
    )
    evidence = {place: [_budget_evidence(place, total, rem, "USD")]
                for place, total, rem in places}
    return _unmeetable_budget_conflict(profile, evidence)


def test_the_multiple_is_computed_in_one_currency():
    """700 EUR is about 760 USD, so 1,031 USD is 1.36x -- not the 1.5x that
    dividing dollars by euros produced."""
    line = _conflict(700, "EUR", [("Seville", 1031.0, -271.0), ("Porto", 1200.0, -440.0)])

    assert "1.4x" in line or "1.36x" in line
    assert "1.5x" not in line


def test_a_converted_budget_is_shown_next_to_the_figure_it_is_compared_with():
    line = _conflict(700, "EUR", [("Seville", 1031.0, -271.0), ("Porto", 1200.0, -440.0)])

    assert "700 EUR" in line
    assert "760 USD" in line


def test_a_matching_currency_states_no_conversion():
    """P08 states USD and is quoted in USD; adding "about 400 USD" would be noise."""
    line = _conflict(400, "USD", [("Stockholm", 1565.0, -1165.0), ("Oslo", 3500.0, -3100.0)])

    assert "400 USD" in line
    assert "about 400 USD" not in line
    assert "3.9x" in line


def test_the_cheapest_researched_place_is_the_one_named():
    line = _conflict(400, "USD", [("Stockholm", 1565.0, -1165.0), ("Oslo", 3500.0, -3100.0)])

    assert "Stockholm" in line
    assert "Oslo" not in line


def test_no_conflict_when_something_fits():
    assert _conflict(3000, "USD", [("Sofia", 700.0, 2300.0), ("Porto", 1200.0, 1800.0)]) is None
