"""TimezoneFitTool -- estimated standard-workday overlap with an origin."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.http_client import JsonHttpClient

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODING_SOURCE_URL = "https://open-meteo.com/en/docs/geocoding-api"
GEOCODING_PARAMS: dict[str, int | str] = {"count": 5, "language": "en", "format": "json"}
ORIGIN_CACHE_TOOL = "TimezoneFitTool:origin"
STANDARD_WORKDAY_HOURS = 8

# Fast, deliberately pragmatic defaults for common city and country inputs.
# Countries spanning several timezones use a visible representative-city guess.
ORIGIN_TIMEZONE_MAP: dict[str, tuple[str, str | None, str]] = {
    "argentina": ("Buenos Aires", "Argentina", "America/Argentina/Buenos_Aires"),
    "australia": ("Sydney", "Australia", "Australia/Sydney"),
    "barcelona": ("Barcelona", "Spain", "Europe/Madrid"),
    "berlin": ("Berlin", "Germany", "Europe/Berlin"),
    "brazil": ("Sao Paulo", "Brazil", "America/Sao_Paulo"),
    "canada": ("Toronto", "Canada", "America/Toronto"),
    "china": ("Shanghai", "China", "Asia/Shanghai"),
    "dubai": ("Dubai", "United Arab Emirates", "Asia/Dubai"),
    "france": ("Paris", "France", "Europe/Paris"),
    "germany": ("Berlin", "Germany", "Europe/Berlin"),
    "greece": ("Athens", "Greece", "Europe/Athens"),
    "haifa": ("Haifa", "Israel", "Asia/Jerusalem"),
    "iceland": ("Reykjavik", "Iceland", "Atlantic/Reykjavik"),
    "india": ("India", "India", "Asia/Kolkata"),
    "israel": ("Israel", "Israel", "Asia/Jerusalem"),
    "italy": ("Rome", "Italy", "Europe/Rome"),
    "japan": ("Japan", "Japan", "Asia/Tokyo"),
    "jerusalem": ("Jerusalem", "Israel", "Asia/Jerusalem"),
    "london": ("London", "United Kingdom", "Europe/London"),
    "madrid": ("Madrid", "Spain", "Europe/Madrid"),
    "mexico": ("Mexico City", "Mexico", "America/Mexico_City"),
    "netherlands": ("Amsterdam", "Netherlands", "Europe/Amsterdam"),
    "new zealand": ("Auckland", "New Zealand", "Pacific/Auckland"),
    "portugal": ("Lisbon", "Portugal", "Europe/Lisbon"),
    "reykjavik": ("Reykjavik", "Iceland", "Atlantic/Reykjavik"),
    "singapore": ("Singapore", "Singapore", "Asia/Singapore"),
    "south africa": ("Johannesburg", "South Africa", "Africa/Johannesburg"),
    "south korea": ("Seoul", "South Korea", "Asia/Seoul"),
    "spain": ("Madrid", "Spain", "Europe/Madrid"),
    "st. john's": ("St. John's", "Canada", "America/St_Johns"),
    "tel aviv": ("Tel Aviv", "Israel", "Asia/Jerusalem"),
    "thailand": ("Bangkok", "Thailand", "Asia/Bangkok"),
    "turkey": ("Istanbul", "Turkey", "Europe/Istanbul"),
    "uk": ("United Kingdom", "United Kingdom", "Europe/London"),
    "united arab emirates": ("Dubai", "United Arab Emirates", "Asia/Dubai"),
    "united kingdom": ("United Kingdom", "United Kingdom", "Europe/London"),
    "united states": ("New York", "United States", "America/New_York"),
    "us": ("New York", "United States", "America/New_York"),
    "us eastern": ("US Eastern Time", "United States", "America/New_York"),
    "us pacific": ("US Pacific Time", "United States", "America/Los_Angeles"),
    "usa": ("New York", "United States", "America/New_York"),
    "vietnam": ("Ho Chi Minh City", "Vietnam", "Asia/Ho_Chi_Minh"),
}


@dataclass(frozen=True)
class OriginResolution:
    name: str
    country: str | None
    timezone: str
    method: str
    retrieved_at: datetime
    country_code: str | None = None
    cached: bool = False
    stale: bool = False


TimezoneAt = Callable[[float, float], str | None]
Today = Callable[[], date]


def _normalize_origin(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _provider_query(origin: str) -> str:
    """Open-Meteo searches place names, not comma-qualified display strings."""
    return origin.split(",", maxsplit=1)[0].strip()


def _valid_timezone(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    name = value.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


def _local_origin(origin: str, retrieved_at: datetime) -> OriginResolution | None:
    normalized = _normalize_origin(origin)
    alias = ORIGIN_TIMEZONE_MAP.get(normalized)
    if alias is None and "," in normalized:
        alias = ORIGIN_TIMEZONE_MAP.get(normalized.split(",", maxsplit=1)[0].strip())
    if alias is not None:
        name, country, timezone_name = alias
        return OriginResolution(name, country, timezone_name, "local_alias", retrieved_at)

    timezone_name = _valid_timezone(origin)
    if timezone_name is not None:
        return OriginResolution(origin.strip(), None, timezone_name, "direct_iana", retrieved_at)
    return None


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
        timezone_at: TimezoneAt | None = None,
        today: Today = date.today,
    ):
        self._cache = cache
        self._http = http or JsonHttpClient(timeout=timeout)
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
    def _resolution_from_cache(payload: dict, *, stale: bool) -> OriginResolution:
        return OriginResolution(
            name=str(payload["name"]),
            country=str(payload["country"]) if payload.get("country") else None,
            timezone=str(payload["timezone"]),
            method="open_meteo",
            retrieved_at=datetime.fromisoformat(str(payload["retrieved_at"])),
            country_code=str(payload["country_code"]) if payload.get("country_code") else None,
            cached=True,
            stale=stale,
        )

    @staticmethod
    def _provider_resolution(payload: object, retrieved_at: datetime) -> OriginResolution | None:
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("Open-Meteo geocoding returned an invalid response structure.")
        for result in payload["results"]:
            if not isinstance(result, dict):
                continue
            timezone_name = _valid_timezone(result.get("timezone"))
            name = result.get("name")
            if timezone_name is None or not isinstance(name, str) or not name.strip():
                continue
            country = result.get("country")
            country_code = result.get("country_code")
            return OriginResolution(
                name=name.strip(),
                country=country.strip() if isinstance(country, str) and country.strip() else None,
                timezone=timezone_name,
                method="open_meteo",
                retrieved_at=retrieved_at,
                country_code=(
                    country_code.strip().upper()
                    if isinstance(country_code, str) and len(country_code.strip()) == 2
                    else None
                ),
            )
        return None

    async def _resolve_origin(self, origin: str, now: datetime) -> tuple[OriginResolution | None, list[str]]:
        local = _local_origin(origin, now)
        if local is not None:
            warnings = []
            if _normalize_origin(origin) in {"australia", "brazil", "canada", "mexico", "united states", "us", "usa"}:
                warnings.append(f"{origin.strip()} spans multiple timezones; using {local.name} as a visible default.")
            return local, warnings

        cache_place = _normalize_origin(origin)
        cached_payload, stale = await self._cache.get(
            ORIGIN_CACHE_TOOL, cache_place, GEOCODING_PARAMS, ttl_key=ORIGIN_CACHE_TOOL
        )
        cached: OriginResolution | None = None
        if cached_payload is not None:
            try:
                cached = self._resolution_from_cache(cached_payload, stale=stale)
            except (KeyError, TypeError, ValueError):
                cached = None
        if cached is not None and not stale:
            return cached, ["Origin was resolved from a cached Open-Meteo top match."]

        try:
            payload = await self._http.get_json(
                GEOCODING_URL,
                params={"name": _provider_query(origin), **GEOCODING_PARAMS},
            )
            resolution = self._provider_resolution(payload, now)
            if resolution is None:
                raise ValueError("Open-Meteo geocoding returned no usable timezone match.")
        except Exception as exc:  # noqa: BLE001 - a stale resolution is safer than losing known evidence
            if cached is not None:
                return cached, [f"Using stale cached origin resolution because the live lookup failed: {exc}"]
            return None, [f"Origin lookup failed: {exc}"]

        await self._cache.set(
            ORIGIN_CACHE_TOOL,
            cache_place,
            GEOCODING_PARAMS,
            {
                "name": resolution.name,
                "country": resolution.country,
                "country_code": resolution.country_code,
                "timezone": resolution.timezone,
                "retrieved_at": resolution.retrieved_at.isoformat(),
            },
            ttl_key=ORIGIN_CACHE_TOOL,
        )
        return resolution, [
            "Origin uses Open-Meteo's top usable match; verify the displayed place if the input was ambiguous."
        ]

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
        if candidate_timezone is None or _valid_timezone(candidate_timezone) is None:
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

        origin, warnings = await self._resolve_origin(origin_input, now)
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
