"""Deterministic, offline fake tool implementations used in tests.

Same BaseTool protocol as the real tools, no network access. Keyed by
candidate place name with a stable hash-based fallback for arbitrary places,
so tests are never limited to a fixed set of names.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult

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


class FakeAmenitiesTool:
    name = "AmenitiesTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        seed = sum(ord(c) for c in candidate.place_name)
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={"categories": ["cafe", "coworking"], "count": seed % 40, "radius_m": 3000},
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


class FakeAccessibilityTool:
    name = "AccessibilityTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        seed = sum(ord(c) for c in candidate.place_name)
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "airports_within_50km": 1 + (seed % 2),
                "train_stations_within_5km": seed % 3,
                "likely_car_dependent": seed % 3 == 0,
            },
            source_name="OpenStreetMap Overpass (fake)",
            retrieved_at=datetime.now(UTC),
            confidence="medium",
        )


class FakeActivitiesTool:
    name = "ActivitiesTool"

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        seed = sum(ord(c) for c in candidate.place_name)
        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={"categories": ["beach", "museum"], "count": seed % 20, "radius_m": 5000},
            source_name="OpenStreetMap Overpass (fake)",
            retrieved_at=datetime.now(UTC),
            confidence="medium",
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
        "AmenitiesTool": FakeAmenitiesTool(),
        "PlaceContextTool": FakePlaceContextTool(),
        "TimezoneFitTool": FakeTimezoneFitTool(),
        "BudgetFitTool": FakeBudgetFitTool(),
        "EducationOptionsTool": FakeEducationOptionsTool(),
        "AccessibilityTool": FakeAccessibilityTool(),
        "ActivitiesTool": FakeActivitiesTool(),
        "OfficialSourceTool": FakeOfficialSourceTool(),
    }
