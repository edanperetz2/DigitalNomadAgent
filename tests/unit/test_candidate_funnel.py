from datetime import UTC, datetime

from app.agent.candidate_funnel import estimate_affordability, select_finalists
from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult


def _candidate(name, country="Spain", country_code="ES", importance=0.7):
    return CandidatePlace(
        place_name=name,
        country=country,
        reason_for_inclusion="t",
        verified=True,
        country_code=country_code,
        geocoding_importance=importance,
    )


def test_a_named_destination_survives_to_the_finalists():
    """The user asked about this place, so "no, and here is why" is a valid
    answer -- but it can only be given if the place is still in the running."""
    profile = PlaceRequestProfile(purpose="remote_work", named_destinations=["Lisbon"])
    candidates = [
        _candidate("Lisbon", country="Portugal", country_code="PT", importance=0.1),
        _candidate("Valencia", importance=0.9),
        _candidate("Seville", importance=0.9),
    ]

    finalists = select_finalists(candidates, profile, {}, max_finalists=2)

    # Ranked last on importance, but pinned because the user named it.
    assert finalists[0].place_name == "Lisbon"
    assert len(finalists) == 2


def test_orchestrator_adds_a_named_destination_the_generator_left_out():
    """Candidate generation proposes places it thinks fit, so the city the user
    is asking about can simply be absent."""
    from app.agent.orchestrator import _include_named_destinations

    profile = PlaceRequestProfile(purpose="remote_work", named_destinations=["Lisbon"])
    candidates = [_candidate("Valencia")]

    result = _include_named_destinations(profile, candidates)

    assert [c.place_name for c in result] == ["Lisbon", "Valencia"]


def test_a_named_destination_already_present_is_not_duplicated():
    from app.agent.orchestrator import _include_named_destinations

    profile = PlaceRequestProfile(purpose="remote_work", named_destinations=["valencia"])
    candidates = [_candidate("Valencia")]

    result = _include_named_destinations(profile, candidates)

    assert [c.place_name for c in result] == ["Valencia"]


def test_orchestrator_relaxes_a_preferred_region_that_matches_nothing():
    """The pipeline-level fix. Relaxing only inside select_finalists is not
    enough: _check_hard_constraints re-runs the same region check during
    scoring, so an unrelaxed profile just moves the failure downstream from
    "eliminated by region constraints" to "eliminated by hard constraints".
    """
    from app.agent.orchestrator import _relax_unresolvable_preferred_regions

    profile = PlaceRequestProfile(
        purpose="remote_work", preferred_regions=["Europe", "mid-sized city"]
    )
    candidates = [_candidate("Valencia"), _candidate("Porto", country="Portugal", country_code="PT")]

    relaxed = _relax_unresolvable_preferred_regions(profile, candidates)

    assert relaxed.preferred_regions == []
    assert any("region preference" in a for a in relaxed.assumptions), "relaxation must be disclosed"
    assert profile.preferred_regions == ["Europe", "mid-sized city"], "must not mutate the original"


def test_orchestrator_keeps_a_preferred_region_that_matches_something():
    from app.agent.orchestrator import _relax_unresolvable_preferred_regions

    profile = PlaceRequestProfile(purpose="remote_work", preferred_regions=["Spain"])
    candidates = [_candidate("Valencia"), _candidate("Porto", country="Portugal", country_code="PT")]

    relaxed = _relax_unresolvable_preferred_regions(profile, candidates)

    assert relaxed.preferred_regions == ["Spain"]
    assert relaxed.assumptions == []


def test_continental_preferred_region_does_not_eliminate_every_candidate():
    """"Somewhere in Europe" must not wipe out the whole field.

    check_geocoded_constraints only compares country identity, so a continent
    name matches nothing and previously eliminated every candidate -- which the
    orchestrator turned into a hard "all candidates eliminated by region
    constraints" failure for the flagship remote-work prompt.
    """
    profile = PlaceRequestProfile(purpose="remote_work", preferred_regions=["Europe"])
    candidates = [_candidate("Valencia"), _candidate("Porto", country="Portugal", country_code="PT")]

    finalists = select_finalists(candidates, profile, {}, max_finalists=8)

    assert {c.place_name for c in finalists} == {"Valencia", "Porto"}


def test_non_geographic_preferred_region_does_not_eliminate_every_candidate():
    """The interpreter has been observed emitting preferred_regions=
    ["Europe", "mid-sized city"]; a size preference is not a region."""
    profile = PlaceRequestProfile(
        purpose="remote_work", preferred_regions=["Europe", "mid-sized city"]
    )
    candidates = [_candidate("Valencia")]

    finalists = select_finalists(candidates, profile, {}, max_finalists=8)

    assert [c.place_name for c in finalists] == ["Valencia"]


