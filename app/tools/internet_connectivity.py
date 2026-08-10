"""Internet speed for a destination: measured medians, not a wealth proxy.

The single thing a remote worker chooses on, and until 2026-08-10 nothing
measured it. P01 states "fast, reliable internet" as its top priority after
budget, and the shipped answer said internet quality "is not directly
established" for seven of its eight candidates. `AmenitiesTool` counted
coworking spaces and cafes -- a different question: a city can have forty cafes
and unusable upstream bandwidth.

**Scored from Ookla median download speeds**, read from Wikipedia's
country-speed table via the MediaWiki API this project already uses, and pinned
to the revision it came from.

The first version of this tool scored World Bank *fixed-broadband subscriptions
per 100 people* instead, on the reasoning that measured Mbps had no free keyless
source. That produced an inverted ranking and was caught by the user:

    country     Ookla median   world rank   subscriptions-per-100 score
    Romania     283.06 Mbit/s          11   0.91
    Thailand    279.65 Mbit/s          14   0.59   <- scored worst
    Portugal    243.84 Mbit/s          22   0.95   <- scored best

Subscriptions per 100 people measures *household penetration*, which tracks GDP
and housing patterns rather than connection quality. Thailand has cheap fast
fibre and fewer lines per capita, so the proxy is biased against exactly the
destinations digital nomads pick -- Chiang Mai, Bali, Medellin, Tbilisi. The
World Bank indicators are kept only as a fallback for countries the speed table
does not list, and are labelled as adoption rather than speed when they are used.

Still national, not city-level, and the answer says so -- the treatment
`BudgetFitTool` already gives country-level cost estimates.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.http_client import JsonHttpClient

WORLD_BANK_API_ROOT = "https://api.worldbank.org/v2/country"
USERS_INDICATOR = "IT.NET.USER.ZS"
USERS_INDICATOR_NAME = "Individuals using the Internet (% of population)"

_TABLE_CACHE_KEY = "__country_speed_table__"

SPEED_PAGE = "List of countries by Internet connection speeds"
SPEED_SOURCE_NAME = "Ookla Speedtest Global Index, via Wikipedia"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
# Sanity floor on the parsed table. Wikipedia markup changes; a parser that
# silently returns three countries would score everything else off the World
# Bank fallback without anyone noticing.
MIN_PARSED_COUNTRIES = 80

# Mbit/s where the measure stops mattering to a remote worker. Below ~10 a video
# call is a struggle; by ~150 the bottleneck is somewhere else. Deliberately
# coarse: these are national medians and a finer curve would imply a precision
# they do not have.
SPEED_FLOOR_MBPS = 10.0
SPEED_SATURATION_MBPS = 150.0

# Fallback only, for countries the speed table does not list. Adoption share,
# not speed -- see the module docstring for why penetration was a bad proxy.
USERS_SATURATION = 95.0
USERS_FLOOR = 40.0


# What a median download speed means for someone working from the place, in
# terms of what they will actually be doing. A reader should not have to know
# what a megabit is: "45 Mbit/s" says nothing on its own, and a 0-1 score says
# less (and never reaches the prose anyway -- D41).
#
# Thresholds are the points where the experience changes, not round numbers:
# video calls need a few Mbit/s each way, screen sharing a little more, and
# pushing a large build or video is where a slow uplink starts to hurt.
SPEED_BANDS: tuple[tuple[float, str], ...] = (
    (10.0, "too slow to rely on for video calls"),
    (25.0, "enough for video calls, though large uploads will drag"),
    (100.0, "comfortable for everyday remote work, including video calls and screen sharing"),
    (250.0, "fast enough for heavy uploads and several devices at once"),
    (float("inf"), "among the fastest measured anywhere"),
)


def speed_band(median_download_mbps: float | None) -> str | None:
    """Plain-language meaning of a median download speed, or None if unknown."""
    if not isinstance(median_download_mbps, int | float) or isinstance(median_download_mbps, bool):
        return None
    for ceiling, description in SPEED_BANDS:
        if median_download_mbps < ceiling:
            return description
    return SPEED_BANDS[-1][1]


def speed_ranking(speeds: dict[str, float], country: str) -> tuple[int, int] | None:
    """(rank, countries measured) for a country, best first.

    Calibration the reader can use without knowing what a megabit is: "44 Mbit/s"
    means little, "78th of 153 countries measured" means something. Free, because
    the whole table is already fetched.
    """
    value = speeds.get(country)
    if value is None:
        return None
    rank = sum(1 for other in speeds.values() if other > value) + 1
    return rank, len(speeds)


def speed_score(median_download_mbps: float | None) -> float | None:
    """Median download speed to [0,1], or None when it is unknown."""
    if not isinstance(median_download_mbps, int | float) or isinstance(median_download_mbps, bool):
        return None
    span = SPEED_SATURATION_MBPS - SPEED_FLOOR_MBPS
    return round(min(1.0, max(0.0, (median_download_mbps - SPEED_FLOOR_MBPS) / span)), 4)


def adoption_score(internet_users_pct: float | None) -> float | None:
    """Fallback signal when no speed figure exists. Weaker, and labelled so."""
    if not isinstance(internet_users_pct, int | float) or isinstance(internet_users_pct, bool):
        return None
    span = USERS_SATURATION - USERS_FLOOR
    return round(min(1.0, max(0.0, (internet_users_pct - USERS_FLOOR) / span)), 4)


def parse_speed_table(wikitext: str) -> dict[str, float]:
    """Country -> median fixed-broadband download in Mbit/s.

    Reads only the "Fixed broadband" section: the mobile table below it answers
    a different question, and merging them would average a phone tether into a
    figure about working from an apartment.
    """
    if "== Fixed broadband ==" not in wikitext:
        return {}
    section = wikitext.split("== Fixed broadband ==", 1)[1].split("\n==", 1)[0]
    speeds: dict[str, float] = {}
    for country, value in re.findall(r"\{\{flagcountry\|([^}|]+)\}\}\s*\|\|\s*([\d.]+)", section):
        name = country.strip()
        if name and name not in speeds:
            try:
                speeds[name] = float(value)
            except ValueError:
                continue
    return speeds


class InternetConnectivityTool:
    name = "InternetConnectivityTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 10.0,
        http: JsonHttpClient | None = None,
    ):
        self._cache = cache
        self._http = http or JsonHttpClient(timeout=timeout)

    async def _indicator(self, country_code: str, indicator: str) -> dict[str, Any]:
        """Latest non-null observation for one World Bank indicator."""
        url = f"{WORLD_BANK_API_ROOT}/{country_code}/indicator/{indicator}"
        payload = await self._http.get_json(url, params={"format": "json", "per_page": 100})
        if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
            raise ValueError(f"World Bank returned a malformed response for {indicator}")
        observations = [
            item
            for item in payload[1]
            if isinstance(item, dict) and item.get("value") is not None
        ]
        if not observations:
            raise ValueError(f"World Bank has no observation for {indicator} in {country_code}")
        latest = max(observations, key=lambda item: str(item.get("date") or ""))
        return {
            "value": float(latest["value"]),
            "year": str(latest.get("date")),
            "country": (latest.get("country") or {}).get("value"),
            "indicator_code": indicator,
            "source_url": f"{url}?format=json&per_page=100",
        }

    async def _speed_table(self) -> dict[str, Any]:
        """The whole country table, fetched once and shared by every candidate.

        Cached under a synthetic place key because it is not about any one
        destination -- eight finalists would otherwise pull the same page eight
        times.
        """
        params = {"page": SPEED_PAGE, "table": "fixed_broadband"}
        cached, stale = await self._cache.get(self.name, _TABLE_CACHE_KEY, params)
        if cached is not None and not stale:
            return cached

        payload = await self._http.get_json(
            WIKIPEDIA_API,
            params={
                "action": "parse",
                "page": SPEED_PAGE,
                "prop": "wikitext|revid",
                "format": "json",
                "formatversion": "2",
            },
        )
        parse = payload.get("parse") or {}
        speeds = parse_speed_table(str(parse.get("wikitext") or ""))
        if len(speeds) < MIN_PARSED_COUNTRIES:
            raise ValueError(
                f"The speed table parsed to {len(speeds)} countries, below the "
                f"{MIN_PARSED_COUNTRIES} expected -- the page markup has probably changed"
            )
        revision_id = parse.get("revid")
        table = {
            "speeds": speeds,
            "revision_id": revision_id,
            "source_url": (
                f"https://en.wikipedia.org/w/index.php?oldid={revision_id}"
                if revision_id
                else "https://en.wikipedia.org/wiki/List_of_countries_by_Internet_connection_speeds"
            ),
        }
        await self._cache.set(self.name, _TABLE_CACHE_KEY, params, table)
        return table

    def _error(self, place: str, message: str, now: datetime) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            place=place,
            source_name="World Bank",
            retrieved_at=now,
            confidence="low",
            error=message,
        )

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        del profile  # Connectivity is a property of the place, not of the request.
        now = datetime.now(UTC)
        country_code = (candidate.country_code or "").strip().upper()
        if len(country_code) != 2:
            return self._error(
                candidate.place_name,
                "A verified ISO alpha-2 country code is required for connectivity evidence.",
                now,
            )

        params = {
            "country_code": country_code,
            "indicators": [USERS_INDICATOR],
        }
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        outcomes = await asyncio.gather(
            self._speed_table(),
            self._indicator(country_code, USERS_INDICATOR),
            return_exceptions=True,
        )
        table, users = outcomes
        warnings: list[str] = []

        users_data = users if isinstance(users, dict) else None

        median_mbps: float | None = None
        speed_source_url: str | None = None
        speed_revision: Any = None
        ranking: tuple[int, int] | None = None
        if isinstance(table, dict):
            speeds = table.get("speeds") or {}
            country_name = (candidate.country or "").strip()
            median_mbps = speeds.get(country_name)
            ranking = speed_ranking(speeds, country_name)
            speed_source_url = table.get("source_url")
            speed_revision = table.get("revision_id")
            if median_mbps is None:
                warnings.append(
                    f"{candidate.country} is not listed in the country speed table; "
                    "falling back to internet-adoption share, which is not a speed."
                )
        else:
            warnings.append(f"The country speed table is unavailable: {table}")

        score = speed_score(median_mbps)
        basis = "median_download_speed"
        if score is None:
            score = adoption_score(users_data["value"] if users_data else None)
            basis = "internet_adoption_share" if score is not None else "none"

        if score is None:
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                return stale_result
            return self._error(
                candidate.place_name,
                "No connectivity evidence could be collected for this destination.",
                now,
            )

        if score is not None:
            warnings.append(
                "This is a national median, not a measurement of any one apartment or cafe: "
                "connections inside a city vary widely around it."
            )

        evidence_items: list[EvidenceItem] = []
        if median_mbps is not None:
            evidence_items.append(
                EvidenceItem(
                    criterion="internet",
                    component="median_fixed_download_mbps",
                    value=median_mbps,
                    normalized_data={
                        "median_download_mbps": median_mbps,
                        "means": speed_band(median_mbps),
                        "country_speed_rank": ranking[0] if ranking else None,
                        "countries_ranked": ranking[1] if ranking else None,
                        "country": candidate.country,
                        "measure": "median fixed-broadband download speed",
                    },
                    source=EvidenceSource(
                        source_name=SPEED_SOURCE_NAME,
                        source_url=speed_source_url,
                        retrieved_at=now,
                        data_date=(
                            f"Wikipedia revision {speed_revision}" if speed_revision else None
                        ),
                        confidence="medium",
                    ),
                )
            )
        elif users_data is not None:
            evidence_items.append(
                EvidenceItem(
                    criterion="internet",
                    component=users_data["indicator_code"],
                    value=users_data["value"],
                    normalized_data={**users_data, "indicator_name": USERS_INDICATOR_NAME},
                    source=EvidenceSource(
                        source_name=f"World Bank {USERS_INDICATOR_NAME}",
                        source_url=f"https://data.worldbank.org/indicator/{USERS_INDICATOR}",
                        retrieved_at=now,
                        data_date=users_data["year"],
                        confidence="low",
                    ),
                    warnings=[
                        "Adoption share, used because no median speed is published for this "
                        "country. It describes how many people are online, not how fast."
                    ],
                )
            )

        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "connectivity_score": score,
                "scored_from": basis,
                "median_download_mbps": median_mbps,
                # The number alone tells a reader nothing; these are what make
                # it mean something without them knowing what a megabit is.
                "speed_meaning": speed_band(median_mbps),
                "country_speed_rank": ranking[0] if ranking else None,
                "countries_ranked": ranking[1] if ranking else None,
                "speed_source_url": speed_source_url,
                "internet_users_pct": users_data["value"] if users_data else None,
                "internet_users_year": users_data["year"] if users_data else None,
                "country": candidate.country,
                "country_code": country_code,
                "evidence_level": "country" if score is not None else "none",
            },
            source_name=SPEED_SOURCE_NAME if median_mbps is not None else "World Bank",
            source_url=speed_source_url,
            retrieved_at=now,
            confidence="medium" if score is not None else "low",
            warnings=warnings,
            evidence_items=evidence_items,
        )
        await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
