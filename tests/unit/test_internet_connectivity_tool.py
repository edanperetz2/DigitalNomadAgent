"""Internet speed as its own criterion, measured rather than inferred from wealth.

P01 states "fast, reliable internet" as its top priority after budget, and on
2026-08-10 the shipped answer said internet quality "is not directly
established" for seven of eight candidates. `AmenitiesTool` counted coworking
spaces and cafes, and the criterion was called `work_infrastructure` -- a city
can have forty cafes and unusable upstream bandwidth.

The first version of this tool scored World Bank *fixed-broadband subscriptions
per 100 people*. It produced an inverted ranking, caught by the user:

    country     Ookla median   world rank   subscriptions-per-100 score
    Romania     283.06 Mbit/s          11   0.91
    Thailand    279.65 Mbit/s          14   0.59   <- scored worst
    Portugal    243.84 Mbit/s          22   0.95   <- scored best

Penetration tracks GDP and housing patterns, not connection quality, so it is
biased against exactly the destinations digital nomads pick. The inversion is
pinned below so it cannot return.
"""

from datetime import UTC, datetime

import pytest

from app.agent.dynamic_evaluation import (
    _CONSTRAINT_LABELS,
    _TOOL_CRITERIA,
    DEFAULT_WEIGHTS,
    _extract_criterion_scores,
    canonical_criterion_name,
    criteria_for_constraint,
)
from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.tools.fakes import FakeInternetConnectivityTool
from app.tools.internet_connectivity import (
    MIN_PARSED_COUNTRIES,
    SPEED_SATURATION_MBPS,
    adoption_score,
    parse_speed_table,
    speed_band,
    speed_ranking,
    speed_score,
)

# Real figures, read from the Ookla-sourced table on 2026-08-10.
OOKLA = {"Romania": 283.06, "Thailand": 279.65, "Portugal": 243.84, "Georgia": 44.73}


def _candidate(name: str = "Cluj-Napoca", country: str = "Romania", cc: str = "RO") -> CandidatePlace:
    return CandidatePlace(
        place_name=name, country=country, reason_for_inclusion="t", verified=True,
        lat=46.77, lon=23.59, country_code=cc,
    )


def _result(data: dict) -> ToolResult:
    return ToolResult(
        tool_name="InternetConnectivityTool", place="Cluj-Napoca", normalized_data=data,
        source_name="Ookla", retrieved_at=datetime.now(UTC), confidence="medium",
    )


def test_internet_is_a_criterion_of_its_own():
    assert "internet" in DEFAULT_WEIGHTS
    assert _TOOL_CRITERIA["InternetConnectivityTool"] == ("internet",)


def test_internet_wording_no_longer_lands_on_the_coworking_criterion():
    for phrase in ("internet_speed", "internet quality", "reliable wifi", "broadband"):
        assert canonical_criterion_name(phrase) == "internet", phrase
    for phrase in ("coworking availability", "remote work setup", "desk space"):
        assert canonical_criterion_name(phrase) == "work_infrastructure", phrase


def test_airport_connectivity_still_means_flights():
    """`connectivity` belongs to accessibility; taking it would be D62 again."""
    assert canonical_criterion_name("airport_connectivity") == "accessibility"


def test_a_stated_internet_requirement_is_checkable():
    assert criteria_for_constraint("fast, reliable internet") == ["internet"]
    assert "internet" in _CONSTRAINT_LABELS


def test_thailand_is_not_ranked_below_portugal():
    """The inversion, pinned. Thailand is faster than Portugal and must score so."""
    assert speed_score(OOKLA["Thailand"]) >= speed_score(OOKLA["Portugal"])


def test_a_genuinely_slow_country_scores_below_a_fast_one():
    assert speed_score(OOKLA["Georgia"]) < speed_score(OOKLA["Romania"])
    assert speed_score(OOKLA["Georgia"]) < 0.5


def test_the_score_rises_with_speed():
    assert speed_score(12.0) < speed_score(50.0) < speed_score(120.0)


def test_the_score_saturates_where_more_stops_mattering():
    """244 and 283 Mbit/s are the same working day; the score should say so."""
    assert speed_score(OOKLA["Portugal"]) == speed_score(OOKLA["Romania"]) == 1.0
    assert speed_score(SPEED_SATURATION_MBPS) == 1.0


def test_unusable_speed_floors_at_zero_rather_than_going_negative():
    assert speed_score(5.0) == 0.0
    assert speed_score(0.0) == 0.0


def test_no_speed_means_no_score_rather_than_a_zero():
    """Absence of evidence must not read as evidence of bad internet (D22)."""
    assert speed_score(None) is None


def test_adoption_is_only_a_fallback_and_is_weaker():
    """Used when no median is published. It says how many, not how fast."""
    assert adoption_score(90.0) is not None
    assert adoption_score(None) is None


def test_the_table_parser_reads_the_fixed_broadband_section_only():
    wikitext = (
        "== Fixed broadband ==\n"
        "{| class=\"wikitable\"\n"
        "| style=\"text-align: left\" | {{flagcountry|Romania}} || 283.06\n"
        "| style=\"text-align: left\" | {{flagcountry|Thailand}} || 279.65\n"
        "|}\n"
        "\n== Mobile broadband ==\n"
        "| style=\"text-align: left\" | {{flagcountry|Romania}} || 61.00\n"
    )
    speeds = parse_speed_table(wikitext)
    assert speeds == {"Romania": 283.06, "Thailand": 279.65}, "mobile must not leak in"


