"""Structured, source-visible cost context for destination comparison."""

from __future__ import annotations

import asyncio
import math
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.http_client import JsonHttpClient

CITY_PRICE_URL = "https://getwherenext.com/api/data/city-prices"
COUNTRY_COST_URL = "https://getwherenext.com/api/data/cost-of-living"
FRANKFURTER_RATE_ROOT = "https://api.frankfurter.dev/v2/rate"
PROVIDER_CONTRACT_VERSION = 1

_CITY_ALIASES = {
    "lisboa": "lisbon",
    "mexico d f": "mexico city",
    "munchen": "munich",
    "muenchen": "munich",
    "praha": "prague",
    "saigon": "ho chi minh city",
}
_SCENARIO_ITEMS = {
    "one_bedroom_center": "1 bedroom apartment center",
    "one_bedroom_outside": "1 bedroom apartment outside",
    "utilities": "electricity water garbage",
    "internet": "internet",
    "monthly_transit": "monthly transit pass",
}


@dataclass(frozen=True)
class CachedPayload:
    payload: dict[str, Any]
    stale: bool


@dataclass(frozen=True)
class BudgetComparison:
    status: str
    original_amount: float | None
    original_currency: str | None
    period: str
    comparison_amount: float | None = None
    comparison_currency: str | None = None
    fx: CachedPayload | None = None
    warning: str | None = None

    def normalized_data(self) -> dict[str, Any]:
        data = {
            "status": self.status,
            "original_amount": self.original_amount,
            "original_currency": self.original_currency,
            "period": self.period,
            "comparison_amount": self.comparison_amount,
            "comparison_currency": self.comparison_currency,
        }
        if self.fx is not None:
            data["exchange_rate"] = self.fx.payload
            data["exchange_rate_stale"] = self.fx.stale
        return data


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text.casefold()))


def _normalized_city(value: str) -> str:
    normalized = _normalize_text(value)
    return _CITY_ALIASES.get(normalized, normalized)


