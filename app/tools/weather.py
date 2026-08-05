"""B. WeatherTool -- requested-season climatology via Open-Meteo."""

from __future__ import annotations

import calendar
import math
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult
from app.tools.http_client import JsonHttpClient

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE_URL = "https://open-meteo.com/en/docs/historical-weather-api"
HISTORY_YEARS = 5
MIN_REPRESENTED_YEARS = 4
MIN_REQUIRED_COVERAGE = 0.80
MIN_OPTIONAL_COVERAGE = 0.80

EXTREME_HEAT_C = 35.0
FREEZING_NIGHT_C = 0.0
RAINY_DAY_MM = 1.0
HEAVY_PRECIPITATION_MM = 20.0
SNOW_DAY_CM = 0.1
HIGH_WIND_GUST_KMH = 50.0

DAILY_VARIABLES = (
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "relative_humidity_2m_mean",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "sunshine_duration",
    "daylight_duration",
    "cloud_cover_mean",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
)
REQUIRED_VARIABLES = {"temperature_2m_max", "temperature_2m_min"}


@dataclass(frozen=True)
class _DailyRow:
    day: date
    values: dict[str, float | None]


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _expected_days(years: range, months: list[int]) -> int:
    return sum(calendar.monthrange(year, month)[1] for year in years for month in months)


def _values(rows: list[_DailyRow], variable: str) -> list[float]:
    return [value for row in rows if (value := row.values.get(variable)) is not None]


def _frequency(values: list[float], predicate: Callable[[float], bool]) -> float | None:
    return sum(1 for value in values if predicate(value)) / len(values) if values else None


def _monthly_total_average(rows: list[_DailyRow], variable: str) -> float | None:
    groups: dict[tuple[int, int], list[float]] = defaultdict(list)
    for row in rows:
        value = row.values.get(variable)
        if value is not None:
            groups[(row.day.year, row.day.month)].append(value)

    complete_totals = []
    for (year, month), values in groups.items():
        expected = calendar.monthrange(year, month)[1]
        if len(values) / expected >= MIN_OPTIONAL_COVERAGE:
            complete_totals.append(sum(values))
    return _mean(complete_totals)


def _parse_rows(
    data: dict[str, Any], *, years: range, months: list[int]
) -> tuple[list[_DailyRow], dict[str, float], list[str]]:
    daily = data.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("Open-Meteo response is missing the daily data object")
    times = daily.get("time")
    if not isinstance(times, list) or not times:
        raise ValueError("Open-Meteo response is missing daily dates")

    arrays: dict[str, list[Any] | None] = {}
    unavailable: list[str] = []
    for variable in DAILY_VARIABLES:
        values = daily.get(variable)
        if not isinstance(values, list) or len(values) != len(times):
            if variable in REQUIRED_VARIABLES:
                raise ValueError(f"Open-Meteo returned a missing or misaligned {variable} array")
            arrays[variable] = None
            unavailable.append(variable)
        else:
            arrays[variable] = values

    rows_by_date: dict[date, _DailyRow] = {}
    valid_years = set(years)
    valid_months = set(months)
    invalid_dates = 0
    for index, raw_day in enumerate(times):
        try:
            parsed_day = date.fromisoformat(str(raw_day))
        except ValueError:
            invalid_dates += 1
            continue
        if parsed_day.year not in valid_years or parsed_day.month not in valid_months:
            continue
        rows_by_date[parsed_day] = _DailyRow(
            day=parsed_day,
            values={
                variable: _finite_float(values[index]) if values is not None else None
                for variable, values in arrays.items()
            },
        )

    rows = [rows_by_date[day] for day in sorted(rows_by_date)]
    expected = _expected_days(years, months)
    coverage = {
        variable: len(_values(rows, variable)) / expected if expected else 0.0 for variable in DAILY_VARIABLES
    }
    warnings = []
    if unavailable:
        warnings.append("Unavailable or misaligned optional variables: " + ", ".join(sorted(unavailable)) + ".")
    if invalid_dates:
        warnings.append(f"Ignored {invalid_dates} malformed daily date value(s).")
    return rows, coverage, warnings