def test_the_parser_returns_nothing_when_the_section_is_gone():
    """A markup change must fail loudly, not silently score everything alike."""
    assert parse_speed_table("== Something else ==\n{{flagcountry|Romania}} || 283.06") == {}
    assert MIN_PARSED_COUNTRIES >= 80


def test_the_speed_is_given_a_meaning_not_just_a_number():
    """"45 Mbit/s" asks the reader to know what a megabit is. It should not."""
    assert speed_band(4.0) == "too slow to rely on for video calls"
    assert "large uploads will drag" in speed_band(18.0)
    assert "everyday remote work" in speed_band(45.0)
    assert "heavy uploads" in speed_band(160.0)
    assert speed_band(283.06) == "among the fastest measured anywhere"
    assert speed_band(None) is None


def test_the_meaning_is_about_what_the_traveller_will_be_doing():
    """Anchored in tasks, not adjectives -- "good internet" means nothing."""
    for mbps in (4.0, 18.0, 45.0, 160.0):
        band = speed_band(mbps)
        assert any(
            word in band for word in ("video call", "uploads", "screen sharing", "devices")
        ), band


def test_the_ranking_calibrates_without_needing_units():
    speeds = {"Romania": 283.06, "Thailand": 279.65, "Portugal": 243.84, "Georgia": 44.73}
    assert speed_ranking(speeds, "Romania") == (1, 4)
    assert speed_ranking(speeds, "Georgia") == (4, 4)
    assert speed_ranking(speeds, "Narnia") is None


def test_a_mediocre_rank_does_not_contradict_a_usable_speed():
    """Georgia is 118th of 153 and still fine to work from.

    The rank alone would read as a warning and the number alone as noise; the
    band is what stops the pair being misleading.
    """
    assert "everyday remote work" in speed_band(44.73)


def test_the_speed_reaches_the_evaluation_as_the_internet_criterion():
    profile = PlaceRequestProfile(purpose="remote_work", relevant_criteria=["internet"])
    scores, _, advantages, _, confidence = _extract_criterion_scores(
        [_result({"connectivity_score": 0.85, "median_download_mbps": 128.0})], profile
    )
    assert scores["internet"] == 0.85
    assert confidence["internet"] < 0.75, "a national median rates below a city measurement"
    assert any("128 Mbit/s" in a for a in advantages)


def test_the_reader_gets_the_number_its_meaning_and_the_comparison():
    profile = PlaceRequestProfile(purpose="remote_work", relevant_criteria=["internet"])
    _, _, advantages, drawbacks, _ = _extract_criterion_scores(
        [
            _result(
                {
                    "connectivity_score": 0.25,
                    "median_download_mbps": 44.73,
                    "speed_meaning": speed_band(44.73),
                    "country_speed_rank": 118,
                    "countries_ranked": 153,
                }
            )
        ],
        profile,
    )
    note = next(t for t in advantages + drawbacks if "Mbit/s" in t)
    assert "45 Mbit/s" in note
    assert "everyday remote work" in note
    assert "118 of 153 countries measured" in note
    assert "not a measurement of any one apartment" in note


def test_the_reader_is_told_it_is_a_national_median():
    profile = PlaceRequestProfile(purpose="remote_work", relevant_criteria=["internet"])
    _, _, advantages, drawbacks, _ = _extract_criterion_scores(
        [_result({"connectivity_score": 0.9, "median_download_mbps": 200.0})], profile
    )
    assert any("national median" in t for t in advantages + drawbacks)


def test_the_fallback_says_plainly_that_it_is_not_a_speed():
    profile = PlaceRequestProfile(purpose="remote_work", relevant_criteria=["internet"])
    _, _, advantages, drawbacks, _ = _extract_criterion_scores(
        [_result({"connectivity_score": 0.6, "median_download_mbps": None, "internet_users_pct": 82.0})],
        profile,
    )
    assert any("how many rather than how fast" in t for t in advantages + drawbacks)


def test_a_missing_score_contributes_nothing_rather_than_a_default():
    profile = PlaceRequestProfile(purpose="remote_work", relevant_criteria=["internet"])
    scores, _, _, _, _ = _extract_criterion_scores([_result({"connectivity_score": None})], profile)
    assert "internet" not in scores


@pytest.mark.asyncio
async def test_the_tool_reports_what_it_scored_from():
    result = await FakeInternetConnectivityTool().run(
        _candidate(), PlaceRequestProfile(purpose="remote_work")
    )
    assert result.normalized_data["scored_from"] == "median_download_speed"
    assert result.normalized_data["median_download_mbps"] is not None
    assert "national median" in " ".join(result.warnings)


@pytest.mark.asyncio
async def test_citations_name_ookla_and_pin_a_revision():
    result = await FakeInternetConnectivityTool().run(
        _candidate(), PlaceRequestProfile(purpose="remote_work")
    )
    items = result.resolved_evidence_items()
    assert items
    assert all(item.criterion == "internet" for item in items)
    assert any("Ookla" in item.source.source_name for item in items)
    assert any("oldid=" in (item.source.source_url or "") for item in items)
