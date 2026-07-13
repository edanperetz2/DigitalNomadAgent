"""F. BudgetFitTool -- estimates whether a destination broadly fits the stated
budget, using a local, clearly dated, curated dataset (never live pricing
sites, never Airbnb/Booking.com/Numbeo/proprietary sources)."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "cost_estimates.csv"


@lru_cache
def _load_cost_table() -> dict[str, dict]:
    table: dict[str, dict] = {}
    if not DATA_PATH.exists():
        return table
    with DATA_PATH.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            table[row["city"].strip().lower()] = row
    return table


class BudgetFitTool:
    name = "BudgetFitTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        table = _load_cost_table()
        row = table.get(candidate.place_name.strip().lower())

        if row is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="PlaceMatch curated cost estimates",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="No cost estimate data is available for this destination.",
            )

        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "lower_monthly_estimate": float(row["lower_monthly_estimate"]),
                "upper_monthly_estimate": float(row["upper_monthly_estimate"]),
                "currency": row["currency"],
                "included_categories": row["included_categories"],
            },
            source_name=row["source_name"],
            source_url=row["source_url"] or None,
            retrieved_at=datetime.now(UTC),
            data_date=row["data_date"],
            confidence="low",
            warnings=[
                "This is a curated sample/test-only estimate, not a live or verified price; "
                "treat it as a rough order of magnitude only."
            ],
        )
        return result
