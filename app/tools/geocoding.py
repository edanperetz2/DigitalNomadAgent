"""A. GeocodingTool -- verifies destination identity via OpenStreetMap Nominatim."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult
from app.tools.http_client import AsyncRateLimiter, JsonHttpClient

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MIN_IMPORTANCE_FOR_HIGH_CONFIDENCE = 0.5
MIN_IMPORTANCE_TO_ACCEPT = 0.15
AMBIGUITY_IMPORTANCE_GAP = 0.05
MAX_RESULTS = 5

CACHE_PARAMS = {
    "limit": MAX_RESULTS,
    "addressdetails": 1,
    "namedetails": 1,
    "accept-language": "en",
}

# Nominatim returns ISO alpha-2 codes. These aliases cover common user/LLM
# variants that cannot be compared directly with the returned country name.
COUNTRY_ALIASES: dict[str, str] = {
    "great britain": "GB",
    "uk": "GB",
    "united kingdom": "GB",
    "u s": "US",
    "u s a": "US",
    "us": "US",
    "usa": "US",
    "united states": "US",
    "united states of america": "US",
    "uae": "AE",
    "united arab emirates": "AE",
}


@dataclass(frozen=True)
class _GeocodingMatch:
    canonical_name: str
    display_name: str
    lat: float
    lon: float
    country: str
    country_code: str
    region: str | None
    region_key: str
    place_id: int
    osm_type: str
    osm_id: int
    importance: float

    @property
    def locality_key(self) -> tuple[str, str, str]:
        return (
            _normalize_text(self.canonical_name),
            self.region_key,
            self.country_code,
        )

    @property
    def source_url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join("".join(character if character.isalnum() else " " for character in ascii_text).casefold().split())


def _country_matches(expected_country: str, actual_country: str, actual_code: str) -> bool:
    expected = _normalize_text(expected_country)
    actual = _normalize_text(actual_country)
    code = actual_code.upper()
    expected_code = COUNTRY_ALIASES.get(expected)
    if expected_code is not None:
        return expected_code == code
    if len(expected) == 2 and expected.isalpha():
        return expected.upper() == code
    return expected == actual


def _require_float(value: Any, field_name: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Nominatim returned a non-finite {field_name}")
    return parsed


def _parse_match(raw: Any, expected_country: str) -> _GeocodingMatch | None:
    if not isinstance(raw, dict):
        raise ValueError("Nominatim returned a non-object match")

    address = raw.get("address")
    if not isinstance(address, dict):
        raise ValueError("Nominatim match is missing address details")
    country = str(address.get("country") or "").strip()
    country_code = str(address.get("country_code") or "").strip().upper()
    if not country or len(country_code) != 2:
        raise ValueError("Nominatim match is missing country identity")
    if not _country_matches(expected_country, country, country_code):
        return None

    importance = _require_float(raw.get("importance"), "importance")
    lat = _require_float(raw.get("lat"), "latitude")
    lon = _require_float(raw.get("lon"), "longitude")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Nominatim returned coordinates outside valid ranges")

    namedetails = raw.get("namedetails") if isinstance(raw.get("namedetails"), dict) else {}
    canonical_name = str(
        namedetails.get("name:en")
        or raw.get("name")
        or namedetails.get("name")
        or address.get("city")
        or address.get("town")
        or address.get("village")
        or ""
    ).strip()
    display_name = str(raw.get("display_name") or "").strip()
    if not canonical_name or not display_name:
        raise ValueError("Nominatim match is missing a usable place name")

    osm_type = str(raw.get("osm_type") or "").lower()
    if osm_type not in {"node", "way", "relation"}:
        raise ValueError("Nominatim match has an invalid OSM type")
    place_id = int(raw.get("place_id"))
    osm_id = int(raw.get("osm_id"))
    if place_id <= 0 or osm_id <= 0:
        raise ValueError("Nominatim match has an invalid OSM identity")

    state = str(address.get("state") or address.get("province") or address.get("region") or "").strip()
    county = str(address.get("county") or address.get("state_district") or "").strip()
    region = state or county or None
    return _GeocodingMatch(
        canonical_name=canonical_name,
        display_name=display_name,
        lat=lat,
        lon=lon,
        country=country,
        country_code=country_code,
        region=region,
        region_key=_normalize_text(state or county),
        place_id=place_id,
        osm_type=osm_type,
        osm_id=osm_id,
        importance=importance,
    )


class GeocodingTool:
    name = "GeocodingTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 10.0,
        rate_limiter: AsyncRateLimiter | None = None,
        http: JsonHttpClient | None = None,
    ):
        self._cache = cache
        self._rate_limiter = rate_limiter or AsyncRateLimiter(1.0)
        self._http = http or JsonHttpClient(timeout=timeout)

    async def _fetch(self, query: str) -> list[dict]:
        payload = await self._http.get_json(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "dedupe": 1, **CACHE_PARAMS},
            before_request=self._rate_limiter.wait,
        )
        if not isinstance(payload, list):
            raise ValueError("Nominatim returned a non-list JSON response")
        return payload

    @staticmethod
    def _error(place: str, message: str) -> ToolResult:
        return ToolResult(
            tool_name=GeocodingTool.name,
            place=place,
            source_name="OpenStreetMap Nominatim",
            source_url="https://nominatim.openstreetmap.org/",
            retrieved_at=datetime.now(UTC),
            confidence="low",
            error=message,
        )

    @staticmethod
    def _select_match(
        results: list[dict], expected_country: str
    ) -> tuple[_GeocodingMatch | None, str | None]:
        if not results:
            return None, "This destination could not be reliably verified."

        accepted: list[_GeocodingMatch] = []
        country_mismatches = 0
        low_importance_matches = 0
        malformed_matches = 0
        for raw in results:
            try:
                match = _parse_match(raw, expected_country)
            except (TypeError, ValueError, OverflowError):
                malformed_matches += 1
                continue
            if match is None:
                country_mismatches += 1
            elif match.importance < MIN_IMPORTANCE_TO_ACCEPT:
                low_importance_matches += 1
            else:
                accepted.append(match)

        if not accepted:
            if country_mismatches:
                return None, "The geocoding matches did not agree with the requested country."
            if low_importance_matches:
                return None, "This destination could not be reliably verified because all matches had low importance."
            if malformed_matches:
                raise ValueError("Nominatim returned matches, but none had a usable response structure")
            return None, "This destination could not be reliably verified."

        locality_groups: dict[tuple[str, str, str], _GeocodingMatch] = {}
        for match in accepted:
            current = locality_groups.get(match.locality_key)
            if current is None or match.importance > current.importance:
                locality_groups[match.locality_key] = match

        ranked = sorted(locality_groups.values(), key=lambda match: match.importance, reverse=True)
        top = ranked[0]
        if len(ranked) > 1 and top.importance - ranked[1].importance <= AMBIGUITY_IMPORTANCE_GAP:
            return (
                None,
                "This destination is ambiguous within the requested country; please specify its region or state.",
            )
        return top, None

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        query = f"{candidate.place_name}, {candidate.country}"
        cached, stale = await self._cache.get(self.name, query, CACHE_PARAMS)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        try:
            results = await self._fetch(query)
            match, rejection = self._select_match(results, candidate.country)
        except Exception as exc:  # noqa: BLE001 - preserve stale identity on any live-response failure
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached geocoding data; live lookup failed.")
                return stale_result
            return self._error(candidate.place_name, f"Geocoding lookup failed: {exc}")

        if match is None:
            return self._error(candidate.place_name, rejection or "This destination could not be reliably verified.")

        confidence = "high" if match.importance >= MIN_IMPORTANCE_FOR_HIGH_CONFIDENCE else "medium"
        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "canonical_name": match.canonical_name,
                "display_name": match.display_name,
                "lat": match.lat,
                "lon": match.lon,
                "country": match.country,
                "country_code": match.country_code,
                "region": match.region,
                "place_id": match.place_id,
                "osm_type": match.osm_type,
                "osm_id": match.osm_id,
                "importance": match.importance,
            },
            source_name="OpenStreetMap Nominatim",
            source_url=match.source_url,
            retrieved_at=datetime.now(UTC),
            confidence=confidence,
        )
        await self._cache.set(self.name, query, CACHE_PARAMS, result.model_dump(mode="json"))
        return result
