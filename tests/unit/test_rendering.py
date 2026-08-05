from app.core.rendering import render_recommendation_markdown


def test_markdown_contains_required_sections():
    payload = {
        "purpose_summary": "a vacation request",
        "assumptions": ["Assumed budget includes accommodation."],
        "validation_issues": [],
        "candidates": [
            {
                "place": "Valencia",
                "country": "Spain",
                "total_score": 0.8,
                "confidence_score": 0.75,
                "advantages": ["Great weather"],
                "drawbacks": ["Crowded in summer"],
                "missing_evidence": [],
            },
            {
                "place": "Santorini",
                "country": "Greece",
                "total_score": 0.7,
                "confidence_score": 0.6,
                "advantages": ["Scenic views"],
                "drawbacks": ["Expensive"],
                "missing_evidence": ["activities"],
            },
        ],
        "sources": [
            {
                "source_name": "Open-Meteo",
                "source_url": "https://open-meteo.com/",
                "retrieved_at": "2026-07-01",
                "data_date": "2021-2025 climatology",
                "confidence": "high",
                "stale": True,
            }
        ],
    }
    markdown = render_recommendation_markdown(payload)
    assert "## Best matches" in markdown
    assert "### 1. Valencia" in markdown
    assert "### Trade-offs" in markdown
    assert "### Assumptions and limitations" in markdown
    assert "### Sources" in markdown
    assert "Assumed budget includes accommodation." in markdown
    assert "Open-Meteo" in markdown
    assert "data 2021-2025 climatology" in markdown
    assert "high confidence" in markdown
    assert "stale fallback" in markdown
    assert "visa" in markdown.lower() or "admission" in markdown.lower()


def test_absent_evidence_is_not_reported_as_an_absent_drawback():
    """A real run printed "No major drawback identified" for all eight
    candidates while also reporting no verified data for the two criteria the
    request hinged on. Silence is not reassurance."""
    payload = {
        "purpose_summary": "a remote work request",
        "candidates": [
            {
                "place": "Bucharest",
                "country": "Romania",
                "confidence_score": 0.2,
                "advantages": [],
                "drawbacks": [],
                "missing_evidence": ["work_infrastructure", "timezone"],
            }
        ],
    }

    markdown = render_recommendation_markdown(payload)

    assert "No major drawback identified" not in markdown
    assert "Not assessed: no verified data for work_infrastructure, timezone" in markdown
    # The strength side must be equally honest.
    assert "No specific strengths recorded" not in markdown
    assert "Ranked on partial evidence" in markdown


def test_no_major_drawback_is_still_used_when_evidence_is_complete():
    payload = {
        "purpose_summary": "a vacation request",
        "candidates": [
            {
                "place": "Valencia",
                "country": "Spain",
                "confidence_score": 0.9,
                "advantages": ["Great weather"],
                "drawbacks": [],
                "missing_evidence": [],
            }
        ],
    }

    assert "No major drawback identified" in render_recommendation_markdown(payload)


def test_markdown_handles_no_sources_gracefully():
    payload = {"purpose_summary": "a study request", "candidates": []}
    markdown = render_recommendation_markdown(payload)
    assert "No external sources" in markdown


def test_markdown_renders_more_than_three_candidates():
    candidates = [
        {
            "place": f"City{i}",
            "country": "Testland",
            "total_score": 1.0 - i * 0.01,
            "confidence_score": 0.6,
            "advantages": ["Good fit"],
            "drawbacks": ["Minor drawback"],
            "missing_evidence": [],
        }
        for i in range(8)
    ]
    payload = {"purpose_summary": "a vacation request", "candidates": candidates}
    markdown = render_recommendation_markdown(payload)

    table_rows = [line for line in markdown.splitlines() if line.startswith("| ") and "City" in line]
    assert len(table_rows) == 8
    for i in range(1, 9):
        assert f"City{i - 1}" in markdown
        assert f"### {i}. City{i - 1}" in markdown


def test_a_candidate_with_scores_but_no_advantage_is_not_called_under_evidenced():
    """Uppsala carried a full set of scores and was rendered "Provisional match
    based on limited evidence". Unfavourable is not the same as unevidenced."""
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        {
            "purpose_summary": "a vacation request",
            "candidates": [
                {
                    "place": "Uppsala",
                    "country": "Sweden",
                    "criterion_scores": {"cost": 0.0, "transportation": 0.4, "activities": 0.55},
                    "advantages": [],
                    "drawbacks": ["Over budget."],
                    "confidence_score": 0.9,
                }
            ],
        }
    )

    assert "Provisional match" not in markdown
    assert "balance of the evidence" in markdown
    assert "weakest on cost" in markdown


def test_a_candidate_with_no_evidence_at_all_still_says_so():
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        {
            "purpose_summary": "a vacation request",
            "candidates": [
                {
                    "place": "Nowhere",
                    "country": "Testland",
                    "criterion_scores": {},
                    "missing_evidence": ["climate"],
                    "advantages": [],
                    "drawbacks": [],
                    "confidence_score": 0.1,
                }
            ],
        }
    )

    assert "climate could not be verified" in markdown


