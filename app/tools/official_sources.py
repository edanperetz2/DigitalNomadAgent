"""J. OfficialSourceTool -- curated official sources for sensitive claims.

Never invents a visa rule, never provides a legal conclusion, never
guarantees entry eligibility. Uses only a curated allow-listed data file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "official_sources.json"


@lru_cache
def _load_official_sources() -> dict[str, list[dict]]:
    if not DATA_PATH.exists():
        return {}
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


class OfficialSourceTool:
    name = "OfficialSourceTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        sources_by_country = _load_official_sources()
        entries = sources_by_country.get(candidate.country)
        now = datetime.now(UTC)

        if not entries:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="PlaceMatch curated official-source directory",
                retrieved_at=now,
                confidence="low",
                error="No curated official sources are available for this country.",
                warnings=[
                    "Visa, entry, and legal requirements must always be verified directly "
                    "with official government sources."
                ],
            )

        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={"official_links": entries},
            source_name="Curated official government/tourism sources",
            source_url=entries[0]["url"],
            retrieved_at=now,
            confidence="medium",
            warnings=[
                "This tool never determines visa eligibility or provides legal conclusions; "
                "always confirm current rules with the official sources listed."
            ],
        )
