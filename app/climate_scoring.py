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


# The temperature a traveller rules out is stated far more often as an
# avoidance than as a target -- "avoid cold", "not tropical heat", "escaping the
# winter" -- so both directions are read off the same phrases rather than from a
# fixed list of the exact wordings someone might use to decline one.
_HEAT_PHRASES: tuple[str, ...] = (
    "extremely hot",
    "extreme heat",
    "tropical heat",
    "too hot",
    "hot",
    "heat",
)
_COLD_PHRASES: tuple[str, ...] = ("freezing", "too cold", "cold")


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
    avoids_extreme_heat = any(_negatively_requested(preferences, phrase) for phrase in _HEAT_PHRASES)
    avoids_freezing = any(_negatively_requested(preferences, phrase) for phrase in _COLD_PHRASES)
    # `_positively_requested`, not a bare substring test: "avoid cold" contains
    # "cold", and reading that as a request for cold weather sent a retired
    # couple escaping the winter to London, Amsterdam and Dublin while the one
    # mild-winter city in the set ranked last. A label only sets the target when
    # it is asked for, never when it is ruled out.
    for label in CLIMATE_TARGETS:
        if _positively_requested(preferences, label):
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


# Pairs of things that cannot both be true of one place at one time of year.
# Each entry is (phrases for one side, phrases for the other, how to name the
# first back to the reader, how to name the second).
_IRRECONCILABLE_REQUESTS: tuple[tuple[tuple[str, ...], tuple[str, ...], str, str], ...] = (
    (
        ("snow", "snowy", "winter atmosphere", "proper winter", "ski"),
        (
            "swim outdoors",
            "swim outside",
            "outdoor swimming",
            "swimming outdoors",
            "sit outside",
            "sitting outside",
            "outdoor cafe",
            "outside at cafe",
            "outside at caf",
        ),
        "proper snow and a real winter atmosphere",
        "swimming outdoors and sitting outside at cafes in the evening",
    ),
    (
        ("tropical heat", "hot weather", "very hot"),
        ("snow", "snowy", "ski"),
        "tropical heat",
        "snow",
    ),
    (
        ("dry", "arid", "little rain"),
        ("lush", "rainforest", "green all year"),
        "a dry climate",
        "lush year-round greenery",
    ),
)

# "no snow", "rather than snow", "avoid swimming outdoors" -- a thing ruled out
# cannot contradict anything, so a negated phrase never counts as a request.
# "escape"/"escaping" belong here for the same reason: someone escaping the
# winter is naming what they are leaving, not what they want to arrive in.
_NEGATION_MARKERS = (
    "no ",
    "not ",
    "never ",
    "avoid",
    "without",
    "rather than",
    "don't",
    "dont",
    "escape",
    "escaping",
    "away from",
)


def _positively_requested(text: str, phrase: str) -> bool:
    index = text.find(phrase)
    while index != -1:
        # A negation reaches only the statement it belongs to. Preferences
        # arrive as separate statements joined by "|", and in "no snow | mild"
        # the "no" governs the snow, not the mild -- without the cut, stating
        # one thing to avoid silently swallowed the next thing asked for.
        window = text[max(0, index - 24) : index].rpartition("|")[2]
        if not any(marker in window for marker in _NEGATION_MARKERS):
            return True
        index = text.find(phrase, index + 1)
    return False


def _negatively_requested(text: str, phrase: str) -> bool:
    """Whether the phrase appears, and every appearance of it is ruled out."""
    return phrase in text and not _positively_requested(text, phrase)


def contradictory_climate_requests(*texts: list[str] | str) -> list[str]:
    """Requests that cannot both be satisfied, named in the reader's words.

    P08 is the "plausibly-worded but internally impossible" case, and it is
    impossible on two axes: the $400 budget *and* wanting proper snow while also
    swimming outdoors and sitting outside at cafes in the evening. Only the
    budget was ever detected. The answer never used the words "snow" or "swim",
    so the more interesting contradiction went unmentioned entirely (D38).
    """
    parts: list[str] = []
    for text in texts:
        parts.extend(text if isinstance(text, list) else [text])
    haystack = " | ".join(part.casefold() for part in parts if part)
    if not haystack:
        return []

    conflicts: list[str] = []
    for first_phrases, second_phrases, first_label, second_label in _IRRECONCILABLE_REQUESTS:
        has_first = any(_positively_requested(haystack, phrase) for phrase in first_phrases)
        has_second = any(_positively_requested(haystack, phrase) for phrase in second_phrases)
        if has_first and has_second:
            conflicts.append(
                f"You asked for {first_label} and for {second_label}. No place offers both at "
                "the same time of year -- tell me which one matters more and I can rank properly "
                "for it."
            )
    return conflicts


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
