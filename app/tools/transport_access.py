"""Destination arrival evidence from OSM, origin distance, and Wikivoyage."""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from typing import Any

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.http_client import JsonHttpClient
from app.tools.mediawiki_client import WIKIVOYAGE_API, MediaWikiClient
from app.tools.origin_resolution import GEOCODING_SOURCE_URL, OriginResolution, OriginResolver
from app.tools.overpass_client import OverpassClient
from app.tools.wikivoyage_sections import (
    CONTEXT_CONTRACT_VERSION,
    WikivoyageSection,
    WikivoyageSectionClient,
    WikivoyageSectionNotFound,
)

AIRPORT_RADIUS_M = 50_000
TERMINAL_RADIUS_M = 10_000
COMPONENTS = (
    "airports_within_50km",
    "potential_mainline_rail_stations_within_10km",
    "bus_terminals_within_10km",
    "ferry_terminals_within_10km",
)
GET_IN_SECTION_NAMES = ("Get in", "Getting in")
# The data, not the manual. Citing the Overpass API documentation page as
# the source of a count is not a citation a reader can check (D44).
OVERPASS_SOURCE_URL = "https://www.openstreetmap.org/"
SUBLOOKUP_TIMEOUT_SECONDS = 40


def build_query(lat: float, lon: float) -> str:
    return (
        "[out:json][timeout:15];("
        f'nwr["aeroway"="aerodrome"](around:{AIRPORT_RADIUS_M},{lat},{lon});'
        f'nwr["railway"="station"](around:{TERMINAL_RADIUS_M},{lat},{lon});'
        f'nwr["amenity"="bus_station"](around:{TERMINAL_RADIUS_M},{lat},{lon});'
        f'nwr["amenity"="ferry_terminal"](around:{TERMINAL_RADIUS_M},{lat},{lon});'
        ");out tags;"
    )


def _matching_components(tags: dict[str, Any]) -> set[str]:
    matched: set[str] = set()
    if tags.get("aeroway") == "aerodrome":
        matched.add("airports_within_50km")
    if (
        tags.get("railway") == "station"
        and tags.get("station") != "subway"
        and tags.get("subway") != "yes"
    ):
        matched.add("potential_mainline_rail_stations_within_10km")
    if tags.get("amenity") == "bus_station":
        matched.add("bus_terminals_within_10km")
    if tags.get("amenity") == "ferry_terminal":
        matched.add("ferry_terminals_within_10km")
    return matched


def parse_counts(data: dict) -> tuple[dict[str, int], int, int]:
    elements = data.get("elements")
    if not isinstance(elements, list):
        raise ValueError("Overpass returned no usable elements list.")

    seen_by_component: dict[str, set[tuple[str, int]]] = {name: set() for name in COMPONENTS}
    valid_identities: set[tuple[str, int]] = set()
    invalid_elements = 0
    for element in elements:
        if not isinstance(element, dict):
            invalid_elements += 1
            continue
        element_type = element.get("type")
        element_id = element.get("id")
        tags = element.get("tags")
        if (
            element_type not in {"node", "way", "relation"}
            or not isinstance(element_id, int)
            or not isinstance(tags, dict)
        ):
            invalid_elements += 1
            continue
        identity = (element_type, element_id)
        valid_identities.add(identity)
        for component in _matching_components(tags):
            seen_by_component[component].add(identity)

    return (
        {component: len(seen_by_component[component]) for component in COMPONENTS},
        invalid_elements,
        len(valid_identities),
    )


def straight_line_distance_km(first_lat: float, first_lon: float, second_lat: float, second_lon: float) -> float:
    radius_km = 6371.0088
    lat1, lat2 = math.radians(first_lat), math.radians(second_lat)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(second_lon - first_lon)
    haversine = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return radius_km * 2 * math.asin(math.sqrt(haversine))


def _mark_stale(result: ToolResult) -> ToolResult:
    result.stale = True
    warning = "Using stale cached transport-access evidence; live lookups failed."
    result.warnings.append(warning)
    for item in result.evidence_items:
        item.source.stale = True
        item.warnings.append(warning)
    return result