def test_country_level_preferred_region_still_filters_when_some_candidates_match():
    """The relaxation must not weaken a preference that genuinely resolves."""
    profile = PlaceRequestProfile(purpose="vacation", preferred_regions=["Spain"])
    candidates = [_candidate("Valencia"), _candidate("Porto", country="Portugal", country_code="PT")]

    finalists = select_finalists(candidates, profile, {}, max_finalists=8)

    assert [c.place_name for c in finalists] == ["Valencia"]


def test_excluded_region_still_eliminates_absolutely():
    """excluded_regions is a stated deal-breaker: it must never be relaxed,
    even when it removes every candidate."""
    profile = PlaceRequestProfile(purpose="vacation", excluded_regions=["Spain"])
    candidates = [_candidate("Valencia"), _candidate("Seville")]

    assert select_finalists(candidates, profile, {}, max_finalists=8) == []


def _budget_result(place, *, status, remaining=None, monthly_total=None, error=None):
    if error:
        return ToolResult(
            tool_name="BudgetFitTool",
            place=place,
            source_name="t",
            retrieved_at=datetime.now(UTC),
            confidence="low",
            error=error,
        )
    scenarios = {}
    if remaining is not None and monthly_total is not None:
        scenarios["center"] = {
            "monthly_total_usd": monthly_total,
            "budget_remaining_after_named_items": {"amount": remaining, "currency": "USD"},
        }
    return ToolResult(
        tool_name="BudgetFitTool",
        place=place,
        source_name="t",
        retrieved_at=datetime.now(UTC),
        confidence="medium",
        normalized_data={
            "budget_context": {"status": status, "comparison_amount": None},
            "fixed_cost_scenarios": scenarios,
            "country_context": {},
        },
    )


def test_estimate_affordability_neutral_when_budget_not_provided():
    result = _budget_result("Valencia", status="not_provided")
    assert estimate_affordability(result.normalized_data) == 1.0


def test_estimate_affordability_neutral_on_missing_or_error_evidence():
    assert estimate_affordability(None) == 0.5
    assert estimate_affordability({}) == 0.5


def test_estimate_affordability_scores_comfortably_under_budget_higher():
    comfortable = _budget_result("Valencia", status="comparable_without_conversion", remaining=500, monthly_total=1000)
    tight = _budget_result("Valencia", status="comparable_without_conversion", remaining=50, monthly_total=1000)
    over_budget = _budget_result("Valencia", status="comparable_without_conversion", remaining=-500, monthly_total=1000)
    comfortable_score = estimate_affordability(comfortable.normalized_data)
    tight_score = estimate_affordability(tight.normalized_data)
    over_budget_score = estimate_affordability(over_budget.normalized_data)
    assert comfortable_score > tight_score > over_budget_score
    assert over_budget_score < 0.5


def test_select_finalists_excludes_region_and_ranks_by_composite():
    profile = PlaceRequestProfile(purpose="vacation", excluded_regions=["France"])
    candidates = [
        _candidate("Nice", country="France", country_code="FR", importance=0.9),
        _candidate("Valencia", country="Spain", country_code="ES", importance=0.6),
        _candidate("Kotor", country="Montenegro", country_code="ME", importance=0.5),
    ]
    budget_results = {
        "Valencia": [_budget_result("Valencia", status="not_provided")],
        "Kotor": [_budget_result("Kotor", status="not_provided")],
    }
    finalists = select_finalists(candidates, profile, budget_results, max_finalists=5)
    names = [c.place_name for c in finalists]
    assert "Nice" not in names
    assert names == ["Valencia", "Kotor"]


def test_select_finalists_caps_at_max_finalists():
    profile = PlaceRequestProfile(purpose="vacation")
    candidates = [_candidate(f"Place{i}", importance=1.0 - i * 0.01) for i in range(10)]
    finalists = select_finalists(candidates, profile, {}, max_finalists=3)
    assert len(finalists) == 3
    assert [c.place_name for c in finalists] == ["Place0", "Place1", "Place2"]


def _budget_estimate_result(place: str, monthly_usd: float, budget_usd: float) -> ToolResult:
    """BudgetFitTool's shape when it has a country-level estimate to compare."""
    return ToolResult(
        tool_name="BudgetFitTool",
        place=place,
        normalized_data={
            "budget_context": {"status": "converted_to_usd", "comparison_amount": budget_usd},
            "country_context": {"monthly_estimate_usd": monthly_usd},
        },
        source_name="Numbeo-style estimate",
        retrieved_at=datetime.now(UTC),
        confidence="medium",
    )


def _scandinavia_profile(amount: float = 400.0) -> PlaceRequestProfile:
    from app.agent.models import Budget

    return PlaceRequestProfile(
        purpose="vacation",
        preferred_regions=["Scandinavia"],
        budget=Budget(
            amount=amount, currency="USD", period="monthly", includes_accommodation=True
        ),
    )


