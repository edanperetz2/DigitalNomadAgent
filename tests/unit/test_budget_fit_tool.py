import asyncio

import pytest

from app.agent.models import Budget, CandidatePlace, PlaceRequestProfile
from app.tools.budget_fit import (
    BudgetComparison,
    BudgetDataClient,
    BudgetFitTool,
    _first_scope_comparison,
    build_fixed_cost_scenarios,
    parse_city_prices,
    parse_country_costs,
    parse_coverage,
    parse_exchange_rate,
    resolve_city_key,
)


class FakeCache:
    def __init__(self):
        self.values = {}
        self.stale_keys = set()
        self.set_calls = []

    @staticmethod
    def _key(tool_name, place, params):
        return tool_name, place, tuple(sorted(params.items()))

    async def get(self, tool_name, place, params, ttl_key=None):
        key = self._key(tool_name, place, params)
        return self.values.get(key), key in self.stale_keys

    async def set(self, tool_name, place, params, response, ttl_key=None):
        key = self._key(tool_name, place, params)
        self.values[key] = response
        self.stale_keys.discard(key)
        self.set_calls.append((tool_name, place, ttl_key))

    def seed(self, tool_name, place, params, response, *, stale=False):
        key = self._key(tool_name, place, params)
        self.values[key] = response
        if stale:
            self.stale_keys.add(key)


class FakeHttp:
    def __init__(self, *, coverage=None, city=None, country=None, fx=None, errors=None, delay=0):
        self.coverage = coverage if coverage is not None else _coverage_payload()
        self.city = city if city is not None else _city_payload()
        self.country = country if country is not None else _country_payload()
        self.fx = fx if fx is not None else _fx_payload()
        self.errors = errors or {}
        self.delay = delay
        self.calls = []

    async def get_json(self, url, *, params=None, **kwargs):
        self.calls.append((url, params))
        if self.delay:
            await asyncio.sleep(self.delay)
        if "frankfurter" in url:
            key, payload = "fx", self.fx
        elif "cost-of-living" in url:
            key, payload = "country", self.country
        elif params and params.get("city"):
            key, payload = "city", self.city
        else:
            key, payload = "coverage", self.coverage
        if key in self.errors:
            raise self.errors[key]
        return payload


def _metadata(**overrides):
    data = {
        "title": "City-Level Cost of Living Prices (2026)",
        "version": "2026.1",
        "source": "WhereNext City Price Dataset",
        "license": "CC BY 4.0",
        "updated": "2026-04-27",
        "releases": "https://getwherenext.com/data/releases",
        "methodology": "https://getwherenext.com/methodology",
        "total_cities": 57,
        "api_docs": "https://getwherenext.com/data",
    }
    data.update(overrides)
    return data


def _coverage_payload(cities=None):
    return {
        "metadata": _metadata(),
        "data": cities
        if cities is not None
        else [
            {
                "city_key": "PT-Lisbon",
                "city_name": "Lisbon",
                "country_code": "pt",
                "item_count": 33,
                "categories": 8,
            },
            {
                "city_key": "DE-Munich",
                "city_name": "Munich",
                "country_code": "de",
                "item_count": 22,
                "categories": 7,
            },
        ],
    }


def _prices():
    return [
        {"category": "Groceries", "item": "Loaf of bread (500g)", "price_usd": 1.3, "price_local": 1.2},
        {
            "category": "Housing",
            "item": "1-bedroom apartment, center",
            "price_usd": 1200,
            "price_local": 1104,
        },
        {
            "category": "Housing",
            "item": "1-bedroom apartment, outside",
            "price_usd": 800,
            "price_local": 736,
        },
        {"category": "Transport", "item": "Monthly transit pass", "price_usd": 44, "price_local": 40},
        {
            "category": "Utilities & Internet",
            "item": "Electricity, water, garbage (85m2)",
            "price_usd": 120,
            "price_local": 110,
        },
        {
            "category": "Utilities & Internet",
            "item": "Internet (60+ Mbps)",
            "price_usd": 35,
            "price_local": 32,
        },
    ]


