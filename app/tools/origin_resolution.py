"""Shared cache-first origin resolution for timezone and distance evidence."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.evidence.cache import ToolCache
from app.tools.http_client import JsonHttpClient

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODING_SOURCE_URL = "https://open-meteo.com/en/docs/geocoding-api"
GEOCODING_PARAMS: dict[str, int | str] = {"count": 5, "language": "en", "format": "json"}
ORIGIN_CACHE_TOOL = "OriginResolver:open_meteo"

# Fast, deliberately pragmatic defaults for timezone-only use. Country inputs
# spanning multiple zones retain their visible representative-city guess.
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

MULTI_TIMEZONE_DEFAULTS = {"australia", "brazil", "canada", "mexico", "united states", "us", "usa"}


@dataclass(frozen=True)
class OriginResolution:
    name: str
    country: str | None
    timezone: str
    method: str
    retrieved_at: datetime
    country_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    cached: bool = False
    stale: bool = False


def normalize_origin(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def provider_query(origin: str) -> str:
    """Open-Meteo searches place names, not comma-qualified display strings."""
    return origin.split(",", maxsplit=1)[0].strip()


def valid_timezone(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    name = value.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


def local_origin(origin: str, retrieved_at: datetime) -> OriginResolution | None:
    normalized = normalize_origin(origin)
    alias = ORIGIN_TIMEZONE_MAP.get(normalized)
    if alias is None and "," in normalized:
        alias = ORIGIN_TIMEZONE_MAP.get(normalized.split(",", maxsplit=1)[0].strip())
    if alias is not None:
        name, country, timezone_name = alias
        return OriginResolution(name, country, timezone_name, "local_alias", retrieved_at)

    timezone_name = valid_timezone(origin)
    if timezone_name is not None:
        return OriginResolution(origin.strip(), None, timezone_name, "direct_iana", retrieved_at)
    return None


def _coordinate(value: object, *, latitude: bool) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    limit = 90 if latitude else 180
    return coordinate if math.isfinite(coordinate) and -limit <= coordinate <= limit else None


class OriginResolver:
    """Resolve an origin once, with cache rechecks serializing duplicate misses."""

    def __init__(self, cache: ToolCache, *, http: JsonHttpClient) -> None:
        self._cache = cache
        self._http = http
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _from_cache(payload: dict, *, stale: bool) -> OriginResolution:
        return OriginResolution(
            name=str(payload["name"]),
            country=str(payload["country"]) if payload.get("country") else None,
            timezone=str(payload["timezone"]),
            method="open_meteo",
            retrieved_at=datetime.fromisoformat(str(payload["retrieved_at"])),
            country_code=str(payload["country_code"]) if payload.get("country_code") else None,
            latitude=_coordinate(payload.get("latitude"), latitude=True),
            longitude=_coordinate(payload.get("longitude"), latitude=False),
            cached=True,
            stale=stale,
        )

    @staticmethod
    def _from_provider(payload: object, retrieved_at: datetime) -> OriginResolution | None:
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("Open-Meteo geocoding returned an invalid response structure.")
        for result in payload["results"]:
            if not isinstance(result, dict):
                continue
            timezone_name = valid_timezone(result.get("timezone"))
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
                latitude=_coordinate(result.get("latitude"), latitude=True),
                longitude=_coordinate(result.get("longitude"), latitude=False),
            )
        return None

    async def resolve(
        self,
        origin: str,
        now: datetime,
        *,
        require_coordinates: bool = False,
    ) -> tuple[OriginResolution | None, list[str]]:
        local = local_origin(origin, now)
        if local is not None and not require_coordinates:
            warnings = []
            if normalize_origin(origin) in MULTI_TIMEZONE_DEFAULTS:
                warnings.append(f"{origin.strip()} spans multiple timezones; using {local.name} as a visible default.")
            return local, warnings

        cache_place = normalize_origin(origin)
        lock = self._locks.setdefault(cache_place, asyncio.Lock())
        async with lock:
            return await self._resolve_provider(origin, cache_place, now, require_coordinates=require_coordinates)

    async def _resolve_provider(
        self,
        origin: str,
        cache_place: str,
        now: datetime,
        *,
        require_coordinates: bool,
    ) -> tuple[OriginResolution | None, list[str]]:
        cached_payload, stale = await self._cache.get(
            ORIGIN_CACHE_TOOL, cache_place, GEOCODING_PARAMS, ttl_key=ORIGIN_CACHE_TOOL
        )
        cached: OriginResolution | None = None
        if cached_payload is not None:
            try:
                cached = self._from_cache(cached_payload, stale=stale)
                if valid_timezone(cached.timezone) is None:
                    cached = None
            except (KeyError, TypeError, ValueError):
                cached = None
        cached_usable = cached is not None and (
            not require_coordinates or (cached.latitude is not None and cached.longitude is not None)
        )
        if cached_usable and not stale:
            return cached, ["Origin was resolved from a cached Open-Meteo top match."]

        try:
            payload = await self._http.get_json(
                GEOCODING_URL,
                params={"name": provider_query(origin), **GEOCODING_PARAMS},
            )
            resolution = self._from_provider(payload, now)
            if resolution is None:
                raise ValueError("Open-Meteo geocoding returned no usable timezone match.")
            if require_coordinates and (resolution.latitude is None or resolution.longitude is None):
                raise ValueError("Open-Meteo geocoding returned no usable coordinate match.")
        except Exception as exc:  # noqa: BLE001 - stale evidence is preferable to losing known identity
            if cached_usable:
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
                "latitude": resolution.latitude,
                "longitude": resolution.longitude,
                "retrieved_at": resolution.retrieved_at.isoformat(),
            },
            ttl_key=ORIGIN_CACHE_TOOL,
        )
        return resolution, [
            "Origin uses Open-Meteo's top usable match; verify the displayed place if the input was ambiguous."
        ]
