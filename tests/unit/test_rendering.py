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