def _optional_metric(
    variable: str,
    coverage: dict[str, float],
    warnings: list[str],
    calculate: Callable[[], float | None],
) -> float | None:
    if coverage[variable] < MIN_OPTIONAL_COVERAGE:
        warnings.append(
            f"{variable} coverage was {coverage[variable]:.1%}; its derived metrics are reported as unavailable."
        )
        return None
    return calculate()


def _month_summary(rows: list[_DailyRow], month: int, years: range) -> dict[str, Any]:
    selected = [row for row in rows if row.day.month == month]
    expected = sum(calendar.monthrange(year, month)[1] for year in years)
    highs = _values(selected, "temperature_2m_max")
    lows = _values(selected, "temperature_2m_min")
    result: dict[str, Any] = {
        "month": month,
        "usable_temperature_days": min(len(highs), len(lows)),
        "avg_high_c": _round(_mean(highs), 1),
        "avg_low_c": _round(_mean(lows), 1),
    }
    optional = {
        "mean_relative_humidity_pct": ("relative_humidity_2m_mean", _mean),
        "avg_daily_precip_mm": ("precipitation_sum", _mean),
        "avg_daily_rain_mm": ("rain_sum", _mean),
        "avg_daily_snowfall_cm": ("snowfall_sum", _mean),
        "avg_daily_sunshine_hours": (
            "sunshine_duration",
            lambda values: _mean([value / 3600 for value in values]),
        ),
        "avg_daily_max_wind_gust_kmh": ("wind_gusts_10m_max", _mean),
        "mean_cloud_cover_pct": ("cloud_cover_mean", _mean),
    }
    for output_name, (variable, aggregate) in optional.items():
        values = _values(selected, variable)
        if expected and len(values) / expected >= MIN_OPTIONAL_COVERAGE:
            result[output_name] = _round(aggregate(values), 1)
    return result


