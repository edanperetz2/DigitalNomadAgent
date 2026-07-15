"""Local car-free mobility evidence from OSM counts and Wikivoyage context."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.http_client import JsonHttpClient
from app.tools.mediawiki_client import WIKIVOYAGE_API, MediaWikiClient
from app.tools.overpass_client import OverpassClient
from app.tools.wikivoyage_sections import (
    CONTEXT_CONTRACT_VERSION,
    WikivoyageSection,
    WikivoyageSectionClient,
    WikivoyageSectionNotFound,
)

RADIUS_M = 3000
OVERPASS_SOURCE_NAME = "OpenStreetMap local mobility infrastructure"
OVERPASS_SOURCE_URL = "https://wiki.openstreetmap.org/wiki/Overpass_API"
WIKIVOYAGE_SOURCE_NAME = "Wikivoyage Get around section"
GET_AROUND_SECTION_NAMES = ("Get around", "Getting around")
COMPONENTS = ("bus_stops", "rail_metro_tram_stations", "pedestrian_ways", "cycleways")


def build_query(lat: float, lon: float) -> str:
    """Build one bounded query from a fixed allow-list of mobility selectors."""
    return (
        "[out:json][timeout:15];("
        f'nwr["highway"="bus_stop"](around:{RADIUS_M},{lat},{lon});'
        f'nwr["public_transport"="platform"]["bus"="yes"](around:{RADIUS_M},{lat},{lon});'
        f'nwr["railway"~"^(station|halt|tram_stop)$"](around:{RADIUS_M},{lat},{lon});'
        f'nwr["station"="subway"](around:{RADIUS_M},{lat},{lon});'
        f'way["highway"~"^(pedestrian|footway|living_street)$"](around:{RADIUS_M},{lat},{lon});'
        f'way["highway"="cycleway"](around:{RADIUS_M},{lat},{lon});'
        f'way["cycleway"](around:{RADIUS_M},{lat},{lon});'
        f'way["cycleway:left"](around:{RADIUS_M},{lat},{lon});'
        f'way["cycleway:right"](around:{RADIUS_M},{lat},{lon});'
        f'way["cycleway:both"](around:{RADIUS_M},{lat},{lon});'
        ");out tags;"
    )


def _has_cycleway(tags: dict) -> bool:
    if tags.get("highway") == "cycleway":
        return True
    absent_values = {"", "no", "none", "separate"}
    return any(
        key == "cycleway" or key.startswith("cycleway:")
        for key, value in tags.items()
        if str(value).casefold() not in absent_values
    )


def _matching_components(element_type: str, tags: dict) -> set[str]:
    matched: set[str] = set()
    if tags.get("highway") == "bus_stop" or (
        tags.get("public_transport") == "platform" and tags.get("bus") == "yes"
    ):
        matched.add("bus_stops")
    if tags.get("railway") in {"station", "halt", "tram_stop"} or tags.get("station") == "subway":
        matched.add("rail_metro_tram_stations")
    if element_type == "way" and tags.get("highway") in {"pedestrian", "footway", "living_street"}:
        matched.add("pedestrian_ways")
    if element_type == "way" and _has_cycleway(tags):
        matched.add("cycleways")
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
        for component in _matching_components(element_type, tags):
            seen_by_component[component].add(identity)

    return (
        {component: len(seen_by_component[component]) for component in COMPONENTS},
        invalid_elements,
        len(valid_identities),
    )


def _mark_stale(result: ToolResult) -> ToolResult:
    result.stale = True
    warning = "Using stale cached local mobility evidence; live lookups failed."
    result.warnings.append(warning)
    for item in result.evidence_items:
        item.source.stale = True
        item.warnings.append(warning)
    return result


class LocalMobilityTool:
    name = "LocalMobilityTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 15.0,
        *,
        overpass: OverpassClient | None = None,
        wikivoyage: WikivoyageSectionClient | None = None,
    ) -> None:
        self._cache = cache
        http = JsonHttpClient(timeout=timeout)
        self._overpass = overpass or OverpassClient(http)
        self._wikivoyage = wikivoyage or WikivoyageSectionClient(
            MediaWikiClient(WIKIVOYAGE_API, http)
        )

    async def _osm(self, candidate: CandidatePlace) -> dict:
        return await self._overpass.query(build_query(candidate.lat, candidate.lon))

    async def _context(self, title: str) -> WikivoyageSection:
        return await self._wikivoyage.fetch(
            title,
            GET_AROUND_SECTION_NAMES,
        )

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        del profile  # Mobility evidence is selected from the profile but uses a fixed local scope.
        now = datetime.now(UTC)
        title = candidate.canonical_name or candidate.place_name
        params = {
            "lat": candidate.lat,
            "lon": candidate.lon,
            "title": title,
            "radius_m": RADIUS_M,
            "wikivoyage_section": "Get around",
            "wikivoyage_context_contract_version": CONTEXT_CONTRACT_VERSION,
        }
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        task_names: list[str] = []
        tasks = []
        if candidate.lat is not None and candidate.lon is not None:
            task_names.append("osm")
            tasks.append(self._osm(candidate))
        task_names.append("wikivoyage")
        tasks.append(self._context(title))
        values = await asyncio.gather(*tasks, return_exceptions=True)
        outcomes = dict(zip(task_names, values, strict=True))

        counts: dict[str, int] | None = None
        valid_element_count: int | None = None
        osm_status = "missing_coordinates" if "osm" not in outcomes else "error"
        osm_warnings = [
            "OSM element counts reflect mapped infrastructure, not routes, service frequency, or journey quality."
        ]
        osm_outcome = outcomes.get("osm")
        osm_partial = False
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
            osm_warnings.append(f"OSM mobility counts are unavailable: {osm_outcome}")
        elif osm_status == "missing_coordinates":
            osm_warnings.append("OSM mobility counts require verified coordinates.")

        context: WikivoyageSection | None = None
        context_status = "error"
        context_warnings = [
            "Wikivoyage is community-maintained context and has not been converted into a score."
        ]
        context_outcome = outcomes["wikivoyage"]
        if isinstance(context_outcome, WikivoyageSection):
            context = context_outcome
            context_status = "available"
        elif isinstance(context_outcome, WikivoyageSectionNotFound):
            context_status = "missing"
            context_warnings.append(str(context_outcome))
        else:
            context_warnings.append(f"Wikivoyage mobility context is unavailable: {context_outcome}")

        if counts is None and context is None:
            if cached is not None:
                return _mark_stale(ToolResult.model_validate(cached))
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                normalized_data={
                    "counts_by_component": {},
                    "wikivoyage_context": None,
                    "radius_m": RADIUS_M,
                    "osm_status": osm_status,
                    "wikivoyage_status": context_status,
                    "scoring_status": "unresolved_pending_llm",
                    "partial": True,
                },
                source_name="OpenStreetMap and Wikivoyage",
                retrieved_at=now,
                confidence="low",
                warnings=osm_warnings + context_warnings,
                error="No local mobility evidence could be retrieved.",
            )

        evidence_items: list[EvidenceItem] = []
        if counts is not None:
            evidence_items.append(
                EvidenceItem(
                    criterion="transportation",
                    component="osm_local_mobility_counts",
                    normalized_data={
                        "counts_by_component": counts,
                        "radius_m": RADIUS_M,
                        "valid_element_count": valid_element_count,
                        "partial": osm_partial,
                    },
                    source=EvidenceSource(
                        source_name=OVERPASS_SOURCE_NAME,
                        source_url=OVERPASS_SOURCE_URL,
                        retrieved_at=now,
                        confidence="low" if osm_partial else "medium",
                    ),
                    warnings=osm_warnings,
                )
            )
        if context is not None:
            evidence_items.append(
                EvidenceItem(
                    criterion="transportation",
                    component="wikivoyage_get_around_context",
                    normalized_data=context.normalized_data(),
                    source=EvidenceSource(
                        source_name=WIKIVOYAGE_SOURCE_NAME,
                        source_url=context.source_url,
                        retrieved_at=now,
                        data_date=(
                            f"Wikivoyage revision {context.revision_id} "
                            f"({context.revision_timestamp})"
                        ),
                        confidence="medium",
                    ),
                    warnings=context_warnings,
                )
            )

        partial = osm_status in {"error", "partial", "missing_coordinates"} or context_status == "error"
        normalized_data = {
            "counts_by_component": counts or {},
            "valid_element_count": valid_element_count,
            "radius_m": RADIUS_M,
            "osm_status": osm_status,
            "wikivoyage_context": context.normalized_data() if context else None,
            "wikivoyage_status": context_status,
            "scoring_status": "unresolved_pending_llm",
            "partial": partial,
        }
        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data=normalized_data,
            source_name="OpenStreetMap and Wikivoyage",
            retrieved_at=now,
            confidence="low" if partial or counts is None else "medium",
            warnings=osm_warnings + context_warnings,
            evidence_items=evidence_items,
        )
        cacheable = osm_status == "available" and context_status in {"available", "missing"}
        if cacheable:
            await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
