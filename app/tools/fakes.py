"""Deterministic, offline fake tool implementations used in tests.

Same BaseTool protocol as the real tools, no network access. Keyed by
candidate place name with a stable hash-based fallback for arbitrary places,
so tests are never limited to a fixed set of names.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.climate_scoring import requested_climate_dimensions
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.activities import CATEGORY_RADII_M, select_activity_categories

_KNOWN_COORDS: dict[str, tuple[float, float, str]] = {
    "lisbon": (38.7223, -9.1393, "PT"),
    "tbilisi": (41.7151, 44.8271, "GE"),
    "berlin": (52.5200, 13.4050, "DE"),
    "tirana": (41.3275, 19.8187, "AL"),
    "mexico city": (19.4326, -99.1332, "MX"),
    "warsaw": (52.2297, 21.0122, "PL"),
    "dublin": (53.3498, -6.2603, "IE"),
    "porto": (41.1579, -8.6291, "PT"),
    "melbourne": (-37.8136, 144.9631, "AU"),
    "valencia": (39.4699, -0.3763, "ES"),
    "santorini": (36.3932, 25.4615, "GR"),
    "kotor": (42.4247, 18.7712, "ME"),
    "nice": (43.7102, 7.2620, "FR"),
    "nowhereville": None,  # deliberately unverifiable, for negative tests
}


def _fallback_coords(place_name: str) -> tuple[float, float, str]:
    seed = sum(ord(c) for c in place_name)
    lat = -60 + (seed % 120)
    lon = -170 + (seed % 340)
    return float(lat), float(lon), "XX"


class FakeGeocodingTool:
    name = "GeocodingTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        key = candidate.place_name.strip().lower()
        if key in _KNOWN_COORDS and _KNOWN_COORDS[key] is None:
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="OpenStreetMap Nominatim (fake)",
                retrieved_at=datetime.now(UTC),
                confidence="low",
                error="This destination could not be reliably verified.",
            )
        lat, lon, cc = _KNOWN_COORDS.get(key) or _fallback_coords(candidate.place_name)
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "lat": lat,
                "lon": lon,
                "canonical_name": f"{candidate.place_name}, {candidate.country}",
                "country_code": cc,
            },
            source_name="OpenStreetMap Nominatim (fake)",
            source_url="https://nominatim.openstreetmap.org/",
            retrieved_at=datetime.now(UTC),
            confidence="high",
        )


class FakeWeatherTool:
    name = "WeatherTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        seed = sum(ord(c) for c in candidate.place_name)
        avg_high = 15 + (seed % 15)
        avg_low = avg_high - 8
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "avg_high_c": float(avg_high),
                "avg_low_c": float(avg_low),
                "p90_high_c": float(avg_high + 4),
                "p10_low_c": float(avg_low - 4),
                "avg_apparent_high_c": float(avg_high + 2),
                "mean_relative_humidity_pct": float(50 + seed % 30),
                "avg_daily_precip_mm": float(seed % 5),
                "avg_monthly_precip_mm": float(20 + seed % 80),
                "rainy_day_frequency": (seed % 30) / 100,
                "heavy_precipitation_day_frequency": (seed % 5) / 100,
                "p95_daily_precip_mm": float(10 + seed % 20),
                "avg_monthly_snowfall_cm": float(seed % 10),
                "snow_day_frequency": (seed % 15) / 100,
                "avg_sunshine_hours_per_day": float(5 + seed % 7),
                "sunshine_fraction_of_daylight": 0.4 + (seed % 40) / 100,
                "avg_daily_max_wind_gust_kmh": float(20 + seed % 30),
                "p95_daily_max_wind_gust_kmh": float(30 + seed % 40),
                "avg_daily_max_wind_speed_kmh": float(10 + seed % 20),
                "p95_daily_max_wind_speed_kmh": float(20 + seed % 30),
                "high_wind_day_frequency": (seed % 20) / 100,
                "mean_cloud_cover_pct": float(20 + seed % 60),
                "extreme_heat_frequency": (seed % 12) / 100,
                "freezing_night_frequency": (seed % 8) / 100,
                "data_kind": "climatology",
                "target_months": profile.target_months or [7],
                "period_start": "2021-01-01",
                "period_end": "2025-12-31",
                "year_count": 5,
                "years_represented": [2021, 2022, 2023, 2024, 2025],
                "temperature_coverage": 1.0,
            },
            source_name="Open-Meteo (fake)",
            source_url="https://open-meteo.com/",
            retrieved_at=datetime.now(UTC),
            data_date="2021-2025 fake climatology",
            confidence="medium",
        )


class FakeWikivoyageClimateTool:
    name = "WikivoyageClimateTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        components = {
            component: round(0.6 + (index % 3) * 0.1, 2)
            for index, component in enumerate(sorted(requested_climate_dimensions(profile.climate_preferences)))
        }
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "resolved_title": candidate.canonical_name or candidate.place_name,
                "section_title": "Climate",
                "revision_id": 123456,
                "revision_timestamp": "2026-01-01T00:00:00Z",
                "target_months": profile.target_months or [7],
                "excerpt": "Deterministic fake climate-section evidence.",
                "preview_excerpt": "Deterministic fake climate-section evidence.",
                "context_chunks": [
                    {
                        "subsection": "Climate",
                        "text": "Deterministic fake climate-section evidence.",
                        "subsection_truncated": False,
                    }
                ],
                "full_section_chars": 44,
                "included_chars": 44,
                "truncated": False,
                "included_subsections": ["Climate"],
                "truncated_subsections": [],
                "omitted_subsections": [],
                "climate_chart": [],
                "phrase_signals": {},
                "component_scores": components,
                "component_details": {},
                "lexicon_version": 1,
            },
            source_name="Wikivoyage climate section (fake)",
            source_url="https://en.wikivoyage.org/w/index.php?oldid=123456#Climate",
            retrieved_at=datetime.now(UTC),
            data_date="Wikivoyage fake revision 123456",
            confidence="medium" if components else "low",
        )


class FakeAmenitiesTool:
    name = "AmenitiesTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        seed = sum(ord(c) for c in candidate.place_name)
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "requested_categories": ["coworking", "cafe", "university", "library"],
                "counts_by_category": {
                    "coworking": seed % 7,
                    "cafe": seed % 35,
                    "university": seed % 4,
                    "library": seed % 10,
                },
                "unsupported_categories": [],
                "radius_m": 3000,
                "partial": False,
                "valid_element_count": seed % 50,
            },
            source_name="OpenStreetMap Overpass (fake)",
            source_url="https://overpass-api.de/",
            retrieved_at=datetime.now(UTC),
            confidence="medium",
        )


class FakePlaceContextTool:
    name = "PlaceContextTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={"excerpt": f"{candidate.place_name} is a fake test destination with a walkable center."},
            source_name="Wikivoyage (fake)",
            source_url=f"https://en.wikivoyage.org/wiki/{candidate.place_name.replace(' ', '_')}",
            retrieved_at=datetime.now(UTC),
            confidence="medium",
        )


class FakeTimezoneFitTool:
    name = "TimezoneFitTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        seed = sum(ord(c) for c in candidate.place_name)
        diff = float(seed % 10)
        overlap = max(0.0, 8 - diff)
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "candidate_timezone": "Etc/FakeZone",
                "origin_timezone": "Etc/UTC",
                "resolved_origin_name": profile.origin or "Fake origin",
                "resolved_origin_country": None,
                "origin_resolution_method": "fake",
                "representative_date": "2026-01-15",
                "utc_offset_diff_hours": diff,
                "estimated_workday_overlap_hours": overlap,
            },
            source_name="timezonefinder + zoneinfo (fake)",
            retrieved_at=datetime.now(UTC),
            confidence="medium",
        )


class FakeBudgetFitTool:
    name = "BudgetFitTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        seed = sum(ord(c) for c in candidate.place_name)
        lower = 800 + (seed % 400)
        upper = lower + 700
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "lower_monthly_estimate": float(lower),
                "upper_monthly_estimate": float(upper),
                "currency": "USD",
                "included_categories": "rent+food+transport+utilities",
            },
            source_name="PlaceMatch curated cost estimates (fake)",
            retrieved_at=datetime.now(UTC),
            data_date="fake-test-data",
            confidence="low",
            warnings=["This is a curated sample/test-only estimate, not a live price."],
        )


class FakeEducationOptionsTool:
    name = "EducationOptionsTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        field_matched = bool(profile.study_field)
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "universities": [{"name": f"University of {candidate.place_name} (fake)", "url": "https://example.edu"}],
                "field_matched": field_matched,
                "match_score": 0.8 if field_matched else 0.4,
            },
            source_name="Official university websites (fake)",
            source_url="https://example.edu",
            retrieved_at=datetime.now(UTC),
            confidence="medium" if field_matched else "low",
        )


class FakeTransportAccessTool:
    name = "TransportAccessTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        seed = sum(ord(c) for c in candidate.place_name)
        now = datetime.now(UTC)
        counts = {
            "airports_within_50km": 1 + (seed % 2),
            "potential_mainline_rail_stations_within_10km": seed % 3,
            "bus_terminals_within_10km": 1 + seed % 4,
            "ferry_terminals_within_10km": seed % 2,
        }
        context = {
            "resolved_title": candidate.canonical_name or candidate.place_name,
            "page_id": 12345,
            "revision_id": 123456,
            "revision_timestamp": "2026-01-01T00:00:00Z",
            "section_title": "Get in",
            "section_index": "2",
            "section_anchor": "Get_in",
            "preview_excerpt": "Deterministic fake arrival context for agent reasoning.",
            "excerpt": "Deterministic fake arrival context for agent reasoning.",
            "context_chunks": [
                {
                    "subsection": "Get in",
                    "text": "Deterministic fake arrival context for agent reasoning.",
                    "subsection_truncated": False,
                }
            ],
            "full_section_chars": 56,
            "included_chars": 56,
            "truncated": False,
            "included_subsections": ["Get in"],
            "truncated_subsections": [],
            "omitted_subsections": [],
        }
        origin_requested = bool(profile.origin)
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "counts_by_component": counts,
                "airport_radius_m": 50_000,
                "terminal_radius_m": 10_000,
                "osm_status": "available",
                "origin_input": profile.origin,
                "origin_status": "available" if origin_requested else "not_requested",
                "resolved_origin_name": "Tel Aviv" if origin_requested else None,
                "resolved_origin_country": "Israel" if origin_requested else None,
                "straight_line_distance_km": 3300.0 if origin_requested else None,
                "wikivoyage_context": context,
                "wikivoyage_status": "available",
                "scoring_status": "unresolved_pending_llm",
                "partial": False,
            },
            source_name="OpenStreetMap, Open-Meteo, and Wikivoyage (fake)",
            retrieved_at=now,
            confidence="medium",
            evidence_items=[
                EvidenceItem(
                    criterion="accessibility",
                    component="osm_arrival_infrastructure_counts",
                    normalized_data={"counts_by_component": counts},
                    source=EvidenceSource(
                        source_name="OpenStreetMap destination arrival infrastructure (fake)",
                        retrieved_at=now,
                    ),
                ),
                EvidenceItem(
                    criterion="accessibility",
                    component="wikivoyage_get_in_context",
                    normalized_data=context,
                    source=EvidenceSource(
                        source_name="Wikivoyage Get in section (fake)",
                        source_url="https://en.wikivoyage.org/w/index.php?oldid=123456#Get_in",
                        retrieved_at=now,
                    ),
                ),
            ],
        )


class FakeLocalMobilityTool:
    name = "LocalMobilityTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        del profile
        seed = sum(ord(c) for c in candidate.place_name)
        now = datetime.now(UTC)
        counts = {
            "bus_stops": 5 + seed % 30,
            "rail_metro_tram_stations": seed % 8,
            "pedestrian_ways": 10 + seed % 50,
            "cycleways": seed % 25,
        }
        context = {
            "resolved_title": candidate.canonical_name or candidate.place_name,
            "page_id": 12345,
            "revision_id": 123456,
            "revision_timestamp": "2026-01-01T00:00:00Z",
            "section_title": "Get around",
            "section_index": "4",
            "section_anchor": "Get_around",
            "excerpt": "Deterministic fake local-mobility context for agent reasoning.",
            "preview_excerpt": "Deterministic fake local-mobility context for agent reasoning.",
            "context_chunks": [
                {
                    "subsection": "Get around",
                    "text": "Deterministic fake local-mobility context for agent reasoning.",
                    "subsection_truncated": False,
                }
            ],
            "full_section_chars": 62,
            "included_chars": 62,
            "truncated": False,
            "included_subsections": ["Get around"],
            "truncated_subsections": [],
            "omitted_subsections": [],
        }
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "counts_by_component": counts,
                "valid_element_count": sum(counts.values()),
                "radius_m": 3000,
                "osm_status": "available",
                "wikivoyage_context": context,
                "wikivoyage_status": "available",
                "scoring_status": "unresolved_pending_llm",
                "partial": False,
            },
            source_name="OpenStreetMap and Wikivoyage (fake)",
            retrieved_at=now,
            confidence="medium",
            evidence_items=[
                EvidenceItem(
                    criterion="transportation",
                    component="osm_local_mobility_counts",
                    normalized_data={"counts_by_component": counts, "radius_m": 3000},
                    source=EvidenceSource(
                        source_name="OpenStreetMap local mobility infrastructure (fake)",
                        source_url="https://wiki.openstreetmap.org/wiki/Overpass_API",
                        retrieved_at=now,
                    ),
                ),
                EvidenceItem(
                    criterion="transportation",
                    component="wikivoyage_get_around_context",
                    normalized_data=context,
                    source=EvidenceSource(
                        source_name="Wikivoyage Get around section (fake)",
                        source_url=(
                            "https://en.wikivoyage.org/w/index.php?oldid=123456#Get_around"
                        ),
                        retrieved_at=now,
                        data_date="Wikivoyage fake revision 123456",
                    ),
                ),
            ],
        )


class FakeActivitiesTool:
    name = "ActivitiesTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        seed = sum(ord(c) for c in candidate.place_name)
        now = datetime.now(UTC)
        categories, unsupported, used_fallback = select_activity_categories(profile)
        counts = {category: 1 + (seed + index) % 12 for index, category in enumerate(categories)}
        context_by_section = {}
        for section_index, section_title in (("3", "See"), ("4", "Do")):
            text = f"Deterministic fake Wikivoyage {section_title} context for activity reasoning."
            context_by_section[section_title.casefold()] = {
                "resolved_title": candidate.canonical_name or candidate.place_name,
                "page_id": 12345,
                "revision_id": 123456,
                "revision_timestamp": "2026-01-01T00:00:00Z",
                "section_title": section_title,
                "section_index": section_index,
                "section_anchor": section_title,
                "preview_excerpt": text,
                "excerpt": text,
                "context_chunks": [
                    {"subsection": section_title, "text": text, "subsection_truncated": False}
                ],
                "full_section_chars": len(text),
                "included_chars": len(text),
                "truncated": False,
                "included_subsections": [section_title],
                "truncated_subsections": [],
                "omitted_subsections": [],
            }
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "requested_categories": categories,
                "unsupported_categories": unsupported,
                "used_generic_vacation_fallback": used_fallback,
                "counts_by_category": counts,
                "category_status": {category: "available" for category in categories}
                | {category: "unsupported" for category in unsupported},
                "radius_by_category_m": {category: CATEGORY_RADII_M[category] for category in categories},
                "osm_status": "available",
                "wikivoyage_see_context": context_by_section["see"],
                "wikivoyage_do_context": context_by_section["do"],
                "wikivoyage_section_status": {"see": "available", "do": "available"},
                "scoring_status": "unresolved_pending_llm",
                "partial": False,
            },
            source_name="OpenStreetMap and Wikivoyage (fake)",
            retrieved_at=now,
            confidence="medium",
            evidence_items=[
                EvidenceItem(
                    criterion="activities",
                    component="osm_activity_counts",
                    normalized_data={"counts_by_category": counts},
                    source=EvidenceSource(
                        source_name="OpenStreetMap activity evidence (fake)",
                        retrieved_at=now,
                    ),
                ),
                *[
                    EvidenceItem(
                        criterion="activities",
                        component=f"wikivoyage_{key}_context",
                        normalized_data=context,
                        source=EvidenceSource(
                            source_name=f"Wikivoyage {context['section_title']} section (fake)",
                            source_url=(
                                "https://en.wikivoyage.org/w/index.php?"
                                f"oldid=123456#{context['section_anchor']}"
                            ),
                            retrieved_at=now,
                        ),
                    )
                    for key, context in context_by_section.items()
                ],
            ],
        )


class FakeOfficialSourceTool:
    name = "OfficialSourceTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "official_links": [
                    {"type": "tourism", "name": f"{candidate.country} Tourism Board (fake)", "url": "https://example.gov"}
                ]
            },
            source_name="Curated official sources (fake)",
            source_url="https://example.gov",
            retrieved_at=datetime.now(UTC),
            confidence="medium",
        )


def build_fake_tool_registry_dict() -> dict[str, object]:
    return {
        "GeocodingTool": FakeGeocodingTool(),
        "WeatherTool": FakeWeatherTool(),
        "WikivoyageClimateTool": FakeWikivoyageClimateTool(),
        "AmenitiesTool": FakeAmenitiesTool(),
        "PlaceContextTool": FakePlaceContextTool(),
        "TimezoneFitTool": FakeTimezoneFitTool(),
        "BudgetFitTool": FakeBudgetFitTool(),
        "EducationOptionsTool": FakeEducationOptionsTool(),
        "TransportAccessTool": FakeTransportAccessTool(),
        "LocalMobilityTool": FakeLocalMobilityTool(),
        "ActivitiesTool": FakeActivitiesTool(),
        "OfficialSourceTool": FakeOfficialSourceTool(),
    }