def _build_climatology(
    data: dict[str, Any], *, years: range, months: list[int]
) -> tuple[dict[str, Any], str, list[str]]:
    rows, coverage, warnings = _parse_rows(data, years=years, months=months)
    expected = _expected_days(years, months)
    highs = _values(rows, "temperature_2m_max")
    lows = _values(rows, "temperature_2m_min")
    temperature_days = [
        row
        for row in rows
        if row.values["temperature_2m_max"] is not None and row.values["temperature_2m_min"] is not None
    ]
    temperature_coverage = len(temperature_days) / expected if expected else 0.0
    represented_years = sorted({row.day.year for row in temperature_days})
    if temperature_coverage < MIN_REQUIRED_COVERAGE or len(represented_years) < MIN_REPRESENTED_YEARS:
        raise ValueError(
            "Historical temperature coverage is insufficient "
            f"({temperature_coverage:.1%}, {len(represented_years)} represented years)."
        )

    apparent = _values(rows, "apparent_temperature_max")
    humidity = _values(rows, "relative_humidity_2m_mean")
    precipitation = _values(rows, "precipitation_sum")
    rain = _values(rows, "rain_sum")
    snowfall = _values(rows, "snowfall_sum")
    sunshine = _values(rows, "sunshine_duration")
    gusts = _values(rows, "wind_gusts_10m_max")
    wind_speeds = _values(rows, "wind_speed_10m_max")
    cloud_cover = _values(rows, "cloud_cover_mean")

    apparent_mean = _optional_metric(
        "apparent_temperature_max", coverage, warnings, lambda: _mean(apparent)
    )
    humidity_mean = _optional_metric(
        "relative_humidity_2m_mean", coverage, warnings, lambda: _mean(humidity)
    )
    precipitation_mean = _optional_metric(
        "precipitation_sum", coverage, warnings, lambda: _mean(precipitation)
    )
    precipitation_monthly = _optional_metric(
        "precipitation_sum", coverage, warnings, lambda: _monthly_total_average(rows, "precipitation_sum")
    )
    rainy_frequency = _optional_metric(
        "rain_sum",
        coverage,
        warnings,
        lambda: _frequency(rain, lambda value: value >= RAINY_DAY_MM),
    )
    heavy_precipitation_frequency = _optional_metric(
        "precipitation_sum",
        coverage,
        warnings,
        lambda: _frequency(precipitation, lambda value: value >= HEAVY_PRECIPITATION_MM),
    )
    precipitation_p95 = _optional_metric(
        "precipitation_sum", coverage, warnings, lambda: _percentile(precipitation, 0.95)
    )
    snowfall_monthly = _optional_metric(
        "snowfall_sum", coverage, warnings, lambda: _monthly_total_average(rows, "snowfall_sum")
    )
    snow_frequency = _optional_metric(
        "snowfall_sum",
        coverage,
        warnings,
        lambda: _frequency(snowfall, lambda value: value >= SNOW_DAY_CM),
    )
    sunshine_hours = _optional_metric(
        "sunshine_duration", coverage, warnings, lambda: _mean([value / 3600 for value in sunshine])
    )
    sunshine_fraction = None
    if (
        coverage["sunshine_duration"] >= MIN_OPTIONAL_COVERAGE
        and coverage["daylight_duration"] >= MIN_OPTIONAL_COVERAGE
    ):
        paired_sunlight = [
            (row.values["sunshine_duration"], row.values["daylight_duration"])
            for row in rows
            if row.values["sunshine_duration"] is not None
            and row.values["daylight_duration"] is not None
            and row.values["daylight_duration"] > 0
        ]
        sunshine_fraction = _mean([max(0.0, min(1.0, sun / daylight)) for sun, daylight in paired_sunlight])
    else:
        warnings.append("Sunshine fraction is unavailable because sunshine or daylight coverage was insufficient.")
    gust_mean = _optional_metric("wind_gusts_10m_max", coverage, warnings, lambda: _mean(gusts))
    gust_p95 = _optional_metric(
        "wind_gusts_10m_max", coverage, warnings, lambda: _percentile(gusts, 0.95)
    )
    high_wind_frequency = _optional_metric(
        "wind_gusts_10m_max",
        coverage,
        warnings,
        lambda: _frequency(gusts, lambda value: value >= HIGH_WIND_GUST_KMH),
    )
    wind_speed_mean = _optional_metric(
        "wind_speed_10m_max", coverage, warnings, lambda: _mean(wind_speeds)
    )
    wind_speed_p95 = _optional_metric(
        "wind_speed_10m_max", coverage, warnings, lambda: _percentile(wind_speeds, 0.95)
    )
    cloud_cover_mean = _optional_metric(
        "cloud_cover_mean", coverage, warnings, lambda: _mean(cloud_cover)
    )

    annual_highs: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        high = row.values["temperature_2m_max"]
        if high is not None:
            annual_highs[row.day.year].append(high)
    annual_means = [_mean(values) for values in annual_highs.values()]
    interannual_stddev = statistics.pstdev([value for value in annual_means if value is not None])

    warnings.append(
        "This is five-year historical climatology from gridded reanalysis data, not a forecast or a station reading."
    )

    normalized_data = {
        "data_kind": "climatology",
        "target_months": months,
        "period_start": f"{years.start}-01-01",
        "period_end": f"{years.stop - 1}-12-31",
        "years_represented": represented_years,
        "year_count": len(represented_years),
        "expected_days": expected,
        "usable_temperature_days": len(temperature_days),
        "temperature_coverage": round(temperature_coverage, 4),
        "variable_coverage": {name: round(value, 4) for name, value in coverage.items()},
        "timezone": data.get("timezone"),
        "avg_high_c": _round(_mean(highs), 1),
        "avg_low_c": _round(_mean(lows), 1),
        "p90_high_c": _round(_percentile(highs, 0.90), 1),
        "p10_low_c": _round(_percentile(lows, 0.10), 1),
        "avg_apparent_high_c": _round(apparent_mean, 1),
        "mean_relative_humidity_pct": _round(humidity_mean, 1),
        "avg_daily_precip_mm": _round(precipitation_mean, 2),
        "avg_monthly_precip_mm": _round(precipitation_monthly, 1),
        "rainy_day_frequency": _round(rainy_frequency, 4),
        "heavy_precipitation_day_frequency": _round(heavy_precipitation_frequency, 4),
        "p95_daily_precip_mm": _round(precipitation_p95, 1),
        "avg_monthly_snowfall_cm": _round(snowfall_monthly, 1),
        "snow_day_frequency": _round(snow_frequency, 4),
        "avg_sunshine_hours_per_day": _round(sunshine_hours, 1),
        "sunshine_fraction_of_daylight": _round(sunshine_fraction, 4),
        "avg_daily_max_wind_gust_kmh": _round(gust_mean, 1),
        "p95_daily_max_wind_gust_kmh": _round(gust_p95, 1),
        "avg_daily_max_wind_speed_kmh": _round(wind_speed_mean, 1),
        "p95_daily_max_wind_speed_kmh": _round(wind_speed_p95, 1),
        "high_wind_day_frequency": _round(high_wind_frequency, 4),
        "mean_cloud_cover_pct": _round(cloud_cover_mean, 1),
        "extreme_heat_frequency": _round(
            _frequency(highs, lambda value: value >= EXTREME_HEAT_C), 4
        ),
        "freezing_night_frequency": _round(
            _frequency(lows, lambda value: value <= FREEZING_NIGHT_C), 4
        ),
        "interannual_avg_high_stddev_c": _round(interannual_stddev, 2),
        "thresholds": {
            "extreme_heat_c": EXTREME_HEAT_C,
            "freezing_night_c": FREEZING_NIGHT_C,
            "rainy_day_mm": RAINY_DAY_MM,
            "heavy_precipitation_mm": HEAVY_PRECIPITATION_MM,
            "snow_day_cm": SNOW_DAY_CM,
            "high_wind_gust_kmh": HIGH_WIND_GUST_KMH,
        },
        "monthly_climatology": [_month_summary(rows, month, years) for month in months],
    }
    confidence = "high" if temperature_coverage >= 0.95 and len(represented_years) == HISTORY_YEARS else "medium"
    return normalized_data, confidence, list(dict.fromkeys(warnings))


