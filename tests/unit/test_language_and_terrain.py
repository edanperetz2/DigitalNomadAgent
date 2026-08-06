"""D34: the two criteria the wheelchair prompt (P06) stated and nothing measured.

Lisbon -- steep and cobbled -- was recommended first to a wheelchair user who
called step-free access and flat terrain non-negotiable, and the evidence cited
in its favour was the city's funiculars and public elevator. Separately,
"English being widely spoken matters a lot" ranked Lisbon, Barcelona and Seville
above London, Dublin, Bristol and Belfast.
"""

from datetime import UTC, datetime

import pytest

from app.agent.dynamic_evaluation import evaluate_candidates
from app.agent.models import Budget, CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.languages import english_reach, spoken_languages
from app.tools.language import LanguageTool
from app.tools.terrain import flatness_score, terrain_label


def _candidate(name: str, country: str) -> CandidatePlace:
    return CandidatePlace(
        place_name=name, country=country, reason_for_inclusion="test", verified=True, lat=1.0, lon=1.0
    )


def _tool_result(tool_name: str, place: str, data: dict) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        place=place,
        normalized_data=data,
        source_name=f"{tool_name} source",
        retrieved_at=datetime.now(UTC),
        confidence="medium",
    )


def test_english_reach_separates_native_widespread_and_limited():
    assert english_reach("United Kingdom") == "native"
    assert english_reach("Netherlands") == "widespread"
    assert english_reach("France") == "limited"


def test_an_unlisted_country_is_unmeasured_not_guessed():
    assert english_reach("Kiribati") is None
    assert spoken_languages("Kiribati") is None


@pytest.mark.asyncio
async def test_language_tool_reports_the_local_languages_and_english_reach():
    result = await LanguageTool().run(_candidate("Lisbon", "Portugal"), PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert result.normalized_data["spoken_languages"] == ["Portuguese"]
    assert result.normalized_data["english_reach"] == "widespread"


@pytest.mark.asyncio
async def test_language_tool_errors_rather_than_guessing_an_unlisted_country():
    result = await LanguageTool().run(_candidate("Tarawa", "Kiribati"), PlaceRequestProfile(purpose="vacation"))

    assert result.error is not None
    assert "not in the language reference table" in result.error


def test_flatness_reads_a_flat_city_higher_than_a_steep_one():
    assert flatness_score(10.0) == 1.0
    assert flatness_score(150.0) == 0.0
    assert 0.0 < flatness_score(60.0) < 1.0
    assert terrain_label(10.0) == "flat"
    assert terrain_label(150.0) == "steep"


def test_a_native_english_city_outranks_one_where_english_is_only_widespread():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["language_spoken"],
        hard_constraints=["widely_spoken_english"],
        budget=Budget(),
    )
    evidence = {
        "Lisbon": [
            _tool_result(
                "LanguageTool",
                "Lisbon",
                {"spoken_languages": ["Portuguese"], "english_reach": "widespread", "english_score": 0.75},
            )
        ],
        "Dublin": [
            _tool_result(
                "LanguageTool",
                "Dublin",
                {"spoken_languages": ["English", "Irish"], "english_reach": "native", "english_score": 1.0},
            )
        ],
    }

    ranked = evaluate_candidates(
        [_candidate("Lisbon", "Portugal"), _candidate("Dublin", "Ireland")], profile, evidence
    )

    assert [e.place for e in ranked] == ["Dublin", "Lisbon"]


