"""Terrain steepness around a city centre, from sampled elevations.

Nothing measured terrain, so P06's "step-free access around the city centre ...
and reasonably flat terrain are non-negotiable" was recorded as a hard
constraint and never checked. Lisbon -- steep and cobbled -- was recommended
first to a wheelchair user, and the evidence offered *in its favour* was the
city's funiculars and public elevator, infrastructure that exists precisely
because the place is vertical (D34).

Deliberately a crude physical measurement rather than an accessibility verdict:
a ring of elevation samples around the centre, reported as the spread between
them. Flat terrain does not make a city step-free, and this never claims it
does -- it rules out the cities where the question is already settled.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.http_client import JsonHttpClient

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
SOURCE_NAME = "Open-Meteo elevation API"
SOURCE_URL = "https://open-meteo.com/en/docs/elevation-api"

# A ring at this radius plus the centre. Big enough to cross the hills a city
# centre sits on, small enough to stay inside the walkable core.
SAMPLE_RADIUS_KM = 2.0
SAMPLE_BEARINGS = (0, 45, 90, 135, 180, 225, 270, 315)
EARTH_RADIUS_KM = 6371.0

# Metres of spread across the ring. Calibrated against the P06 candidate set:
# Adelaide and Bristol sit in the flat band, Lisbon and Porto in the steep one.
FLAT_SPREAD_M = 25.0
STEEP_SPREAD_M = 90.0


def _offset(lat: float, lon: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    bearing = math.radians(bearing_deg)
    angular = distance_km / EARTH_RADIUS_KM
    lat1, lon1 = math.radians(lat), math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular) + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return round(math.degrees(lat2), 5), round(math.degrees(lon2), 5)


def flatness_score(spread_m: float) -> float:
    """1.0 flat, 0.0 steep, linear between the two calibration points."""
    if spread_m <= FLAT_SPREAD_M:
        return 1.0
    if spread_m >= STEEP_SPREAD_M:
        return 0.0
    return round(1.0 - (spread_m - FLAT_SPREAD_M) / (STEEP_SPREAD_M - FLAT_SPREAD_M), 4)


def terrain_label(spread_m: float) -> str:
    if spread_m <= FLAT_SPREAD_M:
        return "flat"
    if spread_m >= STEEP_SPREAD_M:
        return "steep"
    return "rolling"


class TerrainTool:
    name = "TerrainTool"

    def __init__(self, cache: ToolCache, timeout: float = 10.0, http: JsonHttpClient | None = None):
        self._cache = cache
        self._http = http or JsonHttpClient(timeout=timeout)

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

    async def _fetch(self, latitudes: list[float], longitudes: list[float]) -> dict[str, Any]:
        payload = await self._http.get_json(
            ELEVATION_URL,
            params={
                "latitude": ",".join(str(value) for value in latitudes),
                "longitude": ",".join(str(value) for value in longitudes),
            },
        )
        if not isinstance(payload, dict):
            raise ValueError("Open-Meteo elevation returned a non-object JSON response")
        return payload

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        now = datetime.now(UTC)
        if candidate.lat is None or candidate.lon is None:
            return self._error(candidate.place_name, "Cannot sample terrain without verified coordinates.")

        points = [(candidate.lat, candidate.lon)] + [
            _offset(candidate.lat, candidate.lon, bearing, SAMPLE_RADIUS_KM)
            for bearing in SAMPLE_BEARINGS
        ]
        params = {"points": points, "radius_km": SAMPLE_RADIUS_KM}
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        try:
            payload = await self._fetch([p[0] for p in points], [p[1] for p in points])
            raw = payload.get("elevation")
            elevations = [
                float(value)
                for value in (raw if isinstance(raw, list) else [])
                if isinstance(value, int | float) and not isinstance(value, bool)
            ]
        except Exception as exc:  # noqa: BLE001 - a stale sample beats losing the criterion
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached elevation samples; live lookup failed.")
                return stale_result
            return self._error(candidate.place_name, f"Elevation lookup failed: {exc}")

        if len(elevations) < len(points):
            return self._error(
                candidate.place_name,
                f"Elevation API returned {len(elevations)} of {len(points)} sampled points.",
            )

        spread = round(max(elevations) - min(elevations), 1)
        centre = elevations[0]
        normalized_data = {
            "sample_count": len(elevations),
            "sample_radius_km": SAMPLE_RADIUS_KM,
            "centre_elevation_m": round(centre, 1),
            "min_elevation_m": round(min(elevations), 1),
            "max_elevation_m": round(max(elevations), 1),
            "elevation_spread_m": spread,
            "terrain": terrain_label(spread),
            "flatness_score": flatness_score(spread),
        }
        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data=normalized_data,
            source_name=SOURCE_NAME,
            source_url=SOURCE_URL,
            retrieved_at=now,
            confidence="medium",
            warnings=[
                "Elevation spread across a 2 km ring is a measure of gradient, not of step-free "
                "routes, kerb cuts, or accessible transport."
            ],
            evidence_items=[
                EvidenceItem(
                    criterion="terrain",
                    component="elevation_spread",
                    value=normalized_data["flatness_score"],
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
        await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
