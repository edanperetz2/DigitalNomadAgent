"""Spoken-language evidence for a destination, from a static reference table.

No network: the answer is a property of the country, which geocoding already
resolved. Exists because "English being widely spoken matters a lot" (P06) was
captured as a hard constraint and then went unmeasured for every candidate,
letting Lisbon, Barcelona and Seville outrank London, Dublin and Belfast (D34).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.languages import english_reach, spoken_languages

SOURCE_NAME = "DigitalNomadAgent country language reference"
SOURCE_URL = "https://github.com/shanigoren/DigitalNomadAgent/blob/main/app/languages.py"

# How well a stated English requirement is met. Coarse by design: the underlying
# table is a judgement in three bands, and a finer score would imply a precision
# the reference does not have.
_ENGLISH_SCORES = {"native": 1.0, "widespread": 0.75, "limited": 0.25}


class LanguageTool:
    name = "LanguageTool"

    def _error(self, place: str, message: str) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            place=place,
            normalized_data={},
            source_name=SOURCE_NAME,
            source_url=SOURCE_URL,
            retrieved_at=datetime.now(UTC),
            confidence="low",
            error=message,
        )

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        now = datetime.now(UTC)
        country = candidate.country or ""
        if not country:
            return self._error(candidate.place_name, "Cannot resolve languages without a country.")

        languages = spoken_languages(country)
        reach = english_reach(country)
        if languages is None or reach is None:
            return self._error(
                candidate.place_name,
                f"{country} is not in the language reference table; language is left unmeasured.",
            )

        requested = [language.strip() for language in profile.preferred_languages if language.strip()]
        spoken_fold = {language.casefold() for language in languages}
        matched = [language for language in requested if language.casefold() in spoken_fold]

        normalized_data = {
            "country": country,
            "spoken_languages": list(languages),
            "english_reach": reach,
            "english_score": _ENGLISH_SCORES[reach],
            "requested_languages": requested,
            "matched_languages": matched,
        }
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data=normalized_data,
            source_name=SOURCE_NAME,
            source_url=SOURCE_URL,
            retrieved_at=now,
            confidence="medium",
            warnings=[
                "English reach is a coarse three-band judgement about getting by in cities, "
                "not a statistic about the whole country."
            ],
            evidence_items=[
                EvidenceItem(
                    criterion="language_spoken",
                    component="country_languages",
                    normalized_data=normalized_data,
                    source=EvidenceSource(
                        source_name=SOURCE_NAME,
                        source_url=SOURCE_URL,
                        retrieved_at=now,
                        confidence="medium",
                    ),
                )
            ],
        )