def test_naming_english_is_not_answered_worse_than_leaving_it_implied():
    """D58: P06 asked for English and the named-language branch scored every
    country whose *official* list omits it at 0.0 -- below the elimination
    threshold. Cyprus is in this project's own English-widespread table, and its
    four cities were still eliminated for not speaking English. Seven of the
    eight researched places died that way and the answer collapsed to one row.
    """
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["language_spoken"],
        hard_constraints=["English widely spoken"],
        preferred_languages=["English"],
        budget=Budget(),
    )
    evidence = {
        place: [
            _tool_result(
                "LanguageTool",
                place,
                {
                    "spoken_languages": list(spoken_languages(country)),
                    "english_reach": english_reach(country),
                    "english_score": {"native": 1.0, "widespread": 0.75, "limited": 0.25}[
                        english_reach(country)
                    ],
                    "requested_languages": ["English"],
                    "matched_languages": [
                        language
                        for language in ["English"]
                        if language.casefold()
                        in {spoken.casefold() for spoken in spoken_languages(country)}
                    ],
                },
            )
        ]
        for place, country in (("Valletta", "Malta"), ("Limassol", "Cyprus"), ("Malaga", "Spain"))
    }

    ranked = evaluate_candidates(
        [
            _candidate("Valletta", "Malta"),
            _candidate("Limassol", "Cyprus"),
            _candidate("Malaga", "Spain"),
        ],
        profile,
        evidence,
    )

    # Nothing is eliminated for a language the reference says is usable, and the
    # three bands still discriminate: official > widespread > limited.
    assert [e.place for e in ranked] == ["Valletta", "Limassol", "Malaga"]
    assert not [e.place for e in ranked if e.eliminated]

    limassol = next(e for e in ranked if e.place == "Limassol")
    assert any("widely usable" in note for note in limassol.advantages + limassol.drawbacks)


def test_a_requested_language_other_than_english_still_fails_when_unspoken():
    """The D58 fallback is English-only on purpose: the reference table has a
    reach band for English and nothing comparable for any other language, so
    asking for Japanese in Portugal must still be answered honestly."""
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["language_spoken"],
        preferred_languages=["Japanese"],
        budget=Budget(),
    )
    evidence = {
        "Lisbon": [
            _tool_result(
                "LanguageTool",
                "Lisbon",
                {
                    "spoken_languages": ["Portuguese"],
                    "english_reach": "widespread",
                    "english_score": 0.75,
                    "requested_languages": ["Japanese"],
                    "matched_languages": [],
                },
            )
        ]
    }

    ranked = evaluate_candidates([_candidate("Lisbon", "Portugal")], profile, evidence)

    assert any("None of the languages you asked for" in note for note in ranked[0].drawbacks)


def test_flat_terrain_outranks_steep_when_the_request_says_it_is_non_negotiable():
    profile = PlaceRequestProfile(
        purpose="vacation",
        relevant_criteria=["terrain"],
        hard_constraints=["reasonably_flat_terrain", "step_free_access"],
        budget=Budget(),
    )
    evidence = {
        "Lisbon": [
            _tool_result(
                "TerrainTool",
                "Lisbon",
                {"flatness_score": 0.05, "elevation_spread_m": 120.0, "terrain": "steep"},
            )
        ],
        "Adelaide": [
            _tool_result(
                "TerrainTool",
                "Adelaide",
                {"flatness_score": 1.0, "elevation_spread_m": 12.0, "terrain": "flat"},
            )
        ],
    }

    ranked = evaluate_candidates(
        [_candidate("Lisbon", "Portugal"), _candidate("Adelaide", "Australia")], profile, evidence
    )

    assert [e.place for e in ranked] == ["Adelaide", "Lisbon"]
    assert any("steep" in drawback for drawback in ranked[1].drawbacks)


def test_terrain_evidence_never_claims_to_be_step_free_access():
    """A gradient measurement is not an accessibility audit, and must not read
    as one -- the original answer offered Lisbon's funiculars as evidence *for*
    step-free access."""
    profile = PlaceRequestProfile(
        purpose="vacation", relevant_criteria=["terrain"], hard_constraints=["step_free_access"], budget=Budget()
    )
    evidence = {
        "Adelaide": [
            _tool_result(
                "TerrainTool",
                "Adelaide",
                {"flatness_score": 1.0, "elevation_spread_m": 12.0, "terrain": "flat"},
            )
        ]
    }

    evaluation = evaluate_candidates([_candidate("Adelaide", "Australia")], profile, evidence)[0]

    assert all("step-free" not in advantage for advantage in evaluation.advantages)