class WeatherTool:
    name = "WeatherTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 10.0,
        http: JsonHttpClient | None = None,
        today: Callable[[], date] = _utc_today,
    ):
        self._cache = cache
        self._http = http or JsonHttpClient(timeout=timeout)
        self._today = today

    async def _fetch(self, lat: float, lon: float, start: str, end: str) -> dict[str, Any]:
        payload = await self._http.get_json(
            ARCHIVE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start,
                "end_date": end,
                "daily": ",".join(DAILY_VARIABLES),
                "timezone": "auto",
            },
        )
        if not isinstance(payload, dict):
            raise ValueError("Open-Meteo returned a non-object JSON response")
        return payload

    @staticmethod
    def _error(place: str, retrieved_at: datetime, message: str) -> ToolResult:
        return ToolResult(
            tool_name=WeatherTool.name,
            place=place,
            source_name="Open-Meteo historical weather archive",
            source_url=SOURCE_URL,
            retrieved_at=retrieved_at,
            confidence="low",
            error=message,
        )

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        now = datetime.now(UTC)
        if candidate.lat is None or candidate.lon is None:
            return self._error(candidate.place_name, now, "Cannot fetch climatology without verified coordinates.")

        today = self._today()
        months = list(dict.fromkeys(profile.target_months))
        if not months:
            # Substituting the current calendar month produced a real, confident,
            # citable climatology for a month the traveller never mentioned --
            # and it reached the bibliography as "climatology for months 8" on a
            # request that named no season at all (D31). No months, no reading.
            return self._error(
                candidate.place_name,
                now,
                "Cannot build a seasonal climatology: the request did not establish when the stay happens.",
            )
        first_year = today.year - HISTORY_YEARS
        years = range(first_year, today.year)
        start = f"{first_year}-01-01"
        end = f"{today.year - 1}-12-31"
        params = {
            "lat": candidate.lat,
            "lon": candidate.lon,
            "start": start,
            "end": end,
            "months": months,
            "variables": DAILY_VARIABLES,
        }
        cache_name = self.name + ":climate"
        cached, stale = await self._cache.get(cache_name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        try:
            data = await self._fetch(candidate.lat, candidate.lon, start, end)
            normalized_data, confidence, warnings = _build_climatology(
                data, years=years, months=months
            )
        except Exception as exc:  # noqa: BLE001 - stale climatology is safer than losing evidence
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached climatology; live lookup failed.")
                return stale_result
            return self._error(candidate.place_name, now, f"Climatology lookup failed: {exc}")

        period_label = f"{first_year}-{today.year - 1} climatology for months {', '.join(map(str, months))}"
        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data=normalized_data,
            source_name="Open-Meteo historical weather archive",
            source_url=SOURCE_URL,
            retrieved_at=now,
            data_date=period_label,
            confidence=confidence,
            warnings=warnings,
        )
        await self._cache.set(cache_name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
