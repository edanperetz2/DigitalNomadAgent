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


def _trade_off(candidates: list[dict]) -> str:
    """Name the criterion the top two actually disagree on.

    "X is the strongest overall match, but Y may be preferable if its advantages
    matter more to you" is true of any ranked list and so tells the reader
    nothing (E5). The scores needed to say something specific were already on
    the payload: the criterion where the runner-up most outscores the leader is
    exactly the trade-off being made by taking rank 1.
    """
    if len(candidates) < 2:
        return "Only one verified candidate remained after evidence checks.\n"

    first, second = candidates[0], candidates[1]
    first_scores = first.get("criterion_scores") or {}
    second_scores = second.get("criterion_scores") or {}
    shared = set(first_scores) & set(second_scores)
    gains = {c: second_scores[c] - first_scores[c] for c in shared if second_scores[c] > first_scores[c]}

    lead = first.get("place")
    runner_up = second.get("place")
    if not gains:
        losses = {c: first_scores[c] - second_scores[c] for c in shared}
        if losses:
            widest = max(losses, key=lambda c: losses[c])
            return (
                f"{lead} beats {runner_up} on every criterion both were scored on, most clearly "
                f"on {widest} ({first_scores[widest]:.2f} vs {second_scores[widest]:.2f}), so "
                "there is no real trade-off between the top two.\n"
            )
        return (
            f"{lead} and {runner_up} were scored on different criteria, so they cannot be "
            "compared directly; read each on its own evidence.\n"
        )

    criterion = max(gains, key=lambda c: gains[c])
    return (
        f"Taking {lead} over {runner_up} costs you {criterion}: "
        f"{first_scores[criterion]:.2f} against {second_scores[criterion]:.2f}. "
        f"{lead} leads overall ({first.get('total_score', 0):.2f} vs "
        f"{second.get('total_score', 0):.2f}); if {criterion} matters more to you than the "
        f"balance of everything else, {runner_up} is the better pick.\n"
    )


def render_recommendation_markdown(payload: dict[str, Any]) -> str:
    candidates: list[dict] = payload.get("candidates", [])
    assumptions: list[str] = payload.get("assumptions", [])
    validation_issues: list[str] = payload.get("validation_issues", [])
    sources: list[dict] = payload.get("sources", [])
    purpose_summary: str = payload.get("purpose_summary", "your request")

    # Number each source once, so a claim can point into the bibliography
    # instead of the reader having to guess which of ~33 citations backed it (E4).
    source_numbers = {
        s.get("source_name"): i for i, s in enumerate(sources, start=1) if s.get("source_name")
    }

    def _missing(c: dict) -> str:
        return ", ".join(c.get("missing_evidence") or [])

    def _evidence_trail(c: dict) -> str:
        """Each scored criterion with the source numbers behind it."""
        attributed = c.get("criterion_sources") or {}
        parts = []
        for criterion in sorted(attributed):
            numbers = [source_numbers[name] for name in attributed[criterion] if name in source_numbers]
            if numbers:
                parts.append(f"{criterion} [{', '.join(str(n) for n in sorted(numbers))}]")
        return ", ".join(parts)

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
        trail = _evidence_trail(c)
        if trail:
            lines.append(f"- Evidence trail: {trail}")
        lines.append(f"- Confidence: {_confidence_label(c.get('confidence_score', 0.0))}")
        lines.append("")

    lines.append("### Trade-offs\n")
    lines.append(_trade_off(candidates))

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