def _city_payload(prices=None):
    return {
        "metadata": _metadata(
            title="Item-Level Prices in Lisbon (2026)",
            updated="2026-01-15",
            city="PT-Lisbon",
            currency="EUR",
            data_source="INE (Statistics Portugal), Eurostat",
            exchange_rate=0.92,
        ),
        "data": prices if prices is not None else _prices(),
    }


def _country_payload(rows=None):
    return {
        "meta": _metadata(title="2026 Cost of Living Index", source="WhereNext (getwherenext.com)"),
        "data": rows
        if rows is not None
        else [
            {
                "rank": 64,
                "country_code": "PT",
                "country": "Portugal",
                "region": "Europe",
                "cost_index": 54,
                "monthly_estimate_usd": 2000,
                "grocery_index": 41,
                "rent_index": 61,
                "utilities_index": 61,
                "transport_index": 33,
            }
        ],
    }


def _fx_payload(base="ILS", quote="USD", rate=0.33267):
    return {"date": "2026-07-15", "base": base, "quote": quote, "rate": rate}


def _candidate(**overrides):
    defaults = dict(
        place_name="Lisbon",
        country="Portugal",
        reason_for_inclusion="test",
        verified=True,
        canonical_name="Lisbon",
        country_code="PT",
        lat=38.72,
        lon=-9.14,
    )
    defaults.update(overrides)
    return CandidatePlace(**defaults)


def _profile(
    amount=1800.0,
    currency="EUR",
    period="monthly",
    includes_accommodation=True,
    budget_scope="total_living_cost",
):
    return PlaceRequestProfile(
        purpose="remote_work",
        relevant_criteria=["cost"],
        budget=Budget(
            amount=amount,
            currency=currency,
            period=period,
            budget_scope=budget_scope,
            includes_accommodation=includes_accommodation,
        ),
    )


def _tool(http=None, cache=None):
    cache = cache or FakeCache()
    http = http or FakeHttp()
    return BudgetFitTool(cache, http=http), cache, http


def test_provider_parsers_preserve_metadata_and_reject_malformed_payloads():
    assert parse_coverage(_coverage_payload())["metadata"]["version"] == "2026.1"
    city = parse_city_prices(_city_payload())
    assert city["prices"][0]["category"] == "Groceries"
    assert city["metadata"]["exchange_rate"] == 0.92
    assert parse_country_costs(_country_payload())["countries"][0]["country_code"] == "PT"
    assert parse_exchange_rate(_fx_payload(), "ILS", "USD")["rate"] == 0.33267

    with pytest.raises(ValueError):
        parse_coverage({"metadata": {}, "data": []})
    with pytest.raises(ValueError):
        parse_city_prices({"metadata": {}, "data": []})
    with pytest.raises(ValueError):
        parse_country_costs({"meta": {}, "data": []})
    with pytest.raises(ValueError, match="unexpected currency pair"):
        parse_exchange_rate(_fx_payload(base="EUR"), "ILS", "USD")


def test_city_identity_requires_country_and_supports_bounded_aliases():
    coverage = parse_coverage(_coverage_payload())

    assert resolve_city_key(coverage, _candidate(place_name="Lisboa")) == "PT-Lisbon"
    assert (
        resolve_city_key(
            coverage,
            _candidate(place_name="Muenchen", canonical_name=None, country="Germany", country_code="DE"),
        )
        == "DE-Munich"
    )
    assert (
        resolve_city_key(
            coverage,
            _candidate(place_name="München", canonical_name=None, country="Germany", country_code="DE"),
        )
        == "DE-Munich"
    )
    assert resolve_city_key(coverage, _candidate(country_code="ES")) is None
    assert resolve_city_key(coverage, _candidate(country_code=None)) is None


