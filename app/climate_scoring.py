"""Shared deterministic climate-preference parsing and WeatherTool scoring."""

from __future__ import annotations

from typing import Any

CLIMATE_TARGETS: dict[str, tuple[float, float]] = {
    "warm": (22, 8),
    "hot": (32, 8),
    "cold": (5, 8),
    "mild": (18, 6),
    "cool": (12, 8),
}


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def scale(value: float, low: float, high: float) -> float:
    return clamp((value - low) / (high - low))


def climate_preference_directions(climate_preferences: list[str]) -> dict[str, str]:
    """Map supported user phrases to one requested direction per climate dimension."""
    preferences = " | ".join(preference.casefold() for preference in climate_preferences)
    if not preferences:
        return {}

    directions: dict[str, str] = {}
    avoids_extreme_heat = any(
        phrase in preferences for phrase in ("not extremely hot", "avoid extreme heat", "not too hot")
    )
    avoids_freezing = any(phrase in preferences for phrase in ("avoid freezing", "no freezing", "not cold"))
    for label in CLIMATE_TARGETS:
        if label == "hot" and avoids_extreme_heat:
            continue
        if label == "cold" and avoids_freezing:
            continue
        if label in preferences:
            directions["temperature"] = label
            break
    if avoids_extreme_heat:
        directions["extreme_heat"] = "avoid"
    if avoids_freezing:
        directions["freezing"] = "avoid"
    if any(phrase in preferences for phrase in ("low humidity", "not humid", "avoid humidity")):
        directions["humidity"] = "low"
    elif "humid" in preferences:
        directions["humidity"] = "high"
    if any(phrase in preferences for phrase in ("dry", "little rain", "avoid rain", "not rainy")):
        directions["rain"] = "dry"
    elif "rainy" in preferences:
        directions["rain"] = "rainy"
    if any(phrase in preferences for phrase in ("sunny", "sunshine")):
        directions["sunshine"] = "sunny"
    elif "cloudy" in preferences:
        directions["sunshine"] = "cloudy"
    if any(phrase in preferences for phrase in ("no snow", "avoid snow", "not snowy")):
        directions["snow"] = "avoid"
    elif any(phrase in preferences for phrase in ("snowy", "snow")):
        directions["snow"] = "snowy"
    if any(phrase in preferences for phrase in ("calm", "not windy", "avoid strong wind")):
        directions["wind"] = "calm"
    elif "windy" in preferences:
        directions["wind"] = "windy"
    return directions


def requested_climate_dimensions(climate_preferences: list[str]) -> set[str]:
    return set(climate_preference_directions(climate_preferences))


def weather_component_scores(
    normalized_data: dict[str, Any], climate_preferences: list[str]
) -> dict[str, float]:
    """Score only dimensions explicitly requested and available in WeatherTool evidence."""
    directions = climate_preference_directions(climate_preferences)
    scores: dict[str, float] = {}

    temperature_direction = directions.get("temperature")
    avg_high = normalized_data.get("avg_high_c")
    if temperature_direction and isinstance(avg_high, (int, float)):
        target, tolerance = CLIMATE_TARGETS[temperature_direction]
        scores["temperature"] = clamp(1.0 - abs(float(avg_high) - target) / tolerance)

    extreme_heat = normalized_data.get("extreme_heat_frequency")
    if directions.get("extreme_heat") == "avoid" and isinstance(extreme_heat, (int, float)):
        scores["extreme_heat"] = 1.0 - clamp(float(extreme_heat) / 0.20)

    freezing = normalized_data.get("freezing_night_frequency")
    if directions.get("freezing") == "avoid" and isinstance(freezing, (int, float)):
        scores["freezing"] = 1.0 - clamp(float(freezing) / 0.20)

    humidity = normalized_data.get("mean_relative_humidity_pct")
    if isinstance(humidity, (int, float)):
        if directions.get("humidity") == "low":
            scores["humidity"] = 1.0 - scale(float(humidity), 50.0, 80.0)
        elif directions.get("humidity") == "high":
            scores["humidity"] = scale(float(humidity), 50.0, 80.0)

    rainy_days = normalized_data.get("rainy_day_frequency")
    if isinstance(rainy_days, (int, float)):
        if directions.get("rain") == "dry":
            scores["rain"] = 1.0 - clamp(float(rainy_days) / 0.50)
        elif directions.get("rain") == "rainy":
            scores["rain"] = clamp(float(rainy_days) / 0.50)

    sunshine = normalized_data.get("sunshine_fraction_of_daylight")
    if isinstance(sunshine, (int, float)):
        if directions.get("sunshine") == "sunny":
            scores["sunshine"] = scale(float(sunshine), 0.30, 0.80)
        elif directions.get("sunshine") == "cloudy":
            scores["sunshine"] = 1.0 - scale(float(sunshine), 0.30, 0.80)

    snow_days = normalized_data.get("snow_day_frequency")
    if isinstance(snow_days, (int, float)):
        if directions.get("snow") == "avoid":
            scores["snow"] = 1.0 - clamp(float(snow_days) / 0.20)
        elif directions.get("snow") == "snowy":
            scores["snow"] = clamp(float(snow_days) / 0.30)

    gust_p95 = normalized_data.get("p95_daily_max_wind_gust_kmh")
    if isinstance(gust_p95, (int, float)):
        if directions.get("wind") == "calm":
            scores["wind"] = 1.0 - scale(float(gust_p95), 30.0, 70.0)
        elif directions.get("wind") == "windy":
            scores["wind"] = scale(float(gust_p95), 30.0, 70.0)

    return {name: round(score, 4) for name, score in scores.items()}
