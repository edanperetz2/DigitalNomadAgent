"""TimezoneFitTool -- estimated standard-workday overlap with an origin."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.http_client import JsonHttpClient
from app.tools.origin_resolution import (
    GEOCODING_PARAMS,
    GEOCODING_SOURCE_URL,
    ORIGIN_CACHE_TOOL,
    OriginResolver,
    valid_timezone,
)

__all__ = ["GEOCODING_PARAMS", "ORIGIN_CACHE_TOOL", "TimezoneFitTool"]

STANDARD_WORKDAY_HOURS = 8


TimezoneAt = Callable[[float, float], str | None]
Today = Callable[[], date]


def _representative_date(target_months: list[int], today: date) -> tuple[date, str | None]:
    if not target_months:
        return today, "No target month was supplied; timezone overlap uses the current date."

    month = target_months[0]
    representative = date(today.year, month, 15)
    if representative < today:
        representative = date(today.year + 1, month, 15)
    return representative, None


def _utc_offset_hours(timezone_name: str, on_date: date) -> float:
    local_noon = datetime(on_date.year, on_date.month, on_date.day, 12, tzinfo=ZoneInfo(timezone_name))
    offset = local_noon.utcoffset()
    if offset is None:
        raise ValueError(f"Timezone {timezone_name!r} has no UTC offset on {on_date.isoformat()}.")
    return offset.total_seconds() / 3600


def _circular_offset_difference(first: float, second: float) -> tuple[float, float]:
    raw_difference = abs(first - second)
    wrapped_difference = raw_difference % 24
    return raw_difference, min(wrapped_difference, 24 - wrapped_difference)


class TimezoneFitTool:
    name = "TimezoneFitTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 10.0,
        *,
        http: JsonHttpClient | None = None,
        origin_resolver: OriginResolver | None = None,
        timezone_at: TimezoneAt | None = None,
        today: Today = date.today,
    ):
        self._cache = cache
        self._http = http or JsonHttpClient(timeout=timeout)
        self._origin_resolver = origin_resolver or OriginResolver(cache, http=self._http)
        self._timezone_at = timezone_at
        self._today = today
        self._timezone_finder: Any | None = None

    def _destination_timezone(self, lat: float, lon: float) -> str | None:
        if self._timezone_at is not None:
            return self._timezone_at(lat, lon)
        if self._timezone_finder is None:
            from timezonefinder import TimezoneFinder

            self._timezone_finder = TimezoneFinder()
        return self._timezone_finder.timezone_at(lat=lat, lng=lon)

    @staticmethod
    def _calculation_evidence(
        candidate_timezone: str,
        representative_date: date,
        now: datetime,
        normalized_data: dict[str, Any],
    ) -> EvidenceItem:
        return EvidenceItem(
            criterion="timezone",
            component="workday_overlap",
            normalized_data={
                key: normalized_data[key]
                for key in (
                    "candidate_timezone",
                    "origin_timezone",
                    "representative_date",
                    "candidate_utc_offset_hours",
                    "origin_utc_offset_hours",
                    "utc_offset_diff_hours",
                    "estimated_workday_overlap_hours",
                )
                if key in normalized_data
            }
            or {"candidate_timezone": candidate_timezone, "representative_date": representative_date.isoformat()},
            source=EvidenceSource(
                source_name="timezonefinder + IANA time zone database",
                retrieved_at=now,
                data_date=representative_date.isoformat(),
                confidence="medium",
            ),
        )

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        now = datetime.now(UTC)
        if candidate.lat is None or candidate.lon is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="timezonefinder + IANA time zone database",
                retrieved_at=now,
                confidence="low",
                error="Cannot compute timezone fit without verified coordinates.",
            )

        candidate_timezone = self._destination_timezone(candidate.lat, candidate.lon)
        if candidate_timezone is None or valid_timezone(candidate_timezone) is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="timezonefinder + IANA time zone database",
                retrieved_at=now,
                confidence="low",
                error="Could not determine the destination's timezone.",
            )

        representative_date, date_warning = _representative_date(profile.target_months, self._today())
        origin_input = (profile.origin or "").strip()
        if not origin_input:
            normalized_data = {
                "candidate_timezone": candidate_timezone,
                "representative_date": representative_date.isoformat(),
            }
            warnings = ["Origin timezone is unknown; overlap could not be computed."]
            if date_warning:
                warnings.append(date_warning)
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                normalized_data=normalized_data,
                source_name="timezonefinder + IANA time zone database",
                retrieved_at=now,
                confidence="low",
                warnings=warnings,
                evidence_items=[
                    self._calculation_evidence(candidate_timezone, representative_date, now, normalized_data)
                ],
            )

        origin, warnings = await self._origin_resolver.resolve(origin_input, now)
        if date_warning:
            warnings.append(date_warning)
        if origin is None:
            normalized_data = {
                "candidate_timezone": candidate_timezone,
                "origin_input": origin_input,
                "representative_date": representative_date.isoformat(),
            }
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                normalized_data=normalized_data,
                source_name="timezonefinder + IANA time zone database",
                retrieved_at=now,
                confidence="low",
                warnings=warnings,
                error="Could not resolve the origin timezone.",
                evidence_items=[
                    self._calculation_evidence(candidate_timezone, representative_date, now, normalized_data)
                ],
            )

        candidate_offset = _utc_offset_hours(candidate_timezone, representative_date)
        origin_offset = _utc_offset_hours(origin.timezone, representative_date)
        raw_difference, difference = _circular_offset_difference(candidate_offset, origin_offset)
        overlap_hours = max(0.0, STANDARD_WORKDAY_HOURS - difference)
        normalized_data = {
            "origin_input": origin_input,
            "resolved_origin_name": origin.name,
            "resolved_origin_country": origin.country,
            "resolved_origin_country_code": origin.country_code,
            "origin_resolution_method": origin.method,
            "origin_resolution_was_cached": origin.cached,
            "candidate_timezone": candidate_timezone,
            "origin_timezone": origin.timezone,
            "representative_date": representative_date.isoformat(),
            "candidate_utc_offset_hours": round(candidate_offset, 1),
            "origin_utc_offset_hours": round(origin_offset, 1),
            "raw_utc_offset_diff_hours": round(raw_difference, 1),
            "utc_offset_diff_hours": round(difference, 1),
            "estimated_workday_overlap_hours": round(overlap_hours, 1),
        }
        warnings.append("Overlap assumes standard 09:00-17:00 workdays in both locations.")

        evidence_items = [self._calculation_evidence(candidate_timezone, representative_date, now, normalized_data)]
        if origin.method == "open_meteo":
            evidence_items.insert(
                0,
                EvidenceItem(
                    criterion="timezone",
                    component="origin_resolution",
                    normalized_data={
                        "origin_input": origin_input,
                        "resolved_origin_name": origin.name,
                        "resolved_origin_country": origin.country,
                        "resolved_origin_country_code": origin.country_code,
                        "origin_timezone": origin.timezone,
                    },
                    source=EvidenceSource(
                        source_name="Open-Meteo Geocoding API",
                        source_url=GEOCODING_SOURCE_URL,
                        retrieved_at=origin.retrieved_at,
                        confidence="medium",
                        stale=origin.stale,
                    ),
                    warnings=warnings[:-1],
                ),
            )

        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data=normalized_data,
            source_name="timezonefinder + IANA time zone database",
            retrieved_at=now,
            data_date=representative_date.isoformat(),
            confidence="medium",
            warnings=warnings,
            stale=origin.stale,
            evidence_items=evidence_items,
        )
