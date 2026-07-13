"""E. TimezoneFitTool -- work-hour overlap between origin and candidate.

Uses timezonefinder (lazily imported -- it bundles a sizeable data file, so we
only pay that cost when this tool is actually selected) and Python zoneinfo.
Only invoked when timezone compatibility actually matters (see
app/agent/agentic_research.py tool-selection rules) -- never for unrelated
vacation prompts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult

# Small curated mapping from free-text origin hints to IANA timezones. Not
# exhaustive by design -- when the origin cannot be mapped, we say so rather
# than guessing.
ORIGIN_TIMEZONE_MAP: dict[str, str] = {
    "israel": "Asia/Jerusalem",
    "us eastern": "America/New_York",
    "us pacific": "America/Los_Angeles",
    "uk": "Europe/London",
    "united kingdom": "Europe/London",
    "germany": "Europe/Berlin",
    "india": "Asia/Kolkata",
    "australia": "Australia/Sydney",
    "japan": "Asia/Tokyo",
}

STANDARD_WORKDAY_HOURS = 8


def _utc_offset_hours(tz_name: str) -> float:
    now = datetime.now(ZoneInfo(tz_name))
    return (now.utcoffset() or UTC.utcoffset(now)).total_seconds() / 3600


class TimezoneFitTool:
    name = "TimezoneFitTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        if candidate.lat is None or candidate.lon is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="timezonefinder + zoneinfo",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="Cannot compute timezone fit without verified coordinates.",
            )

        from timezonefinder import (
            TimezoneFinder,  # lazy import: avoid paying data-load cost unless needed
        )

        tf = TimezoneFinder()
        candidate_tz = tf.timezone_at(lat=candidate.lat, lng=candidate.lon)
        if candidate_tz is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="timezonefinder + zoneinfo",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="Could not determine the destination's timezone.",
            )

        origin_key = (profile.origin or "").strip().lower()
        origin_tz = ORIGIN_TIMEZONE_MAP.get(origin_key)
        if origin_tz is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                normalized_data={"candidate_timezone": candidate_tz},
                source_name="timezonefinder + zoneinfo",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                warnings=["Origin timezone is unknown; overlap could not be computed."],
            )

        candidate_offset = _utc_offset_hours(candidate_tz)
        origin_offset = _utc_offset_hours(origin_tz)
        diff = abs(candidate_offset - origin_offset)
        overlap_hours = max(0.0, STANDARD_WORKDAY_HOURS - diff)

        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "candidate_timezone": candidate_tz,
                "origin_timezone": origin_tz,
                "utc_offset_diff_hours": round(diff, 1),
                "estimated_workday_overlap_hours": round(overlap_hours, 1),
            },
            source_name="timezonefinder + zoneinfo",
            retrieved_at=datetime.now(UTC),
            confidence="medium",
            warnings=["Overlap is estimated from standard 9-17 workday hours and ignores DST transitions."],
        )
