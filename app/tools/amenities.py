"""Prompt-driven nearby infrastructure evidence from OpenStreetMap Overpass."""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult
from app.tools.overpass_client import OverpassClient, build_counted_query, parse_counts

RADIUS_M = 3000
OVERPASS_SOURCE_URL = "https://wiki.openstreetmap.org/wiki/Overpass_API"
CATEGORY_REGISTRY_SOURCE = "DigitalNomadAgent amenity category registry"

# User text never reaches query construction directly; only these bounded OSM
# selectors can be interpolated into an Overpass request.
CATEGORY_TAGS: dict[str, tuple[tuple[str, str], ...]] = {
    # Three taggings are in live use for the same thing: office=coworking is the
    # current one, amenity=coworking_space the deprecated one still widely
    # present, and coworking=yes what cafes and other venues that offer desks
    # carry. Querying only the first two returned 0 or 1 for cities with
    # hundreds of mapped cafes (D37).
    "coworking": (("office", "coworking"), ("amenity", "coworking_space"), ("coworking", "yes")),
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
    """One counted set per category -- see overpass_client.build_counted_query."""
    return build_counted_query(
        [
            [
                f'nwr["{key}"="{value}"](around:{RADIUS_M},{lat},{lon});'
                for key, value in CATEGORY_TAGS[category]
            ]
            for category in categories
        ]
    )


def parse_category_counts(data: dict, categories: list[str]) -> tuple[dict[str, int], int, int]:
    """Map the per-set counts back onto their categories, in order.

    `invalid_elements` is retained for the caller's warning/confidence logic but
    is now always 0: counting happens server-side, so there are no per-element
    payloads left to be malformed. A shape Overpass should never produce raises
    instead, and the caller falls back to stale cache.
    """
    counts_list = parse_counts(data, len(categories))
    counts = dict(zip(categories, counts_list, strict=True))
    # Sum rather than a de-duplicated identity count: an element matching two
    # categories is now counted once per category, which is what the per-category
    # scores consume anyway.
    return counts, 0, sum(counts_list)


class AmenitiesTool:
    name = "AmenitiesTool"

    def __init__(self, cache: ToolCache, timeout: float = 15.0, overpass: OverpassClient | None = None):
        self._cache = cache
        # OverpassClient supplies its own timeout/retry policy; the tool-level
        # `timeout` is the fast-REST budget and is too short for Overpass.
        self._overpass = overpass or OverpassClient()

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
