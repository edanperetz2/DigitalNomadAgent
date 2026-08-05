"""Shared deterministic markdown renderer for final recommendations.

Used both by MockLLMClient (to produce realistic deterministic output for
tests/demo) and by the Recommendation Generator's fallback path when the real
LLM is unavailable or fails -- so both paths always produce the same,
contract-compliant markdown structure from the spec (best-matches table,
per-place sections, trade-offs, assumptions/limitations, numbered sources).
"""

from __future__ import annotations

from typing import Any


def _confidence_label(score: float) -> str:
    if score >= 0.75:
        return "High"
    if score >= 0.45:
        return "Medium"
    return "Low"


def render_recommendation_markdown(payload: dict[str, Any]) -> str:
    candidates: list[dict] = payload.get("candidates", [])
    assumptions: list[str] = payload.get("assumptions", [])
    validation_issues: list[str] = payload.get("validation_issues", [])
    sources: list[dict] = payload.get("sources", [])
    purpose_summary: str = payload.get("purpose_summary", "your request")

    def _missing(c: dict) -> str:
        return ", ".join(c.get("missing_evidence") or [])

    def _why_it_fits(c: dict) -> str:
        """No advantage recorded is not the same as no evidence gathered.

        A candidate whose criteria all scored below the advantage threshold used
        to be described as a "Provisional match based on limited evidence" --
        Uppsala got that line while carrying a full set of scores. Its evidence
        was not limited, it was unfavourable, and the two need different
        sentences (the D11 rule, applied to the positive column).
        """
        advantages = c.get("advantages") or []
        if advantages:
            return advantages[0]
        missing = _missing(c)
        scores = c.get("criterion_scores") or {}
        if scores:
            weakest = ", ".join(sorted(scores, key=lambda name: scores[name])[:2])
            return (
                f"Nothing scored strongly here; it ranks on the balance of the evidence, "
                f"weakest on {weakest}"
            )
        if missing:
            return f"Ranked on partial evidence; {missing} could not be verified"
        return "Provisional match based on limited evidence"

    def _main_drawback(c: dict) -> str:
        """Never report absent evidence as an absent drawback.

        "No major drawback identified" was printed alongside "no verified data
        for work_infrastructure, timezone" -- the two criteria the request
        actually hinged on. Silence is not reassurance, and saying so turned a
        gap into a false positive.
        """
        drawbacks = c.get("drawbacks") or []
        if drawbacks:
            return drawbacks[0]
        missing = _missing(c)
        if missing:
            return f"Not assessed: no verified data for {missing}"
        return "No major drawback identified"

    lines: list[str] = []
    lines.append(f"## Interpretation\n\nThis recommendation addresses {purpose_summary}.\n")

    lines.append("## Best matches\n")
    lines.append("| Rank | Place | Why it fits | Main drawback | Confidence |")
    lines.append("|---:|---|---|---|---|")
    for i, c in enumerate(candidates, start=1):
        why = _why_it_fits(c)
        drawback = _main_drawback(c)
        label = _confidence_label(c.get("confidence_score", 0.0))
        lines.append(f"| {i} | {c.get('place', 'Unknown')} | {why} | {drawback} | {label} |")
    lines.append("")

    for i, c in enumerate(candidates, start=1):
        lines.append(f"### {i}. {c.get('place', 'Unknown')}, {c.get('country', '')}".rstrip(", "))
        lines.append("")
        for adv in c.get("advantages", []) or [_why_it_fits(c)]:
            lines.append(f"- Why it fits: {adv}")
        for draw in c.get("drawbacks", []) or [_main_drawback(c)]:
            lines.append(f"- Main drawback: {draw}")
        if c.get("missing_evidence"):
            missing = ", ".join(c["missing_evidence"])
            lines.append(f"- Evidence limitations: no verified data for {missing}.")
        lines.append(f"- Confidence: {_confidence_label(c.get('confidence_score', 0.0))}")
        lines.append("")

    lines.append("### Trade-offs\n")
    if len(candidates) >= 2:
        lines.append(
            f"{candidates[0].get('place')} is the strongest overall match, but "
            f"{candidates[1].get('place')} may be preferable if its advantages "
            "matter more to you than to the top pick.\n"
        )
    else:
        lines.append("Only one verified candidate remained after evidence checks.\n")

    lines.append("### Assumptions and limitations\n")
    if assumptions:
        for a in assumptions:
            lines.append(f"- {a}")
    else:
        lines.append("- No explicit assumptions were required.")
    if validation_issues:
        for issue in validation_issues:
            lines.append(f"- {issue}")
    lines.append(
        "- Visa, entry, and admission rules change frequently; always confirm with official "
        "sources before booking or applying. No live prices, flight availability, or guaranteed "
        "admission/visa eligibility are claimed here."
    )
    lines.append("")

    lines.append("### Sources\n")
    if sources:
        for i, s in enumerate(sources, start=1):
            url = s.get("source_url") or "n/a"
            retrieved = s.get("retrieved_at") or "n/a"
            details = [f"retrieved {retrieved}"]
            if s.get("data_date"):
                details.append(f"data {s['data_date']}")
            if s.get("confidence"):
                details.append(f"{s['confidence']} confidence")
            if s.get("stale"):
                details.append("stale fallback")
            lines.append(
                f"{i}. {s.get('source_name', 'Unknown source')} — {url} — " + " — ".join(details)
            )
    else:
        lines.append("1. No external sources were available for this request.")

    return "\n".join(lines)