class TransportAccessTool:
    name = "TransportAccessTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 15.0,
        *,
        overpass: OverpassClient | None = None,
        wikivoyage: WikivoyageSectionClient | None = None,
        origin_resolver: OriginResolver | None = None,
        sublookup_timeout: float = SUBLOOKUP_TIMEOUT_SECONDS,
    ) -> None:
        self._cache = cache
        http = JsonHttpClient(timeout=timeout)
        self._overpass = overpass or OverpassClient(http)
        self._wikivoyage = wikivoyage or WikivoyageSectionClient(MediaWikiClient(WIKIVOYAGE_API, http))
        self._origin_resolver = origin_resolver or OriginResolver(cache, http=http)
        self._sublookup_timeout = sublookup_timeout

    async def _osm(self, candidate: CandidatePlace) -> dict:
        return await self._overpass.query(build_query(candidate.lat, candidate.lon))

    async def _context(self, title: str) -> WikivoyageSection:
        return await self._wikivoyage.fetch(title, GET_IN_SECTION_NAMES)

    async def _origin(self, origin: str, now: datetime) -> tuple[OriginResolution | None, list[str]]:
        return await self._origin_resolver.resolve(origin, now, require_coordinates=True)

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        now = datetime.now(UTC)
        title = candidate.canonical_name or candidate.place_name
        origin_input = (profile.origin or "").strip()
        params = {
            "lat": candidate.lat,
            "lon": candidate.lon,
            "title": title,
            "origin": origin_input.casefold(),
            "airport_radius_m": AIRPORT_RADIUS_M,
            "terminal_radius_m": TERMINAL_RADIUS_M,
            "wikivoyage_section": "Get in",
            "wikivoyage_context_contract_version": CONTEXT_CONTRACT_VERSION,
        }
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        task_names: list[str] = ["wikivoyage"]
        tasks = [asyncio.wait_for(self._context(title), timeout=self._sublookup_timeout)]
        if candidate.lat is not None and candidate.lon is not None:
            task_names.append("osm")
            tasks.append(asyncio.wait_for(self._osm(candidate), timeout=self._sublookup_timeout))
            if origin_input:
                task_names.append("origin")
                tasks.append(asyncio.wait_for(self._origin(origin_input, now), timeout=self._sublookup_timeout))
        outcomes = dict(zip(task_names, await asyncio.gather(*tasks, return_exceptions=True), strict=True))

        warnings = [
            "Infrastructure counts do not establish live routes, services, schedules, frequency, "
            "prices, or travel times.",
            "The final arrival-access score is unresolved until the reasoning agent assesses all sources together.",
        ]
        counts: dict[str, int] | None = None
        valid_element_count: int | None = None
        osm_status = "missing_coordinates" if "osm" not in outcomes else "error"
        osm_partial = False
        osm_warnings = ["OSM counts reflect mapped infrastructure and may be incomplete."]
        osm_outcome = outcomes.get("osm")
        if isinstance(osm_outcome, dict):
            try:
                counts, invalid_elements, valid_element_count = parse_counts(osm_outcome)
                remark = osm_outcome.get("remark")
                osm_partial = bool(remark) or invalid_elements > 0
                osm_status = "partial" if osm_partial else "available"
                if remark:
                    osm_warnings.append(f"Overpass reported a partial response: {str(remark)[:200]}")
                if invalid_elements:
                    osm_warnings.append(f"Ignored {invalid_elements} malformed Overpass element(s).")
            except Exception as exc:  # noqa: BLE001 - malformed provider data is missing evidence
                osm_outcome = exc
        if isinstance(osm_outcome, BaseException):
            osm_warnings.append(f"OSM arrival-infrastructure counts are unavailable: {osm_outcome}")
        elif osm_status == "missing_coordinates":
            osm_warnings.append("OSM counts require verified destination coordinates.")

        context: WikivoyageSection | None = None
        context_status = "error"
        context_warnings = ["Wikivoyage is community-maintained context and is not scored inside this tool."]
        context_outcome = outcomes["wikivoyage"]
        if isinstance(context_outcome, WikivoyageSection):
            context = context_outcome
            context_status = "available"
        elif isinstance(context_outcome, WikivoyageSectionNotFound):
            context_status = "missing"
            context_warnings.append(str(context_outcome))
        else:
            context_warnings.append(f"Wikivoyage arrival context is unavailable: {context_outcome}")

        origin: OriginResolution | None = None
        origin_status = "not_requested" if not origin_input else "missing_destination_coordinates"
        origin_warnings: list[str] = []
        origin_outcome = outcomes.get("origin")
        if isinstance(origin_outcome, tuple):
            origin, origin_warnings = origin_outcome
            origin_status = "available" if origin is not None else "error"
        elif isinstance(origin_outcome, BaseException):
            origin_status = "error"
            origin_warnings.append(f"Origin resolution is unavailable: {origin_outcome}")

        distance_km: float | None = None
        if (
            origin is not None
            and origin.latitude is not None
            and origin.longitude is not None
            and candidate.lat is not None
            and candidate.lon is not None
        ):
            distance_km = round(
                straight_line_distance_km(origin.latitude, origin.longitude, candidate.lat, candidate.lon), 1
            )
            origin_warnings.append("Distance is straight-line Haversine distance, not route distance or travel time.")

        evidence_items: list[EvidenceItem] = []
        if counts is not None:
            evidence_items.append(
                EvidenceItem(
                    criterion="accessibility",
                    component="osm_arrival_infrastructure_counts",
                    normalized_data={
                        "counts_by_component": counts,
                        "airport_radius_m": AIRPORT_RADIUS_M,
                        "terminal_radius_m": TERMINAL_RADIUS_M,
                        "valid_element_count": valid_element_count,
                        "partial": osm_partial,
                    },
                    source=EvidenceSource(
                        source_name="OpenStreetMap destination arrival infrastructure",
                        source_url=OVERPASS_SOURCE_URL,
                        retrieved_at=now,
                        confidence="low" if osm_partial else "medium",
                    ),
                    warnings=osm_warnings,
                )
            )
        if origin is not None and distance_km is not None:
            evidence_items.append(
                EvidenceItem(
                    criterion="accessibility",
                    component="origin_resolution_and_straight_line_distance",
                    normalized_data={
                        "origin_input": origin_input,
                        "resolved_origin_name": origin.name,
                        "resolved_origin_country": origin.country,
                        "resolved_origin_country_code": origin.country_code,
                        "origin_latitude": origin.latitude,
                        "origin_longitude": origin.longitude,
                        "origin_resolution_method": origin.method,
                        "origin_resolution_was_cached": origin.cached,
                        "straight_line_distance_km": distance_km,
                    },
                    source=EvidenceSource(
                        source_name="Open-Meteo origin resolution and DigitalNomadAgent Haversine calculation",
                        source_url=GEOCODING_SOURCE_URL,
                        retrieved_at=origin.retrieved_at,
                        confidence="medium",
                        stale=origin.stale,
                    ),
                    warnings=origin_warnings,
                )
            )
        if context is not None:
            evidence_items.append(
                EvidenceItem(
                    criterion="accessibility",
                    component="wikivoyage_get_in_context",
                    normalized_data=context.normalized_data(),
                    source=EvidenceSource(
                        source_name="Wikivoyage Get in section",
                        source_url=context.source_url,
                        retrieved_at=now,
                        data_date=f"Wikivoyage revision {context.revision_id} ({context.revision_timestamp})",
                        confidence="medium",
                    ),
                    warnings=context_warnings,
                )
            )

        no_evidence = not evidence_items
        if no_evidence and cached is not None:
            return _mark_stale(ToolResult.model_validate(cached))

        partial = (
            osm_status != "available"
            or context_status == "error"
            or (origin_input and origin_status != "available")
            or bool(origin and origin.stale)
        )
        normalized_data = {
            "counts_by_component": counts or {},
            "airport_radius_m": AIRPORT_RADIUS_M,
            "terminal_radius_m": TERMINAL_RADIUS_M,
            "valid_element_count": valid_element_count,
            "osm_status": osm_status,
            "origin_input": origin_input or None,
            "origin_status": origin_status,
            "resolved_origin_name": origin.name if origin else None,
            "resolved_origin_country": origin.country if origin else None,
            "resolved_origin_country_code": origin.country_code if origin else None,
            "origin_latitude": origin.latitude if origin else None,
            "origin_longitude": origin.longitude if origin else None,
            "origin_resolution_method": origin.method if origin else None,
            "origin_resolution_was_cached": origin.cached if origin else False,
            "straight_line_distance_km": distance_km,
            "wikivoyage_context": context.normalized_data() if context else None,
            "wikivoyage_status": context_status,
            "scoring_status": "unresolved_pending_llm",
            "partial": partial,
        }
        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data=normalized_data,
            source_name="OpenStreetMap, Open-Meteo, and Wikivoyage",
            retrieved_at=now,
            confidence="low" if partial else "medium",
            stale=all(item.source.stale for item in evidence_items) if evidence_items else False,
            warnings=warnings + osm_warnings + origin_warnings + context_warnings,
            error="No destination arrival evidence could be retrieved." if no_evidence else None,
            evidence_items=evidence_items,
        )
        cacheable = (
            osm_status == "available"
            and context_status in {"available", "missing"}
            and (not origin_input or origin_status == "available")
            and not (origin and origin.stale)
        )
        if cacheable:
            await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
