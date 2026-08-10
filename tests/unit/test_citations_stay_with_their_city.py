"""A claim about one place must not cite another place's evidence.

Found on 2026-08-10 in a real P01 run. Timisoara ranked **first**, and every
factual claim about it -- "5 coworking spaces and about 100 cafes" [1], "about
EUR996/month" [2], "major airport about 10 km away" [4][9][10] -- cited Seville's
sources. Timisoara's own sources, numbered 11-20 and correctly labelled in the
payload, were cited nowhere, so the deterministic bibliography (D74) faithfully
printed Seville's entries beneath the top recommendation.

The cause was two naming conventions for one thing. D44 appended "-- <place>" to
source names when building the bibliography, so a reader could tell which city
"Wikivoyage Get around section" supported. `_criterion_sources` was never
updated, so a candidate's own sources stayed *bare* -- "Wikivoyage Connect
section", identical for every city in the run. The model was handed a flat list
of eighty numbered, city-suffixed sources and, per candidate, a set of unsuffixed
names, and left to bridge them by reading city names out of strings.
"""

from datetime import UTC, datetime

from app.agent.dynamic_evaluation import evaluate_candidates
from app.agent.models import Budget, CandidatePlace, PlaceRequestProfile
from app.agent.recommendation_generator import _llm_payload
from app.evidence.models import (
    EvidenceItem,
    EvidenceSource,
    ToolResult,
    qualified_source_name,
)


def _candidate(place: str, country: str) -> CandidatePlace:
    return CandidatePlace(
        place_name=place, country=country, reason_for_inclusion="t", verified=True,
        lat=1.0, lon=1.0,
    )


def _amenities(place: str) -> ToolResult:
    now = datetime.now(UTC)
    return ToolResult(
        tool_name="AmenitiesTool",
        place=place,
        normalized_data={"counts_by_category": {"coworking": 5, "cafe": 25}, "partial": False},
        source_name="envelope",
        retrieved_at=now,
        confidence="medium",
        evidence_items=[
            EvidenceItem(
                criterion="work_infrastructure",
                normalized_data={},
                source=EvidenceSource(
                    source_name="OpenStreetMap Overpass", retrieved_at=now, confidence="medium"
                ),
            )
        ],
    )


def test_the_two_naming_conventions_are_one_function():
    assert qualified_source_name("OpenStreetMap Overpass", "Timișoara") == (
        "OpenStreetMap Overpass — Timișoara"
    )


def test_a_name_that_already_says_the_place_is_not_suffixed_twice():
    assert qualified_source_name("Wikivoyage Lisbon article", "Lisbon") == (
        "Wikivoyage Lisbon article"
    )


def test_a_candidates_sources_name_the_candidate():
    """Bare names were identical across every city in the run."""
    profile = PlaceRequestProfile(
        purpose="remote_work", relevant_criteria=["work_infrastructure"], budget=Budget()
    )
    evaluations = evaluate_candidates(
        [_candidate("Timișoara", "Romania"), _candidate("Seville", "Spain")],
        profile,
        {"Timișoara": [_amenities("Timișoara")], "Seville": [_amenities("Seville")]},
    )
    by_place = {e.place: e.criterion_sources["work_infrastructure"] for e in evaluations}
    assert by_place["Timișoara"] == ["OpenStreetMap Overpass — Timișoara"]
    assert by_place["Seville"] == ["OpenStreetMap Overpass — Seville"]
    assert by_place["Timișoara"] != by_place["Seville"], "the whole bug in one line"


def test_each_candidate_is_handed_its_own_source_numbers():
    """No lookup left to do: the numbers arrive attached to the place."""
    payload = {
        "sources": [
            {"source_name": "OpenStreetMap Overpass — Seville"},
            {"source_name": "OpenStreetMap Overpass — Timișoara"},
        ],
        "candidates": [
            {
                "place": "Timișoara",
                "country": "Romania",
                "criterion_sources": {
                    "work_infrastructure": ["OpenStreetMap Overpass — Timișoara"]
                },
            },
            {
                "place": "Seville",
                "country": "Spain",
                "criterion_sources": {"work_infrastructure": ["OpenStreetMap Overpass — Seville"]},
            },
        ],
    }
    presented = _llm_payload(payload)
    by_place = {c["place"]: c for c in presented["candidates"]}

    assert by_place["Timișoara"]["cite_only_these_source_numbers"] == [2]
    assert by_place["Seville"]["cite_only_these_source_numbers"] == [1]
    assert by_place["Timișoara"]["criterion_sources"] == {"work_infrastructure": [2]}


def test_the_numbers_a_candidate_is_given_are_the_bibliography_numbers():
    """Off-by-one here is the earlier version of this same defect."""
    payload = {
        "sources": [{"source_name": f"Source {i} — Place{i}"} for i in range(1, 6)],
        "candidates": [
            {
                "place": "Place3",
                "criterion_sources": {"cost": ["Source 3 — Place3"]},
            }
        ],
    }
    presented = _llm_payload(payload)
    assert presented["sources"][2]["number"] == 3
    assert presented["candidates"][0]["cite_only_these_source_numbers"] == [3]


def test_a_source_the_candidate_does_not_have_is_not_offered_to_it():
    payload = {
        "sources": [
            {"source_name": "A — Seville"},
            {"source_name": "B — Timișoara"},
        ],
        "candidates": [{"place": "Timișoara", "criterion_sources": {"cost": ["B — Timișoara"]}}],
    }
    presented = _llm_payload(payload)
    assert 1 not in presented["candidates"][0]["cite_only_these_source_numbers"]


def test_the_generator_is_told_to_stay_within_a_places_own_numbers():
    from app.agent.recommendation_generator import SYSTEM_PROMPT

    assert "cite_only_these_source_numbers" in SYSTEM_PROMPT
    assert "cite only from" in SYSTEM_PROMPT