def _finite_nonnegative(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return number


def _required_text(mapping: dict[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing or invalid {field}")
    return value.strip()


def _currency_code(value: str | None) -> str | None:
    normalized = value.strip().upper() if value else None
    return normalized if normalized and re.fullmatch(r"[A-Z]{3}", normalized) else None


def _dataset_metadata(raw: Any, *, key: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Provider metadata must be an object")
    metadata = {
        "title": _required_text(raw, "title"),
        "version": _required_text(raw, "version"),
        "source": _required_text(raw, "source"),
        "license": _required_text(raw, "license"),
        "updated": _required_text(raw, "updated"),
        "methodology": _required_text(raw, "methodology"),
    }
    for optional in (
        "releases",
        "report",
        "csv",
        "api_docs",
        "data_source",
        "city",
        "currency",
    ):
        if raw.get(optional) is not None:
            metadata[optional] = raw[optional]
    for optional_number in ("total_cities", "countries", "exchange_rate"):
        if raw.get(optional_number) is not None:
            metadata[optional_number] = _finite_nonnegative(raw[optional_number], optional_number)
    if key == "city" and _currency_code(str(metadata.get("currency") or "")) is None:
        raise ValueError("City price metadata has no valid currency")
    return metadata


def parse_coverage(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("WhereNext returned malformed city coverage")
    metadata = _dataset_metadata(payload.get("metadata"), key="coverage")
    cities = []
    invalid = 0
    for raw in payload["data"]:
        try:
            if not isinstance(raw, dict):
                raise ValueError("city entry is not an object")
            country_code = _required_text(raw, "country_code").upper()
            if len(country_code) != 2:
                raise ValueError("invalid country code")
            cities.append(
                {
                    "city_key": _required_text(raw, "city_key"),
                    "city_name": _required_text(raw, "city_name"),
                    "country_code": country_code,
                    "item_count": int(_finite_nonnegative(raw.get("item_count"), "item_count")),
                    "categories": int(_finite_nonnegative(raw.get("categories"), "categories")),
                }
            )
        except (TypeError, ValueError):
            invalid += 1
    if not cities:
        raise ValueError("WhereNext city coverage contains no valid cities")
    return {"metadata": metadata, "cities": cities, "invalid_city_count": invalid}


def parse_city_prices(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("WhereNext returned malformed city prices")
    metadata = _dataset_metadata(payload.get("metadata"), key="city")
    prices = []
    invalid = 0
    for raw in payload["data"]:
        try:
            if not isinstance(raw, dict):
                raise ValueError("price entry is not an object")
            prices.append(
                {
                    "category": _required_text(raw, "category"),
                    "item": _required_text(raw, "item"),
                    "price_local": _finite_nonnegative(raw.get("price_local"), "price_local"),
                    "price_usd": _finite_nonnegative(raw.get("price_usd"), "price_usd"),
                }
            )
        except ValueError:
            invalid += 1
    if not prices:
        raise ValueError("WhereNext city response contains no valid prices")
    return {"metadata": metadata, "prices": prices, "invalid_price_count": invalid}


def parse_country_costs(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("WhereNext returned malformed country costs")
    metadata = _dataset_metadata(payload.get("meta"), key="country")
    countries = []
    invalid = 0
    for raw in payload["data"]:
        try:
            if not isinstance(raw, dict):
                raise ValueError("country entry is not an object")
            country_code = _required_text(raw, "country_code").upper()
            if len(country_code) != 2:
                raise ValueError("invalid country code")
            countries.append(
                {
                    "country_code": country_code,
                    "country": _required_text(raw, "country"),
                    "region": str(raw.get("region") or ""),
                    "rank": int(_finite_nonnegative(raw.get("rank"), "rank")),
                    "cost_index": _finite_nonnegative(raw.get("cost_index"), "cost_index"),
                    "monthly_estimate_usd": _finite_nonnegative(
                        raw.get("monthly_estimate_usd"), "monthly_estimate_usd"
                    ),
                    "grocery_index": _finite_nonnegative(raw.get("grocery_index"), "grocery_index"),
                    "rent_index": _finite_nonnegative(raw.get("rent_index"), "rent_index"),
                    "utilities_index": _finite_nonnegative(raw.get("utilities_index"), "utilities_index"),
                    "transport_index": _finite_nonnegative(raw.get("transport_index"), "transport_index"),
                }
            )
        except (TypeError, ValueError):
            invalid += 1
    if not countries:
        raise ValueError("WhereNext country response contains no valid countries")
    return {"metadata": metadata, "countries": countries, "invalid_country_count": invalid}


def parse_exchange_rate(payload: Any, base: str, quote_currency: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Frankfurter returned a non-object response")
    returned_base = _required_text(payload, "base").upper()
    returned_quote = _required_text(payload, "quote").upper()
    if returned_base != base or returned_quote != quote_currency:
        raise ValueError("Frankfurter returned an unexpected currency pair")
    rate = _finite_nonnegative(payload.get("rate"), "rate")
    if rate == 0:
        raise ValueError("Frankfurter returned a zero exchange rate")
    return {
        "date": _required_text(payload, "date"),
        "base": returned_base,
        "quote": returned_quote,
        "rate": rate,
    }


class BudgetDataClient:
    """Cache and coalesce shared provider data across concurrent candidates."""

    def __init__(self, cache: ToolCache, http: JsonHttpClient):
        self._cache = cache
        self._http = http
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._waiters: dict[str, int] = {}
        self._inflight_lock = asyncio.Lock()

    async def _coalesce(self, key: str, factory: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        async with self._inflight_lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(factory())
                self._inflight[key] = task
            self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            return await asyncio.shield(task)
        finally:
            async with self._inflight_lock:
                self._waiters[key] -= 1
                if self._waiters[key] == 0:
                    if not task.done():
                        task.cancel()
                    self._waiters.pop(key, None)
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)

    async def _get(
        self,
        *,
        cache_name: str,
        place: str,
        params: dict[str, Any],
        ttl_key: str,
        url: str,
        request_params: dict[str, Any] | None,
        parser: Callable[[Any], dict[str, Any]],
    ) -> CachedPayload:
        cached, stale = await self._cache.get(cache_name, place, params, ttl_key=ttl_key)
        if cached is not None and not stale:
            return CachedPayload(cached, False)

        async def fetch() -> dict[str, Any]:
            raw = await self._http.get_json(url, params=request_params)
            parsed = parser(raw)
            await self._cache.set(cache_name, place, params, parsed, ttl_key=ttl_key)
            return parsed

        key = f"{cache_name}:{place}:{sorted(params.items())}"
        try:
            return CachedPayload(await self._coalesce(key, fetch), False)
        except Exception:
            if cached is not None:
                return CachedPayload(cached, True)
            raise

    async def coverage(self) -> CachedPayload:
        return await self._get(
            cache_name="BudgetFitTool:coverage",
            place="all-cities",
            params={"contract_version": PROVIDER_CONTRACT_VERSION},
            ttl_key="BudgetFitTool:coverage",
            url=CITY_PRICE_URL,
            request_params=None,
            parser=parse_coverage,
        )

    async def city_prices(self, city_key: str) -> CachedPayload:
        return await self._get(
            cache_name="BudgetFitTool:city_prices",
            place=city_key,
            params={"contract_version": PROVIDER_CONTRACT_VERSION},
            ttl_key="BudgetFitTool:city_prices",
            url=CITY_PRICE_URL,
            request_params={"city": city_key},
            parser=parse_city_prices,
        )

    async def country_costs(self) -> CachedPayload:
        return await self._get(
            cache_name="BudgetFitTool:country_costs",
            place="all-countries",
            params={"contract_version": PROVIDER_CONTRACT_VERSION},
            ttl_key="BudgetFitTool:country_costs",
            url=COUNTRY_COST_URL,
            request_params=None,
            parser=parse_country_costs,
        )

    async def exchange_rate(self, base: str, quote_currency: str = "USD") -> CachedPayload:
        url = f"{FRANKFURTER_RATE_ROOT}/{base}/{quote_currency}"
        return await self._get(
            cache_name="BudgetFitTool:exchange_rate",
            place=f"{base}-{quote_currency}",
            params={"contract_version": PROVIDER_CONTRACT_VERSION},
            ttl_key="BudgetFitTool:exchange_rate",
            url=url,
            request_params=None,
            parser=lambda payload: parse_exchange_rate(payload, base, quote_currency),
        )


def resolve_city_key(coverage: dict[str, Any], candidate: CandidatePlace) -> str | None:
    country_code = (candidate.country_code or "").strip().upper()
    if len(country_code) != 2:
        return None
    names = {_normalized_city(candidate.place_name)}
    if candidate.canonical_name:
        names.add(_normalized_city(candidate.canonical_name))
    matches = [
        city
        for city in coverage["cities"]
        if city["country_code"] == country_code and _normalized_city(city["city_name"]) in names
    ]
    return matches[0]["city_key"] if len(matches) == 1 else None


def _find_price(prices: list[dict[str, Any]], normalized_name: str, *, prefix: bool = False) -> dict | None:
    matches = []
    for price in prices:
        normalized = _normalize_text(price["item"])
        matched = normalized.startswith(normalized_name) if prefix else normalized == normalized_name
        if matched:
            matches.append(price)
    return matches[0] if len(matches) == 1 else None


def build_fixed_cost_scenarios(
    prices: list[dict[str, Any]],
    local_currency: str,
    comparison: BudgetComparison,
) -> tuple[dict[str, Any], list[str]]:
    found = {
        "one_bedroom_center": _find_price(prices, _SCENARIO_ITEMS["one_bedroom_center"]),
        "one_bedroom_outside": _find_price(prices, _SCENARIO_ITEMS["one_bedroom_outside"]),
        "utilities": _find_price(prices, _SCENARIO_ITEMS["utilities"], prefix=True),
        "internet": _find_price(prices, _SCENARIO_ITEMS["internet"], prefix=True),
        "monthly_transit": _find_price(prices, _SCENARIO_ITEMS["monthly_transit"]),
    }
    common_names = ("utilities", "internet", "monthly_transit")
    scenarios: dict[str, Any] = {}
    missing: list[str] = []
    for label, housing_name in (("center", "one_bedroom_center"), ("outside_center", "one_bedroom_outside")):
        item_names = (housing_name, *common_names)
        absent = [name for name in item_names if found[name] is None]
        if absent:
            missing.extend(absent)
            continue
        included = [found[name] for name in item_names]
        total_local = round(sum(item["price_local"] for item in included if item), 2)
        total_usd = round(sum(item["price_usd"] for item in included if item), 2)
        budget_remaining = None
        if comparison.comparison_amount is not None:
            if comparison.comparison_currency == local_currency:
                budget_remaining = {
                    "amount": round(comparison.comparison_amount - total_local, 2),
                    "currency": local_currency,
                }
            elif comparison.comparison_currency == "USD":
                budget_remaining = {
                    "amount": round(comparison.comparison_amount - total_usd, 2),
                    "currency": "USD",
                }
        scenarios[label] = {
            "monthly_total_local": total_local,
            "local_currency": local_currency,
            "monthly_total_usd": total_usd,
            "included_items": included,
            "budget_remaining_after_named_items": budget_remaining,
        }
    return scenarios, sorted(set(missing))


def _exception_text(exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class BudgetFitTool:
    name = "BudgetFitTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 10.0,
        *,
        http: JsonHttpClient | None = None,
        data_client: BudgetDataClient | None = None,
    ) -> None:
        self._cache = cache
        self._http = http or JsonHttpClient(timeout=timeout)
        self._data = data_client or BudgetDataClient(cache, self._http)

    async def _budget_comparison(
        self,
        profile: PlaceRequestProfile,
        direct_currency: str,
    ) -> BudgetComparison:
        budget = profile.budget
        original_currency = budget.currency.strip().upper() if budget.currency else None
        currency = _currency_code(original_currency)
        if budget.amount is None:
            return BudgetComparison("not_provided", None, original_currency, budget.period)
        if not math.isfinite(budget.amount) or budget.amount <= 0:
            return BudgetComparison(
                "invalid_amount",
                budget.amount,
                original_currency,
                budget.period,
                warning="The stated budget amount must be finite and greater than zero.",
            )
        if budget.period != "monthly":
            return BudgetComparison(
                "unsupported_period",
                budget.amount,
                original_currency,
                budget.period,
                warning="Fixed-cost scenarios are monthly and cannot be compared with a non-monthly budget.",
            )
        if budget.includes_accommodation is False:
            return BudgetComparison(
                "excludes_accommodation",
                budget.amount,
                original_currency,
                budget.period,
                warning=(
                    "The stated budget excludes accommodation, so it cannot be compared with rent-inclusive "
                    "fixed-cost scenarios."
                ),
            )
        if currency is None:
            return BudgetComparison(
                "missing_currency",
                budget.amount,
                original_currency,
                budget.period,
                warning="The stated budget has no valid three-letter currency code.",
            )
        if currency in {direct_currency, "USD"}:
            return BudgetComparison(
                "comparable_without_conversion",
                budget.amount,
                original_currency,
                budget.period,
                comparison_amount=budget.amount,
                comparison_currency=currency,
            )
        try:
            fx = await self._data.exchange_rate(currency)
        except Exception as exc:  # noqa: BLE001 - cost evidence remains usable without conversion
            return BudgetComparison(
                "conversion_unavailable",
                budget.amount,
                original_currency,
                budget.period,
                warning=f"Budget currency conversion is unavailable: {_exception_text(exc)}",
            )
        return BudgetComparison(
            "converted_to_usd",
            budget.amount,
            original_currency,
            budget.period,
            comparison_amount=round(budget.amount * fx.payload["rate"], 2),
            comparison_currency="USD",
            fx=fx,
        )

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        now = datetime.now(UTC)
        country_code = (candidate.country_code or "").strip().upper()
        warnings = [
            "Cost evidence is decision support, not a complete or guaranteed personal monthly budget.",
            "The final affordability judgment is unresolved until the reasoning agent assesses the visible basket.",
        ]

        coverage: CachedPayload | None = None
        coverage_error: BaseException | None = None
        city_key: str | None = None
        city_data: CachedPayload | None = None
        try:
            coverage = await self._data.coverage()
            city_key = resolve_city_key(coverage.payload, candidate)
            if city_key:
                city_data = await self._data.city_prices(city_key)
        except Exception as exc:  # noqa: BLE001 - country fallback remains useful
            coverage_error = exc

        country_data: CachedPayload | None = None
        country_error: BaseException | None = None
        if city_data is None:
            try:
                country_data = await self._data.country_costs()
            except Exception as exc:  # noqa: BLE001 - converted to an honest missing result
                country_error = exc

        if city_data is None and country_data is None:
            errors = [error for error in (coverage_error, country_error) if error is not None]
            detail = "; ".join(_exception_text(error) for error in errors) or "No provider coverage"
            return ToolResult(
                tool_name=self.name,
                place=candidate.place_name,
                source_name="WhereNext cost datasets",
                retrieved_at=now,
                confidence="low",
                warnings=warnings,
                error=f"No cost evidence is available for this destination: {detail}",
            )

        if coverage_error:
            warnings.append(f"City-price lookup failed; using country context: {_exception_text(coverage_error)}")
        elif city_key is None:
            warnings.append("No exact verified city-price match was found; using country-level context.")
        if coverage is not None and coverage.stale:
            warnings.append("City coverage was resolved from expired cached provider data.")
        if coverage is not None and coverage.payload.get("invalid_city_count"):
            warnings.append(
                f"Ignored {coverage.payload['invalid_city_count']} malformed city coverage entry or entries."
            )

        evidence_items: list[EvidenceItem] = []
        if city_data is not None:
            metadata = city_data.payload["metadata"]
            local_currency = str(metadata["currency"]).upper()
            comparison = await self._budget_comparison(profile, local_currency)
            scenarios, missing_scenario_items = build_fixed_cost_scenarios(
                city_data.payload["prices"], local_currency, comparison
            )
            if comparison.warning:
                warnings.append(comparison.warning)
            if missing_scenario_items:
                warnings.append(
                    "Some fixed-cost scenarios are unavailable because named items are missing: "
                    + ", ".join(missing_scenario_items)
                    + "."
                )
            if city_data.payload["invalid_price_count"]:
                warnings.append(
                    f"Ignored {city_data.payload['invalid_price_count']} malformed city-price item(s)."
                )
            if city_data.stale:
                warnings.append("Live city prices failed; returning expired cached city-price evidence.")
            if comparison.fx is not None and comparison.fx.stale:
                warnings.append("Live exchange rates failed; the budget conversion uses an expired cached rate.")
            city_source_url = f"{CITY_PRICE_URL}?city={quote(city_key or '', safe='')}"
            primary_warnings = [
                "WhereNext is a third-party researched/modelled dataset; individual prices may not match "
                "a user's needs."
            ]
            evidence_items.append(
                EvidenceItem(
                    criterion="cost",
                    component="city_price_basket",
                    normalized_data={
                        "evidence_level": "city",
                        "city_key": city_key,
                        "dataset_metadata": metadata,
                        "prices": city_data.payload["prices"],
                        "fixed_cost_scenarios": scenarios,
                        "missing_fixed_cost_items": missing_scenario_items,
                    },
                    source=EvidenceSource(
                        source_name=str(metadata["source"]),
                        source_url=city_source_url,
                        retrieved_at=now,
                        data_date=str(metadata["updated"]),
                        confidence="low" if city_data.stale else "medium",
                        stale=city_data.stale,
                    ),
                    warnings=primary_warnings,
                )
            )
            if comparison.fx is not None:
                fx = comparison.fx
                evidence_items.append(
                    EvidenceItem(
                        criterion="cost",
                        component="budget_exchange_rate",
                        value=fx.payload["rate"],
                        normalized_data=fx.payload,
                        source=EvidenceSource(
                            source_name="Frankfurter exchange-rate API",
                            source_url=(
                                f"{FRANKFURTER_RATE_ROOT}/{fx.payload['base']}/{fx.payload['quote']}"
                            ),
                            retrieved_at=now,
                            data_date=fx.payload["date"],
                            confidence="low" if fx.stale else "high",
                            stale=fx.stale,
                        ),
                        warnings=[
                            "The exchange rate converts the stated budget only; it does not predict future rates."
                        ],
                    )
                )
            normalized_data = {
                "evidence_level": "city",
                "resolved_city_key": city_key,
                "dataset_metadata": metadata,
                "price_basket": city_data.payload["prices"],
                "fixed_cost_scenarios": scenarios,
                "missing_fixed_cost_items": missing_scenario_items,
                "budget_context": {
                    **comparison.normalized_data(),
                    "includes_accommodation": profile.budget.includes_accommodation,
                },
                "scoring_status": "unresolved_pending_llm",
            }
            stale = city_data.stale or bool(comparison.fx and comparison.fx.stale)
            confidence: Literal["medium", "low"] = "low" if stale else "medium"
            source_name = str(metadata["source"])
            source_url = city_source_url
            data_date = str(metadata["updated"])
        else:
            assert country_data is not None
            metadata = country_data.payload["metadata"]
            country_row = next(
                (
                    row
                    for row in country_data.payload["countries"]
                    if row["country_code"] == country_code
                ),
                None,
            )
            if country_row is None:
                return ToolResult(
                    tool_name=self.name,
                    place=candidate.place_name,
                    source_name=str(metadata["source"]),
                    source_url=COUNTRY_COST_URL,
                    retrieved_at=now,
                    data_date=str(metadata["updated"]),
                    confidence="low",
                    warnings=warnings,
                    error="No city or country cost evidence is available for this destination.",
                )
            comparison = await self._budget_comparison(profile, "USD")
            if comparison.warning:
                warnings.append(comparison.warning)
            if country_data.payload["invalid_country_count"]:
                warnings.append(
                    f"Ignored {country_data.payload['invalid_country_count']} malformed country cost entry or entries."
                )
            if country_data.stale:
                warnings.append("Live country costs failed; returning expired cached country-level evidence.")
            if comparison.fx is not None and comparison.fx.stale:
                warnings.append("Live exchange rates failed; the budget conversion uses an expired cached rate.")
            evidence_items.append(
                EvidenceItem(
                    criterion="cost",
                    component="country_cost_context",
                    normalized_data={
                        "evidence_level": "country",
                        "dataset_metadata": metadata,
                        "country_context": country_row,
                    },
                    source=EvidenceSource(
                        source_name=str(metadata["source"]),
                        source_url=COUNTRY_COST_URL,
                        retrieved_at=now,
                        data_date=str(metadata["updated"]),
                        confidence="low",
                        stale=country_data.stale,
                    ),
                    warnings=[
                        "This is country-level researched/modelled context and must not be presented as "
                        "a city estimate.",
                        "Provider index directions are preserved without interpretation inside this tool.",
                    ],
                )
            )
            if comparison.fx is not None:
                fx = comparison.fx
                evidence_items.append(
                    EvidenceItem(
                        criterion="cost",
                        component="budget_exchange_rate",
                        value=fx.payload["rate"],
                        normalized_data=fx.payload,
                        source=EvidenceSource(
                            source_name="Frankfurter exchange-rate API",
                            source_url=(
                                f"{FRANKFURTER_RATE_ROOT}/{fx.payload['base']}/{fx.payload['quote']}"
                            ),
                            retrieved_at=now,
                            data_date=fx.payload["date"],
                            confidence="low" if fx.stale else "high",
                            stale=fx.stale,
                        ),
                    )
                )
            normalized_data = {
                "evidence_level": "country",
                "dataset_metadata": metadata,
                "country_context": country_row,
                "price_basket": [],
                "fixed_cost_scenarios": {},
                "budget_context": {
                    **comparison.normalized_data(),
                    "includes_accommodation": profile.budget.includes_accommodation,
                },
                "scoring_status": "unresolved_pending_llm",
            }
            stale = country_data.stale or bool(comparison.fx and comparison.fx.stale)
            confidence = "low"
            source_name = str(metadata["source"])
            source_url = COUNTRY_COST_URL
            data_date = str(metadata["updated"])

        return ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data=normalized_data,
            source_name=source_name,
            source_url=source_url,
            retrieved_at=now,
            data_date=data_date,
            confidence=confidence,
            stale=stale,
            warnings=warnings,
            evidence_items=evidence_items,
        )