@pytest.mark.asyncio
async def test_city_result_returns_complete_basket_and_transparent_fixed_costs():
    tool, cache, http = _tool()

    result = await tool.run(_candidate(), _profile())

    assert result.error is None
    assert result.confidence == "medium"
    assert result.normalized_data["evidence_level"] == "city"
    assert result.normalized_data["resolved_city_key"] == "PT-Lisbon"
    assert result.normalized_data["price_basket"] == parse_city_prices(_city_payload())["prices"]
    assert result.normalized_data["dataset_metadata"]["data_source"] == "INE (Statistics Portugal), Eurostat"
    scenarios = result.normalized_data["fixed_cost_scenarios"]
    assert scenarios["center"]["monthly_total_local"] == 1286.0
    assert scenarios["center"]["budget_remaining_after_named_items"] == {
        "amount": 514.0,
        "currency": "EUR",
    }
    assert result.normalized_data["compatible_budget_comparison"]["cost_scope"] == "total_living_cost"
    assert result.normalized_data["compatible_budget_comparison"]["comparison_cost"] == {
        "amount": 1286.0,
        "currency": "EUR",
    }
    assert scenarios["outside_center"]["monthly_total_local"] == 918.0
    assert scenarios["outside_center"]["budget_remaining_after_named_items"]["amount"] == 882.0
    assert result.normalized_data["scoring_status"] == "unresolved_pending_llm"
    assert [item.component for item in result.evidence_items] == ["city_price_basket"]
    assert result.evidence_items[0].source.source_url.endswith("?city=PT-Lisbon")
    assert not any("frankfurter" in url for url, _ in http.calls)
    assert {call[0] for call in cache.set_calls} == {
        "BudgetFitTool:coverage",
        "BudgetFitTool:city_prices",
    }


@pytest.mark.asyncio
async def test_foreign_budget_is_converted_and_recorded_as_independent_evidence():
    tool, _, _ = _tool(http=FakeHttp(fx=_fx_payload(rate=0.25)))

    result = await tool.run(_candidate(), _profile(amount=4000, currency="ILS"))

    budget = result.normalized_data["budget_context"]
    assert budget["status"] == "converted_to_usd"
    assert budget["original_amount"] == 4000
    assert budget["comparison_amount"] == 1000
    assert budget["exchange_rate"]["date"] == "2026-07-15"
    assert [item.component for item in result.evidence_items] == [
        "city_price_basket",
        "budget_exchange_rate",
    ]
    fx_item = result.evidence_items[1]
    assert fx_item.value == 0.25
    assert fx_item.source.source_url.endswith("/ILS/USD")
    assert result.normalized_data["fixed_cost_scenarios"]["outside_center"][
        "budget_remaining_after_named_items"
    ] == {"amount": 1.0, "currency": "USD"}