def test_a_budget_nothing_can_meet_is_stated_not_just_scored_low():
    """P08 asks for a month in Scandinavia on $400 including accommodation.

    The run handled it without error and disclosed the relaxed region, but never
    told the reader the budget and the request are irreconcilable.
    """
    from app.agent.orchestrator import _disclose_unmeetable_budget

    profile = _scandinavia_profile()
    evidence = {
        "Oslo": [_budget_estimate_result("Oslo", 2400.0, 400.0)],
        "Bergen": [_budget_estimate_result("Bergen", 2100.0, 400.0)],
        "Tallinn": [_budget_estimate_result("Tallinn", 1150.0, 400.0)],
    }

    disclosed = _disclose_unmeetable_budget(profile, evidence)
    statement = disclosed.assumptions[-1]

    assert "None of the 3 places researched" in statement
    assert "400 USD monthly" in statement
    assert "including accommodation" in statement
    assert "Tallinn" in statement and "1,150 USD" in statement  # the cheapest, named
    # The original profile is never mutated -- callers still hold it.
    assert profile.assumptions == []


def test_no_budget_claim_when_something_researched_actually_fits():
    """An expensive-but-possible request must not be told it is impossible."""
    from app.agent.orchestrator import _disclose_unmeetable_budget

    profile = _scandinavia_profile(amount=2500.0)
    evidence = {
        "Oslo": [_budget_estimate_result("Oslo", 2400.0, 2500.0)],
        "Bergen": [_budget_estimate_result("Bergen", 2600.0, 2500.0)],
    }

    assert _disclose_unmeetable_budget(profile, evidence).assumptions == []


def test_no_budget_claim_from_a_single_data_point_or_from_none():
    """One over-budget city is not evidence that nothing works."""
    from app.agent.orchestrator import _disclose_unmeetable_budget

    profile = _scandinavia_profile()
    lone = {"Oslo": [_budget_estimate_result("Oslo", 2400.0, 400.0)]}
    assert _disclose_unmeetable_budget(profile, lone).assumptions == []
    assert _disclose_unmeetable_budget(profile, {}).assumptions == []


def test_no_budget_claim_when_the_user_never_stated_one():
    from app.agent.orchestrator import _disclose_unmeetable_budget

    profile = PlaceRequestProfile(purpose="vacation")
    evidence = {
        "Oslo": [_budget_estimate_result("Oslo", 2400.0, 400.0)],
        "Bergen": [_budget_estimate_result("Bergen", 2100.0, 400.0)],
    }
    assert _disclose_unmeetable_budget(profile, evidence).assumptions == []


def test_budget_comparison_returns_none_when_nothing_is_comparable():
    from app.agent.candidate_funnel import budget_comparison

    assert budget_comparison(None) is None
    assert budget_comparison({"budget_context": {"status": "not_provided"}}) is None
    assert budget_comparison({"budget_context": {"status": "converted_to_usd"}}) is None


def test_a_local_currency_estimate_is_not_reported_as_usd():
    """monthly_total_local is in the place's own currency, so labelling every
    figure USD would misreport it."""
    from app.agent.candidate_funnel import budget_comparison

    local = {
        "budget_context": {
            "status": "comparable_without_conversion",
            "comparison_currency": "NOK",
        },
        "fixed_cost_scenarios": {
            "center": {
                "monthly_total_local": 24000.0,
                "budget_remaining_after_named_items": {"amount": -20000.0},
            }
        },
    }
    assert budget_comparison(local) == (24000.0, -20000.0, "NOK")

    usd = {
        "budget_context": {"status": "converted_to_usd"},
        "fixed_cost_scenarios": {
            "center": {
                "monthly_total_usd": 2400.0,
                "budget_remaining_after_named_items": {"amount": -2000.0},
            }
        },
    }
    assert budget_comparison(usd) == (2400.0, -2000.0, "USD")


def test_the_region_relaxation_does_not_claim_selection_targeted_the_region():
    """P08 asks for Scandinavia and gets Lisbon, Tbilisi, Chiang Mai and Bali.

    The disclosure used to end "candidate selection still targeted it", which is
    true when the generator stays in the region and false when it does not --
    and nothing here can tell the two apart, because the region cannot be
    resolved to countries in the first place. So it must not be asserted.
    """
    from app.agent.orchestrator import _relax_unresolvable_preferred_regions

    profile = PlaceRequestProfile(purpose="vacation", preferred_regions=["Scandinavia"])
    candidates = [_candidate("Tbilisi", country="Georgia", country_code="GE")]

    statement = _relax_unresolvable_preferred_regions(profile, candidates).assumptions[-1]

    assert "Scandinavia" in statement
    assert "guidance rather than a filter" in statement
    assert "still targeted it" not in statement
    assert "may therefore not be in the region asked for" in statement
