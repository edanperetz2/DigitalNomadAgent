"""Prompt-driven nearby infrastructure evidence from OpenStreetMap Overpass."""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult
from app.tools.http_client import JsonHttpClient
from app.tools.overpass_client import OverpassClient

RADIUS_M = 3000
OVERPASS_SOURCE_URL = "https://wiki.openstreetmap.org/wiki/Overpass_API"
CATEGORY_REGISTRY_SOURCE = "PlaceMatch amenity category registry"

# User text never reaches query construction directly; only these bounded OSM
# selectors can be interpolated into an Overpass request.
CATEGORY_TAGS: dict[str, tuple[tuple[str, str], ...]] = {
    "coworking": (("office", "coworking"), ("amenity", "coworking_space")),
    "cafe": (("amenity", "cafe"),),
    "university": (("amenity", "university"),),
    "library": (("amenity", "library"),),
    "park": (("leisure", "park"),),
    "pharmacy": (("amenity", "pharmacy"),),
    "supermarket": (("shop", "supermarket"),),
    "fitness_centre": (("leisure", "fitness_centre"),),
}

CATEGORY_ALIASES: dict[str, str] = {
    "coworking": "coworking",
    "coworking space": "coworking",
    "coworking spaces": "coworking",
    "cafe": "cafe",
    "cafes": "cafe",
    "café": "cafe",
    "cafés": "cafe",
    "coffee shop": "cafe",
    "coffee shops": "cafe",
    "university": "university",
    "universities": "university",
    "library": "library",
    "libraries": "library",
    "park": "park",
    "parks": "park",
    "green space": "park",
    "green spaces": "park",
    "pharmacy": "pharmacy",
    "pharmacies": "pharmacy",
    "supermarket": "supermarket",
    "supermarkets": "supermarket",
    "grocery store": "supermarket",
    "grocery stores": "supermarket",
    "fitness centre": "fitness_centre",
    "fitness centres": "fitness_centre",
    "fitness center": "fitness_centre",
    "fitness centers": "fitness_centre",
    "gym": "fitness_centre",
    "gyms": "fitness_centre",
}


def _normalize_category(value: str) -> str:
    return " ".join(value.strip().casefold().replace("_", " ").replace("-", " ").split())


def select_categories(profile: PlaceRequestProfile) -> tuple[list[str], list[str]]:
    categories: list[str] = []
    purposes = {profile.purpose}
    if profile.purpose == "mixed":
        purposes = set(profile.secondary_purposes) or {"remote_work", "vacation"}
    if "remote_work" in purposes:
        categories.extend(("coworking", "cafe"))
    if "study" in purposes:
        categories.extend(("university", "library"))

    unsupported: list[str] = []
    for raw_category in profile.amenity_preferences:
        normalized = _normalize_category(raw_category)
        category = CATEGORY_ALIASES.get(normalized)
        if category is None:
            if normalized and normalized not in unsupported:
                unsupported.append(normalized)
        elif category not in categories:
            categories.append(category)
    return categories, unsupported


def build_query(categories: list[str], lat: float, lon: float) -> str:
    clauses = "".join(
        f'nwr["{key}"="{value}"](around:{RADIUS_M},{lat},{lon});'
        for category in categories
        for key, value in CATEGORY_TAGS[category]
    )
    return f"[out:json][timeout:15];({clauses});out tags;"


def parse_category_counts(data: dict, categories: list[str]) -> tuple[dict[str, int], int, int]:
    elements = data.get("elements")
    if not isinstance(elements, list):
        raise ValueError("Overpass returned no usable elements list.")

    seen_by_category: dict[str, set[tuple[str, int]]] = {category: set() for category in categories}
    valid_identities: set[tuple[str, int]] = set()
    invalid_elements = 0
    for element in elements:
        if not isinstance(element, dict):
            invalid_elements += 1
            continue
        element_type = element.get("type")
        element_id = element.get("id")
        tags = element.get("tags")
        if element_type not in {"node", "way", "relation"} or not isinstance(element_id, int):
            invalid_elements += 1
            continue
        if not isinstance(tags, dict):
            invalid_elements += 1
            continue
        identity = (element_type, element_id)
        valid_identities.add(identity)
        for category in categories:
            if any(tags.get(key) == value for key, value in CATEGORY_TAGS[category]):
                seen_by_category[category].add(identity)

    counts = {category: len(seen_by_category[category]) for category in categories}
    return counts, invalid_elements, len(valid_identities)


class AmenitiesTool:
    name = "AmenitiesTool"

    def __init__(self, cache: ToolCache, timeout: float = 15.0, overpass: OverpassClient | None = None):
        self._cache = cache
        self._overpass = overpass or OverpassClient(JsonHttpClient(timeout=timeout))

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        now = datetime.now(UTC)
        if candidate.lat is None or candidate.lon is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Overpass",
                source_url=OVERPASS_SOURCE_URL,
                retrieved_at=now,
                confidence="low",
                error="Cannot query amenities without verified coordinates.",
            )

        categories, unsupported = select_categories(profile)
        if not categories:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                normalized_data={
                    "requested_categories": [],
                    "counts_by_category": {},
                    "unsupported_categories": unsupported,
                    "radius_m": RADIUS_M,
                    "partial": False,
                    "valid_element_count": 0,
                },
                source_name=CATEGORY_REGISTRY_SOURCE,
                retrieved_at=now,
                confidence="low",
                warnings=["No supported amenity category could be selected; the request remains unresolved."],
            )

        params = {"lat": candidate.lat, "lon": candidate.lon, "categories": categories}
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        try:
            data = await self._overpass.query(build_query(categories, candidate.lat, candidate.lon))
            counts, invalid_elements, valid_element_count = parse_category_counts(data, categories)
        except Exception as exc:  # noqa: BLE001 - preserve stale evidence after any provider-path failure
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached amenities data; live lookup failed.")
                return stale_result
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Overpass",
                source_url=OVERPASS_SOURCE_URL,
                retrieved_at=now,
                confidence="low",
                error=f"Amenities lookup failed: {exc}",
            )

        warnings: list[str] = []
        if unsupported:
            warnings.append("Unsupported amenity preferences were not queried: " + ", ".join(unsupported) + ".")
        remark = data.get("remark")
        partial = bool(remark) or invalid_elements > 0
        if remark:
            warnings.append(f"Overpass reported a partial response: {str(remark)[:200]}")
        if invalid_elements:
            warnings.append(f"Ignored {invalid_elements} malformed Overpass element(s).")

        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "requested_categories": categories,
                "counts_by_category": counts,
                "unsupported_categories": unsupported,
                "radius_m": RADIUS_M,
                "partial": partial,
                "valid_element_count": valid_element_count,
            },
            source_name="OpenStreetMap Overpass",
            source_url=OVERPASS_SOURCE_URL,
            retrieved_at=now,
            confidence="low" if partial else "medium",
            warnings=warnings,
        )
        await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
