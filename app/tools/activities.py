"""Requested activity evidence from OSM counts and Wikivoyage context."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.http_client import JsonHttpClient
from app.tools.mediawiki_client import WIKIVOYAGE_API, MediaWikiClient
from app.tools.overpass_client import OverpassClient, build_counted_query
from app.tools.overpass_client import parse_counts as parse_counted_sets
from app.tools.wikivoyage_sections import (
    CONTEXT_CONTRACT_VERSION,
    WikivoyageSection,
    WikivoyageSectionClient,
    WikivoyageSectionNotFound,
)

MAX_CATEGORIES = 5
SUBLOOKUP_TIMEOUT_SECONDS = 40
# The data, not the manual. Citing the Overpass API documentation page as
# the source of a count is not a citation a reader can check (D44).
OVERPASS_SOURCE_URL = "https://www.openstreetmap.org/"
WIKIVOYAGE_SECTION_GROUPS = {"see": ("See",), "do": ("Do",)}

CATEGORY_RADII_M = {
    "culture": 5_000,
    "nightlife": 5_000,
    "parks": 5_000,
    "beaches": 10_000,
    "hiking": 20_000,
}

CATEGORY_ALIASES = {
    "culture": "culture",
    "cultural": "culture",
    "museum": "culture",
    "museums": "culture",
    "historical": "culture",
    "historic": "culture",
    "nightlife": "nightlife",
    "nightclub": "nightlife",
    "nightclubs": "nightlife",
    "party": "nightlife",
    "parties": "nightlife",
    "partying": "nightlife",
    "party destination": "nightlife",
    "party destinations": "nightlife",
    "big party destinations": "nightlife",
    "clubbing": "nightlife",
    "bar": "nightlife",
    "bars": "nightlife",
    "park": "parks",
    "parks": "parks",
    "garden": "parks",
    "gardens": "parks",
    "beach": "beaches",
    "beaches": "beaches",
    "hike": "hiking",
    "hikes": "hiking",
    "hiking": "hiking",
    "trail": "hiking",
    "trails": "hiking",
}

CATEGORY_SELECTORS = {
    "culture": (
        'nwr["tourism"~"^(museum|gallery)$"]',
        'nwr["amenity"="arts_centre"]',
        'nwr["historic"]',
    ),
    "nightlife": ('nwr["amenity"~"^(nightclub|bar|pub)$"]',),
    "parks": ('nwr["leisure"~"^(park|garden)$"]',),
    "beaches": ('nwr["natural"="beach"]',),
    "hiking": (
        'relation["route"="hiking"]',
        'nwr["information"="trailhead"]',
        'nwr["natural"="peak"]',
    ),
}


def avoided_activity_categories(profile: PlaceRequestProfile) -> list[str]:
    """Categories the request wants *less* of, read off deal_breakers.

    A deal-breaker used to be collected and then never queried, because only
    positively requested categories reached Overpass. P04's deal-breaker was
    "big party destinations"; nightlife was never counted anywhere, so Barcelona
    took rank 1 with "lively central areas suit evening walking" offered as a
    reason to go (D35). What the user wants avoided has to be measured before it
    can count against a place.
    """
    avoided: list[str] = []
    for phrase in profile.deal_breakers:
        normalized = " ".join(phrase.strip().casefold().replace("_", " ").split())
        category = CATEGORY_ALIASES.get(normalized)
        if category is None:
            category = next(
                (
                    mapped
                    for alias, mapped in CATEGORY_ALIASES.items()
                    if len(alias) > 3 and alias in normalized
                ),
                None,
            )
        if category is not None and category not in avoided:
            avoided.append(category)
    return avoided


def select_activity_categories(profile: PlaceRequestProfile) -> tuple[list[str], list[str], bool]:
    supported: list[str] = []
    unsupported: list[str] = []
    for preference in profile.activity_preferences:
        normalized = " ".join(preference.strip().casefold().replace("_", " ").split())
        category = CATEGORY_ALIASES.get(normalized)
        if category is None:
            if normalized and normalized not in unsupported:
                unsupported.append(normalized)
        elif category not in supported:
            supported.append(category)

    # Counted too, so an avoidance can be evidenced rather than only asserted.
    for category in avoided_activity_categories(profile):
        if category not in supported:
            supported.append(category)

    used_fallback = False
    if not supported and not unsupported and profile.purpose in {"vacation", "mixed"}:
        supported = ["culture", "parks"]
        used_fallback = True
    return supported[:MAX_CATEGORIES], unsupported[:MAX_CATEGORIES], used_fallback


def build_query(categories: list[str], lat: float, lon: float) -> str:
    """One counted set per category -- see overpass_client.build_counted_query."""
    return build_counted_query(
        [
            [
                f"{selector}(around:{CATEGORY_RADII_M[category]},{lat},{lon});"
                for selector in CATEGORY_SELECTORS[category]
            ]
            for category in categories
        ]
    )


def _matching_categories(tags: dict[str, Any]) -> set[str]:
    matched: set[str] = set()
    if tags.get("tourism") in {"museum", "gallery"} or tags.get("amenity") == "arts_centre" or "historic" in tags:
        matched.add("culture")
    if tags.get("amenity") in {"nightclub", "bar", "pub"}:
        matched.add("nightlife")
    if tags.get("leisure") in {"park", "garden"}:
        matched.add("parks")
    if tags.get("natural") == "beach":
        matched.add("beaches")
    if (
        tags.get("route") == "hiking"
        or tags.get("information") == "trailhead"
        or tags.get("natural") == "peak"
    ):
        matched.add("hiking")
    return matched


def parse_counts(data: dict, categories: list[str]) -> tuple[dict[str, int], int, int]:
    """Map the per-set counts back onto their categories, in order.

    `invalid_elements` stays in the signature for the caller's warning and
    confidence logic but is now always 0: counting happens server-side, so no
    per-element payload survives to be malformed.
    """
    counts_list = parse_counted_sets(data, len(categories))
    counts = dict(zip(categories, counts_list, strict=True))
    return counts, 0, sum(counts_list)


def _mark_stale(result: ToolResult) -> ToolResult:
    result.stale = True
    warning = "Using stale cached activity evidence; live lookups failed."
    result.warnings.append(warning)
    for item in result.evidence_items:
        item.source.stale = True
        item.warnings.append(warning)
    return result


def _exception_text(exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class ActivitiesTool:
    name = "ActivitiesTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 15.0,
        *,
        overpass: OverpassClient | None = None,
        wikivoyage: WikivoyageSectionClient | None = None,
        sublookup_timeout: float = SUBLOOKUP_TIMEOUT_SECONDS,
    ) -> None:
        self._cache = cache
        http = JsonHttpClient(timeout=timeout)
        self._overpass = overpass or OverpassClient(http)
        self._wikivoyage = wikivoyage or WikivoyageSectionClient(MediaWikiClient(WIKIVOYAGE_API, http))
        self._sublookup_timeout = sublookup_timeout

    async def _osm(self, categories: list[str], candidate: CandidatePlace) -> dict:
        return await self._overpass.query(build_query(categories, candidate.lat, candidate.lon))

    async def _context(self, title: str) -> dict[str, WikivoyageSection | BaseException]:
        return await self._wikivoyage.fetch_many(title, WIKIVOYAGE_SECTION_GROUPS)

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        now = datetime.now(UTC)
        title = candidate.canonical_name or candidate.place_name
        categories, unsupported, used_fallback = select_activity_categories(profile)
        params = {
            "lat": candidate.lat,
            "lon": candidate.lon,
            "title": title,
            "categories": categories,
            "unsupported_categories": unsupported,
            "radius_by_category_m": {category: CATEGORY_RADII_M[category] for category in categories},
            "wikivoyage_sections": list(WIKIVOYAGE_SECTION_GROUPS),
            "wikivoyage_context_contract_version": CONTEXT_CONTRACT_VERSION,
        }
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        task_names = ["wikivoyage"]
        tasks = [asyncio.wait_for(self._context(title), timeout=self._sublookup_timeout)]
        if categories and candidate.lat is not None and candidate.lon is not None:
            task_names.append("osm")
            tasks.append(
                asyncio.wait_for(self._osm(categories, candidate), timeout=self._sublookup_timeout)
            )
        outcomes = dict(zip(task_names, await asyncio.gather(*tasks, return_exceptions=True), strict=True))

        warnings = [
            "OSM counts reflect mapped places and routes, not live events, opening status, "
            "trail conditions, or quality.",
            "The final activity score is unresolved until the reasoning agent assesses counts and context together.",
        ]
        if used_fallback:
            warnings.append("No activities were requested; using culture and parks as a generic vacation fallback.")
        if unsupported:
            warnings.append(f"Unsupported requested activities remain unresolved: {', '.join(unsupported)}.")

        counts: dict[str, int] | None = None
        valid_element_count: int | None = None
        osm_partial = False
        if not categories:
            osm_status = "not_requested"
        elif candidate.lat is None or candidate.lon is None:
            osm_status = "missing_coordinates"
        else:
            osm_status = "error"
        osm_warnings = ["A complete zero count means no matching mapped OSM elements were returned."]
        osm_outcome = outcomes.get("osm")
        if isinstance(osm_outcome, dict):
            try:
                counts, invalid_elements, valid_element_count = parse_counts(osm_outcome, categories)
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
            osm_warnings.append(f"OSM activity counts are unavailable: {_exception_text(osm_outcome)}")
        elif osm_status == "missing_coordinates":
            osm_warnings.append("OSM activity counts require verified destination coordinates.")

        sections: dict[str, WikivoyageSection | None] = {"see": None, "do": None}
        section_status: dict[str, str] = {"see": "error", "do": "error"}
        section_warnings: dict[str, list[str]] = {
            key: ["Wikivoyage is community-maintained context and is not scored inside this tool."]
            for key in sections
        }
        context_outcome = outcomes["wikivoyage"]
        if isinstance(context_outcome, dict):
            for key in sections:
                outcome = context_outcome.get(key)
                if isinstance(outcome, WikivoyageSection):
                    sections[key] = outcome
                    section_status[key] = "available"
                elif isinstance(outcome, WikivoyageSectionNotFound):
                    section_status[key] = "missing"
                    section_warnings[key].append(str(outcome))
                else:
                    section_warnings[key].append(
                        f"Wikivoyage {key.title()} context is unavailable: "
                        f"{_exception_text(outcome) if isinstance(outcome, BaseException) else outcome}"
                    )
        else:
            context_error = (
                _exception_text(context_outcome)
                if isinstance(context_outcome, BaseException)
                else str(context_outcome)
            )
            for key in sections:
                section_warnings[key].append(
                    f"Wikivoyage article or section lookup is unavailable: {context_error}"
                )

        evidence_items: list[EvidenceItem] = []
        if counts is not None:
            evidence_items.append(
                EvidenceItem(
                    criterion="activities",
                    component="osm_activity_counts",
                    normalized_data={
                        "counts_by_category": counts,
                        "radius_by_category_m": {
                            category: CATEGORY_RADII_M[category] for category in categories
                        },
                        "valid_element_count": valid_element_count,
                        "partial": osm_partial,
                    },
                    source=EvidenceSource(
                        source_name="OpenStreetMap category-specific activity evidence",
                        source_url=OVERPASS_SOURCE_URL,
                        retrieved_at=now,
                        confidence="low" if osm_partial else "medium",
                    ),
                    warnings=osm_warnings,
                )
            )
        for key, section in sections.items():
            if section is None:
                continue
            evidence_items.append(
                EvidenceItem(
                    criterion="activities",
                    component=f"wikivoyage_{key}_context",
                    normalized_data=section.normalized_data(),
                    source=EvidenceSource(
                        source_name=f"Wikivoyage {section.section_title} section",
                        source_url=section.source_url,
                        retrieved_at=now,
                        data_date=(
                            f"Wikivoyage revision {section.revision_id} "
                            f"({section.revision_timestamp})"
                        ),
                        confidence="medium",
                    ),
                    warnings=section_warnings[key],
                )
            )

        no_evidence = not evidence_items
        if no_evidence and cached is not None:
            return _mark_stale(ToolResult.model_validate(cached))

        partial = (
            bool(unsupported)
            or (bool(categories) and osm_status != "available")
            or any(status != "available" for status in section_status.values())
        )
        normalized_data = {
            "requested_categories": categories,
            "unsupported_categories": unsupported,
            "used_generic_vacation_fallback": used_fallback,
            "counts_by_category": counts if counts is not None else {},
            "category_status": {
                category: osm_status for category in categories
            }
            | {category: "unsupported" for category in unsupported},
            "radius_by_category_m": {category: CATEGORY_RADII_M[category] for category in categories},
            "valid_element_count": valid_element_count,
            "osm_status": osm_status,
            "wikivoyage_see_context": sections["see"].normalized_data() if sections["see"] else None,
            "wikivoyage_do_context": sections["do"].normalized_data() if sections["do"] else None,
            "wikivoyage_section_status": section_status,
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
            warnings=warnings + osm_warnings + [warning for values in section_warnings.values() for warning in values],
            error="No activity evidence could be retrieved." if no_evidence else None,
            evidence_items=evidence_items,
        )
        cacheable = (
            (not categories or osm_status == "available")
            and all(status in {"available", "missing"} for status in section_status.values())
        )
        if cacheable:
            await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
