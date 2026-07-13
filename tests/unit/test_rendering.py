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
            {"source_name": "Open-Meteo", "source_url": "https://open-meteo.com/", "retrieved_at": "2026-07-01"}
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
    assert "visa" in markdown.lower() or "admission" in markdown.lower()


def test_markdown_handles_no_sources_gracefully():
    payload = {"purpose_summary": "a study request", "candidates": []}
    markdown = render_recommendation_markdown(payload)
    assert "No external sources" in markdown
