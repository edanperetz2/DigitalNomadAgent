from tests.integration.test_clickable_citations_ui import _run_app_js_assertions

MARKDOWN = r"""
## Brief interpretation

You need a ranked city recommendation.

## Best matches

| Rank | Place | Why it fits | Main drawback | Confidence |
|---|---|---|---|---|
| 1 | Bucharest | Strong practical fit. | Some evidence is missing. | Medium |
| 2 | Seville | Very good budget fit. | Less complete evidence. | Medium |
| 3 | Valencia | Good transit fit. | Lower score in this backend order. | High |

## 1. Bucharest

- Why it fits: Strong practical fit.
- Main drawback: Some evidence is missing.
- Confidence: Medium, because not every requested criterion was verified.

## 2. Seville

- Why it fits: Very good budget fit.
- Main drawback: Less complete evidence.
- Confidence: Medium.

## 3. Valencia

- Why it fits: Good transit fit.
- Main drawback: Lower score in this backend order.
- Confidence: High.
"""


METRICS = r"""
[
  {
    module: "Recommendation Generator",
    response: {
      candidate_metrics: [
        { rank: 1, place: "Bucharest", total_score: 0.89, evidence_coverage: "Medium" },
        { rank: 2, place: "Seville", total_score: 0.96, evidence_coverage: "Medium" },
        { rank: 3, place: "Valencia", total_score: 0.79, evidence_coverage: "High" },
      ],
    },
  },
]
"""


def test_collapsed_card_shows_fit_score_and_evidence_coverage():
    _run_app_js_assertions(
        f"""
        const html = hooks.renderStructuredRecommendation({MARKDOWN!r}, "prompt", {METRICS});
        assert(html.includes("Fit 89"), "collapsed card should show rounded fit score");
        assert(html.includes("Medium"), "collapsed card should keep evidence coverage badge");
        assert(html.includes("Evidence Coverage"), "expanded metric block should label coverage");
        assert(html.includes("89/100"), "expanded metric block should show fit score out of 100");
        """
    )


def test_recommendation_cards_no_longer_label_the_metric_confidence():
    _run_app_js_assertions(
        f"""
        const html = hooks.renderStructuredRecommendation({MARKDOWN!r}, "prompt", {METRICS});
        assert(html.includes("Evidence Coverage"), "cards should use the new user-facing label");
        assert(!html.includes(">Confidence<"), "cards should not show Confidence as the metric title");
        """
    )


def test_backend_ranking_order_is_preserved_even_when_fit_scores_differ():
    _run_app_js_assertions(
        f"""
        const html = hooks.renderStructuredRecommendation({MARKDOWN!r}, "prompt", {METRICS});
        const first = html.indexOf("1. Bucharest");
        const second = html.indexOf("2. Seville");
        const third = html.indexOf("3. Valencia");
        assert(first > -1 && second > -1 && third > -1, "all cities should render");
        assert(first < second && second < third, "UI should preserve backend order instead of sorting by Fit Score");
        assert(html.indexOf("Fit 96") > html.indexOf("1. Bucharest"), "higher score can appear below rank 1");
        """
    )


def test_how_ranking_works_explanation_is_rendered():
    _run_app_js_assertions(
        f"""
        const html = hooks.renderStructuredRecommendation({MARKDOWN!r}, "prompt", {METRICS});
        assert(html.includes("How ranking works"), "results should explain the two metrics");
        assert(html.includes("Fit Score"), "explanation should define Fit Score");
        assert(html.includes("Evidence Coverage"), "explanation should define Evidence Coverage");
        const hasHardRequirementCaveat = html.includes("Hard requirements are prioritized before preference scores.");
        assert(hasHardRequirementCaveat, "hard requirement caveat is required");
        """
    )


def test_legacy_result_without_fit_score_does_not_crash():
    _run_app_js_assertions(
        f"""
        const html = hooks.renderStructuredRecommendation({MARKDOWN!r}, "prompt", []);
        assert(html.includes("Bucharest"), "legacy result should still render");
        assert(html.includes("Evidence Coverage"), "legacy result should still label the coverage badge");
        assert(!html.includes("Fit 89"), "missing legacy scores should not be invented");
        """
    )
