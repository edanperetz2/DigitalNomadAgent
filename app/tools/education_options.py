"""G. EducationOptionsTool -- study-related evidence from official university sources.

Only called for study-purpose requests (see app/agent/agentic_research.py).
Never claims admission eligibility or current program availability without
official confirmation -- distinguishes "a university exists here" from "a
matching department/program exists" from "a specific program is currently
open" (the last is never claimed).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult

# Curated, hand-verified official university homepages. Real, stable URLs.
EDUCATION_DATA: dict[str, dict] = {
    "berlin": {
        "universities": [
            {"name": "Humboldt University of Berlin", "url": "https://www.hu-berlin.de/en"},
            {"name": "Technical University of Berlin", "url": "https://www.tu.berlin/en/"},
        ],
        "fields_common": ["computer science", "data science", "engineering", "economics"],
    },
    "warsaw": {
        "universities": [{"name": "University of Warsaw", "url": "https://www.uw.edu.pl/en/"}],
        "fields_common": ["economics", "computer science"],
    },
    "dublin": {
        "universities": [
            {"name": "Trinity College Dublin", "url": "https://www.tcd.ie/"},
            {"name": "University College Dublin", "url": "https://www.ucd.ie/"},
        ],
        "fields_common": ["business", "computer science", "law"],
    },
    "porto": {
        "universities": [{"name": "University of Porto", "url": "https://www.up.pt/portal/en/"}],
        "fields_common": ["engineering", "economics"],
    },
    "melbourne": {
        "universities": [{"name": "University of Melbourne", "url": "https://www.unimelb.edu.au/"}],
        "fields_common": ["business", "computer science", "medicine", "law"],
    },
}


class EducationOptionsTool:
    name = "EducationOptionsTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        entry = EDUCATION_DATA.get(candidate.place_name.strip().lower())
        now = datetime.now(UTC)

        if entry is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="PlaceMatch curated university directory",
                retrieved_at=now,
                confidence="low",
                error="No curated university information is available for this destination.",
            )

        field = (profile.study_field or "").lower()
        field_matched = bool(field) and any(field in f for f in entry["fields_common"])

        warnings = [
            "University existence is confirmed, but current program availability and "
            "admission requirements must be verified on the official website."
        ]
        if not field_matched:
            warnings.append("The requested academic field could not be confirmed for this city.")

        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "universities": entry["universities"],
                "field_matched": field_matched,
                "match_score": 0.8 if field_matched else 0.4,
            },
            source_name="Official university websites",
            source_url=entry["universities"][0]["url"],
            retrieved_at=now,
            confidence="medium" if field_matched else "low",
            warnings=warnings,
        )
        return result