@pytest.mark.asyncio
async def test_currency_failure_preserves_prices_but_never_compares_currencies():
    tool, _, _ = _tool(http=FakeHttp(errors={"fx": RuntimeError("rates unavailable")}))

    result = await tool.run(_candidate(), _profile(currency="ILS"))

    assert result.error is None
    assert result.normalized_data["budget_context"]["status"] == "conversion_unavailable"
    assert all(
        scenario["budget_remaining_after_named_items"] is None
        for scenario in result.normalized_data["fixed_cost_scenarios"].values()
    )
    assert [item.component for item in result.evidence_items] == ["city_price_basket"]
    assert any("conversion is unavailable" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_country_fallback_is_low_confidence_and_never_city_specific():
    tool, _, _ = _tool(http=FakeHttp(coverage=_coverage_payload(cities=[_coverage_payload()["data"][1]])))

    result = await tool.run(_candidate(), _profile(currency="USD"))

    assert result.error is None
    assert result.confidence == "low"
    assert result.normalized_data["evidence_level"] == "country"
    assert result.normalized_data["country_context"]["monthly_estimate_usd"] == 2000
    assert result.normalized_data["compatible_budget_comparison"]["comparison_cost"] == {
        "amount": 2000.0,
        "currency": "USD",
    }
    assert result.normalized_data["price_basket"] == []
    assert result.normalized_data["fixed_cost_scenarios"] == {}
    assert [item.component for item in result.evidence_items] == ["country_cost_context"]
    assert "country-level" in " ".join(result.evidence_items[0].warnings)


@pytest.mark.asyncio
async def test_city_provider_failure_degrades_to_country_context():
    tool, _, _ = _tool(http=FakeHttp(errors={"city": TimeoutError("city request timed out")}))

    result = await tool.run(_candidate(), _profile(currency="USD"))

    assert result.error is None
    assert result.normalized_data["evidence_level"] == "country"
    assert result.confidence == "low"
    assert any("City-price lookup failed" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_missing_city_and_country_coverage_returns_error_not_positive_evidence():
    tool, _, _ = _tool(
        http=FakeHttp(
            coverage=_coverage_payload(cities=[_coverage_payload()["data"][1]]),
            country=_country_payload(rows=[]),
        )
    )

    result = await tool.run(_candidate(), _profile())

    assert result.error is not None
    assert result.normalized_data == {}
    assert result.evidence_items == []


@pytest.mark.asyncio
async def test_missing_fixed_items_omit_scenario_instead_of_estimating():
    prices = [price for price in _prices() if price["item"] != "Internet (60+ Mbps)"]
    tool, _, _ = _tool(http=FakeHttp(city=_city_payload(prices=prices)))

    result = await tool.run(_candidate(), _profile())

    assert result.normalized_data["fixed_cost_scenarios"] == {}
    assert result.normalized_data["missing_fixed_cost_items"] == ["internet"]
    assert any("named items are missing" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_malformed_price_item_is_ignored_and_disclosed():
    prices = [*_prices(), {"category": "Housing", "item": "Broken", "price_usd": "unknown"}]
    tool, _, _ = _tool(http=FakeHttp(city=_city_payload(prices=prices)))

    result = await tool.run(_candidate(), _profile())

    assert len(result.normalized_data["price_basket"]) == len(_prices())
    assert any("Ignored 1 malformed" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_non_monthly_budget_is_not_compared_with_monthly_items():
    tool, _, _ = _tool()

    result = await tool.run(_candidate(), _profile(period="total"))

    assert result.normalized_data["budget_context"]["status"] == "unsupported_period"
    assert all(
        scenario["budget_remaining_after_named_items"] is None
        for scenario in result.normalized_data["fixed_cost_scenarios"].values()
    )


@pytest.mark.asyncio
async def test_budget_excluding_accommodation_is_not_compared_with_rent_scenario():
    tool, _, _ = _tool()

    result = await tool.run(
        _candidate(),
        _profile(
            includes_accommodation=False,
            budget_scope="living_cost_excluding_accommodation",
        ),
    )

    assert result.normalized_data["budget_context"]["status"] == "comparable_without_conversion"
    assert result.normalized_data["budget_context"]["budget_scope"] == "living_cost_excluding_accommodation"
    assert result.normalized_data["budget_context"]["includes_accommodation"] is False
    assert all(
        scenario["budget_remaining_after_named_items"] is None
        for scenario in result.normalized_data["fixed_cost_scenarios"].values()
    )
    assert result.normalized_data["compatible_budget_comparison"]["cost_scope"] == (
        "living_cost_excluding_accommodation"
    )
    assert result.normalized_data["compatible_budget_comparison"]["comparison_cost"] == {
        "amount": 182.0,
        "currency": "EUR",
    }


def test_accommodation_only_budget_uses_only_housing_component():
    from app.agent.candidate_funnel import budget_comparison

    prices = [
        {
            "category": "Housing",
            "item": "1-bedroom apartment, outside",
            "price_usd": 650.0,
            "price_local": 650.0,
        },
        {
            "category": "Utilities & Internet",
            "item": "Electricity, water, garbage (85m2)",
            "price_usd": 115.0,
            "price_local": 115.0,
        },
        {
            "category": "Utilities & Internet",
            "item": "Internet (60+ Mbps)",
            "price_usd": 33.0,
            "price_local": 33.0,
        },
        {"category": "Transport", "item": "Monthly transit pass", "price_usd": 40.0, "price_local": 40.0},
    ]
    comparison = BudgetComparison(
        "comparable_without_conversion",
        700.0,
        "USD",
        "monthly",
        "accommodation_only",
        comparison_amount=700.0,
        comparison_currency="USD",
    )

    scenarios, _missing, scope_comparisons = build_fixed_cost_scenarios(prices, "USD", comparison)
    selected = scope_comparisons["accommodation_only"]["outside_center"]

    assert scenarios["outside_center"]["monthly_total_usd"] == 838.0
    assert budget_comparison(
        {
            "budget_context": comparison.normalized_data(),
            "fixed_cost_scenarios": scenarios,
            "compatible_budget_comparison": selected,
        }
    ) == (650.0, 50.0, "USD")


def test_accommodation_only_budget_selects_cheapest_compatible_housing():
    from app.agent.candidate_funnel import budget_comparison

    prices = [
        {
            "category": "Housing",
            "item": "1-bedroom apartment, center",
            "price_usd": 850.0,
            "price_local": 850.0,
        },
        {
            "category": "Housing",
            "item": "1-bedroom apartment, outside",
            "price_usd": 650.0,
            "price_local": 650.0,
        },
    ]
    comparison = BudgetComparison(
        "comparable_without_conversion",
        700.0,
        "EUR",
        "monthly",
        "accommodation_only",
        comparison_amount=700.0,
        comparison_currency="EUR",
    )

    scenarios, _missing, scope_comparisons = build_fixed_cost_scenarios(prices, "EUR", comparison)
    selected = _first_scope_comparison(scope_comparisons, "accommodation_only")

    assert selected is not None
    assert selected["evidence_label"] == "outside_center"
    assert selected["comparison_cost"] == {"amount": 650.0, "currency": "EUR"}
    assert selected["budget_remaining"] == {"amount": 50.0, "currency": "EUR"}
    assert budget_comparison(
        {
            "budget_context": comparison.normalized_data(),
            "fixed_cost_scenarios": scenarios,
            "compatible_budget_comparison": selected,
        }
    ) == (650.0, 50.0, "EUR")


def test_accommodation_only_budget_selects_cheapest_housing_when_all_options_are_over():
    prices = [
        {
            "category": "Housing",
            "item": "1-bedroom apartment, center",
            "price_usd": 900.0,
            "price_local": 900.0,
        },
        {
            "category": "Housing",
            "item": "1-bedroom apartment, outside",
            "price_usd": 780.0,
            "price_local": 780.0,
        },
    ]
    comparison = BudgetComparison(
        "comparable_without_conversion",
        700.0,
        "EUR",
        "monthly",
        "accommodation_only",
        comparison_amount=700.0,
        comparison_currency="EUR",
    )

    _scenarios, _missing, scope_comparisons = build_fixed_cost_scenarios(prices, "EUR", comparison)
    selected = _first_scope_comparison(scope_comparisons, "accommodation_only")

    assert selected is not None
    assert selected["evidence_label"] == "outside_center"
    assert selected["comparison_cost"] == {"amount": 780.0, "currency": "EUR"}
    assert selected["budget_remaining"] == {"amount": -80.0, "currency": "EUR"}


def test_generic_apartment_evidence_is_not_marked_as_verified_student_housing():
    prices = [
        {
            "category": "Housing",
            "item": "1-bedroom apartment, outside",
            "price_usd": 650.0,
            "price_local": 650.0,
        },
    ]
    comparison = BudgetComparison(
        "comparable_without_conversion",
        700.0,
        "EUR",
        "monthly",
        "accommodation_only",
        comparison_amount=700.0,
        comparison_currency="EUR",
    )

    _scenarios, _missing, scope_comparisons = build_fixed_cost_scenarios(prices, "EUR", comparison)
    selected = _first_scope_comparison(scope_comparisons, "accommodation_only")

    assert selected is not None
    assert selected["comparison_cost"] == {"amount": 650.0, "currency": "EUR"}
    assert selected["housing_evidence_kind"] == "generic_apartment"
    assert selected["housing_evidence_description"] == (
        "generic one-bedroom apartment outside the city center"
    )
    assert selected["student_housing_directly_verified"] is False


def test_total_living_cost_budget_can_use_combined_monthly_scenario():
    from app.agent.candidate_funnel import budget_comparison

    prices = [
        {
            "category": "Housing",
            "item": "1-bedroom apartment, outside",
            "price_usd": 650.0,
            "price_local": 650.0,
        },
        {
            "category": "Utilities & Internet",
            "item": "Electricity, water, garbage (85m²)",
            "price_usd": 115.0,
            "price_local": 115.0,
        },
        {
            "category": "Utilities & Internet",
            "item": "Internet (60+ Mbps)",
            "price_usd": 33.0,
            "price_local": 33.0,
        },
        {"category": "Transport", "item": "Monthly transit pass", "price_usd": 40.0, "price_local": 40.0},
    ]
    comparison = BudgetComparison(
        "comparable_without_conversion",
        1800.0,
        "USD",
        "monthly",
        "total_living_cost",
        comparison_amount=1800.0,
        comparison_currency="USD",
    )

    scenarios, _missing, scope_comparisons = build_fixed_cost_scenarios(prices, "USD", comparison)
    selected = scope_comparisons["total_living_cost"]["outside_center"]

    assert budget_comparison(
        {
            "budget_context": comparison.normalized_data(),
            "fixed_cost_scenarios": scenarios,
            "compatible_budget_comparison": selected,
        }
    ) == (838.0, 962.0, "USD")


@pytest.mark.asyncio
async def test_invalid_budget_amount_or_currency_is_never_compared():
    tool, _, _ = _tool()

    zero = await tool.run(_candidate(), _profile(amount=0))
    invalid_currency = await tool.run(_candidate(), _profile(currency="euros"))

    assert zero.normalized_data["budget_context"]["status"] == "invalid_amount"
    assert invalid_currency.normalized_data["budget_context"]["status"] == "missing_currency"
    assert invalid_currency.normalized_data["budget_context"]["original_currency"] == "EUROS"
    for result in (zero, invalid_currency):
        assert all(
            scenario["budget_remaining_after_named_items"] is None
            for scenario in result.normalized_data["fixed_cost_scenarios"].values()
        )


@pytest.mark.asyncio
async def test_shared_coverage_and_exchange_requests_are_coalesced():
    cache = FakeCache()
    http = FakeHttp(delay=0.01)
    client = BudgetDataClient(cache, http)

    await asyncio.gather(client.coverage(), client.coverage(), client.coverage())
    await asyncio.gather(client.exchange_rate("ILS"), client.exchange_rate("ILS"))

    coverage_calls = [(url, params) for url, params in http.calls if "city-prices" in url]
    fx_calls = [(url, params) for url, params in http.calls if "frankfurter" in url]
    assert len(coverage_calls) == 1
    assert len(fx_calls) == 1


@pytest.mark.asyncio
async def test_cancelling_last_waiter_cancels_shared_provider_work():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingHttp:
        async def get_json(self, url, **kwargs):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    client = BudgetDataClient(FakeCache(), BlockingHttp())
    task = asyncio.create_task(client.coverage())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(cancelled.wait(), timeout=0.1)
    assert client._inflight == {}


@pytest.mark.asyncio
async def test_fresh_raw_cache_skips_provider_and_stale_cache_is_disclosed_on_failure():
    cache = FakeCache()
    first_http = FakeHttp()
    first = BudgetDataClient(cache, first_http)
    await first.coverage()
    await first.city_prices("PT-Lisbon")

    cached_http = FakeHttp(errors={"coverage": RuntimeError("must not be called")})
    cached_tool = BudgetFitTool(cache, http=cached_http, data_client=BudgetDataClient(cache, cached_http))
    cached_result = await cached_tool.run(_candidate(), _profile())
    assert cached_result.stale is False
    assert cached_http.calls == []

    for key in list(cache.values):
        if key[0] in {"BudgetFitTool:coverage", "BudgetFitTool:city_prices"}:
            cache.stale_keys.add(key)
    stale_http = FakeHttp(errors={"coverage": RuntimeError("down"), "city": RuntimeError("down")})
    stale_tool = BudgetFitTool(cache, http=stale_http, data_client=BudgetDataClient(cache, stale_http))
    stale_result = await stale_tool.run(_candidate(), _profile())
    assert stale_result.stale is True
    assert stale_result.confidence == "low"
    assert stale_result.evidence_items[0].source.stale is True
    assert any("expired cached city-price" in warning for warning in stale_result.warnings)


def test_fixed_cost_builder_does_not_infer_missing_values():
    parsed = parse_city_prices(_city_payload(prices=_prices()[:1]))

    scenarios, missing, scope_comparisons = build_fixed_cost_scenarios(
        parsed["prices"],
        "EUR",
        BudgetComparison("not_provided", None, None, "unknown"),
    )

    assert scenarios == {}
    assert scope_comparisons == {}
    assert "one_bedroom_center" in missing