_SOURCES = [
    {"source_name": "Numbeo", "source_url": "https://numbeo.example"},
    {"source_name": "OpenStreetMap", "source_url": "https://osm.example"},
    {"source_name": "GOV.UK", "source_url": "https://gov.example"},
]


def test_each_claim_points_into_the_numbered_bibliography():
    """E4: 33 undifferentiated citations that no claim pointed into."""
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        {
            "purpose_summary": "a remote work request",
            "sources": _SOURCES,
            "candidates": [
                {
                    "place": "Krakow",
                    "criterion_scores": {"cost": 0.8, "safety": 0.9},
                    "criterion_sources": {"cost": ["Numbeo"], "safety": ["GOV.UK", "OpenStreetMap"]},
                    "advantages": ["Cheap."],
                    "drawbacks": ["Cold."],
                    "confidence_score": 0.8,
                }
            ],
        }
    )

    assert "Evidence trail: cost [1], safety [2, 3]" in markdown
    assert "1. Numbeo" in markdown and "3. GOV.UK" in markdown


def test_a_source_not_in_the_bibliography_is_not_cited():
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        {
            "purpose_summary": "a remote work request",
            "sources": _SOURCES,
            "candidates": [
                {
                    "place": "Krakow",
                    "criterion_scores": {"cost": 0.8},
                    "criterion_sources": {"cost": ["Some tool that never registered a source"]},
                    "advantages": ["Cheap."],
                    "drawbacks": ["Cold."],
                    "confidence_score": 0.8,
                }
            ],
        }
    )

    assert "Evidence trail" not in markdown


def _ranked(place: str, total: float, scores: dict) -> dict:
    return {
        "place": place,
        "total_score": total,
        "criterion_scores": scores,
        "advantages": ["a"],
        "drawbacks": ["d"],
        "confidence_score": 0.8,
    }


def test_the_trade_off_names_the_criterion_actually_given_up():
    """E5: "X is the strongest match, but Y may be preferable if its advantages
    matter more to you" is true of every ranked list ever produced."""
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        {
            "purpose_summary": "a remote work request",
            "sources": [],
            "candidates": [
                _ranked("Krakow", 0.81, {"cost": 0.9, "safety": 0.6, "timezone": 0.5}),
                _ranked("Berlin", 0.78, {"cost": 0.4, "safety": 0.95, "timezone": 0.5}),
            ],
        }
    )

    assert "Taking Krakow over Berlin costs you safety: 0.60 against 0.95" in markdown
    assert "may be preferable if its advantages" not in markdown


def test_a_dominant_leader_is_reported_as_having_no_trade_off():
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        {
            "purpose_summary": "a remote work request",
            "sources": [],
            "candidates": [
                _ranked("Krakow", 0.9, {"cost": 0.9, "safety": 0.9}),
                _ranked("Berlin", 0.5, {"cost": 0.4, "safety": 0.5}),
            ],
        }
    )

    assert "beats Berlin on every criterion" in markdown
    assert "no real trade-off" in markdown


def test_candidates_scored_on_different_criteria_are_not_falsely_compared():
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        {
            "purpose_summary": "a remote work request",
            "sources": [],
            "candidates": [
                _ranked("Krakow", 0.9, {"cost": 0.9}),
                _ranked("Berlin", 0.5, {"safety": 0.5}),
            ],
        }
    )

    assert "cannot be compared directly" in markdown


def _named_payload(named, candidates):
    return {
        "purpose_summary": "a remote work request",
        "sources": [],
        "named_destinations": named,
        "candidates": candidates,
    }


def test_a_named_place_gets_its_verdict_before_the_ranking():
    """D30: P09 asked "is Lisbon a good fit?" and the answer opened "You asked
    for remote-work-friendly destinations" -- the generator was never told."""
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        _named_payload(
            ["Lisbon"],
            [
                _ranked("Seville", 0.92, {"cost": 0.9}),
                {**_ranked("Lisbon", 0.88, {"cost": 0.7}), "drawbacks": ["Centre is over budget."]},
            ],
        )
    )

    assert "On the place you asked about" in markdown
    assert "**Lisbon:** a reasonable fit, ranked 2 of 2; Seville scored higher" in markdown
    assert "Centre is over budget." in markdown
    # The verdict leads; the table still follows.
    assert markdown.index("On the place you asked about") < markdown.index("Best matches")


def test_a_named_place_that_ranks_first_is_said_so_plainly():
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        _named_payload(["Lisbon"], [_ranked("Lisbon", 0.9, {"cost": 0.9})])
    )
    assert "**Lisbon:** yes — it ranks first here" in markdown


def test_a_named_place_missing_from_the_shortlist_is_not_left_unmentioned():
    """The P09 failure mode: eight other cities and no word about the one asked about."""
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        _named_payload(["Lisbon"], [_ranked("Seville", 0.9, {"cost": 0.9})])
    )
    assert "**Lisbon:** researched, but it did not reach the final shortlist" in markdown


def test_no_verdict_section_when_no_place_was_named():
    from app.core.rendering import render_recommendation_markdown

    markdown = render_recommendation_markdown(
        {
            "purpose_summary": "a remote work request",
            "sources": [],
            "candidates": [_ranked("Seville", 0.9, {"cost": 0.9})],
        }
    )
    assert "On the place you asked about" not in markdown
