"""Climate-specific Wikivoyage evidence and deterministic corroboration scores."""

from __future__ import annotations

import html
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.climate_scoring import CLIMATE_TARGETS, clamp, climate_preference_directions, scale
from app.evidence.cache import ToolCache
from app.evidence.models import ToolResult
from app.tools.http_client import JsonHttpClient
from app.tools.mediawiki_client import WIKIVOYAGE_API, MediaWikiClient
from app.tools.wikivoyage_sections import CONTEXT_CONTRACT_VERSION, build_section_context

SOURCE_NAME = "Wikivoyage climate section"
MAX_EXCERPT_CHARS = 800
MAX_EVIDENCE_EXCERPT_CHARS = 240
LEXICON_VERSION = 1
MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

_PHRASE_RULES: dict[str, tuple[tuple[str, str], ...]] = {
    "temperature": (
        (r"\b(?:not|rarely)\s+(?:very\s+)?hot\b", "mild"),
        (r"\bmild\b", "mild"),
        (r"\bvery warm\b|\bwarm\b", "warm"),
        (r"\bscorching\b|\bvery hot\b|\bhot\b", "hot"),
        (r"\bcool\b", "cool"),
        (r"\bcold\b", "cold"),
    ),
    "extreme_heat": (
        (r"\b(?:no|little) extreme heat\b|\bextreme heat (?:is )?(?:rare|unusual)\b", "absent"),
        (r"\bextreme heat\b|\bheat ?waves?\b|\bscorching\b", "present"),
    ),
    "freezing": (
        (r"\bfrost (?:is |are )?(?:rare|nearly unknown)\b|\brarely freezes\b", "absent"),
        (r"\bfreez(?:e|es|ing)\b|\bfrost\b", "present"),
    ),
    "humidity": (
        (r"\bnot humid\b|\blow humidity\b|\bdry air\b", "low"),
        (r"\bvery humid\b|\bhumid\b|\bmuggy\b", "high"),
    ),
    "rain": (
        (r"\bvery dry\b|\bdry season\b|\blittle rain\b|\brain is rare\b", "dry"),
        (r"\bvery rainy\b|\brainy\b|\bfrequent rain\b|\bheavy rain\b", "rainy"),
    ),
    "sunshine": (
        (r"\bnot sunny\b|\bmostly cloudy\b|\bovercast\b", "cloudy"),
        (r"\bvery sunny\b|\bmostly sunny\b|\babundant sunshine\b|\bsunny\b", "sunny"),
    ),
    "snow": (
        (r"\bsnow (?:is |and frost are )?(?:rare|nearly unknown)\b|\blittle or no snow\b", "absent"),
        (r"\bsnowy\b|\bsnowfall\b|\bfrequent snow\b", "present"),
    ),
    "wind": (
        (r"\bnot windy\b|\blittle wind\b|\bcalm winds?\b", "calm"),
        (r"\bvery windy\b|\bstrong winds?\b|\bwindy\b", "windy"),
    ),
}


class _VisibleTextParser(HTMLParser):
    _SKIP_TAGS = {"table", "style", "script", "figure", "sup"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in {"p", "li", "br"} and not self._skip_depth:
            separator = " " if self.parts and self.parts[-1].rstrip().endswith((".", "!", "?")) else ". "
            self.parts.append(separator)

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _plain_text(rendered_html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(rendered_html)
    text = " ".join(html.unescape("".join(parser.parts)).split())
    return re.sub(r"\[\s*edit(?:\s*\|\s*edit source)?\s*\]", "", text, flags=re.I)


def _value(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return str(payload.get("*") or "")
    return ""


def _finite_number(raw: str) -> float | None:
    cleaned = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL).strip().replace(",", ".")
    match = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned)
    return float(cleaned) if match else None


def _extract_template(wikitext: str) -> str | None:
    lowered = wikitext.casefold()
    for template_name in ("climate chart", "weather box"):
        match = re.search(r"\{\{\s*" + re.escape(template_name) + r"\b", lowered)
        if not match:
            continue
        start = match.start()
        depth = 0
        index = start
        while index < len(wikitext) - 1:
            token = wikitext[index : index + 2]
            if token == "{{":
                depth += 1
                index += 2
                continue
            if token == "}}":
                depth -= 1
                index += 2
                if depth == 0:
                    return wikitext[start:index]
                continue
            index += 1
    return None


def _named_chart_values(parts: list[str]) -> dict[int, dict[str, float]]:
    values: dict[int, dict[str, float]] = defaultdict(dict)
    for part in parts:
        if "=" not in part:
            continue
        raw_key, raw_value = part.split("=", 1)
        number = _finite_number(raw_value)
        if number is None:
            continue
        key = re.sub(r"[^a-z0-9]", "", raw_key.casefold())
        month = next((index + 1 for index, name in enumerate(MONTH_NAMES) if name in key), None)
        if month is None:
            continue
        if any(label in key for label in ("highc", "maxc", "hightemp", "maximum")):
            values[month]["avg_high_c"] = number
        elif any(label in key for label in ("lowc", "minc", "lowtemp", "minimum")):
            values[month]["avg_low_c"] = number
        elif any(label in key for label in ("precip", "rainmm", "rainfall")):
            values[month]["precipitation_mm"] = number
    return dict(values)


def parse_climate_chart(wikitext: str) -> list[dict[str, float | int]]:
    """Parse common positional or named Wikivoyage climate-chart templates."""
    template = _extract_template(wikitext)
    if template is None:
        return []
    parts = template[2:-2].split("|")[1:]
    named = _named_chart_values(parts)
    if named:
        return [{"month": month, **values} for month, values in sorted(named.items())]

    positional = [_finite_number(part) for part in parts if "=" not in part]
    numbers = [number for number in positional if number is not None]
    if len(numbers) < 36:
        return []
    return [
        {
            "month": month,
            "avg_low_c": numbers[(month - 1) * 3],
            "avg_high_c": numbers[(month - 1) * 3 + 1],
            "precipitation_mm": numbers[(month - 1) * 3 + 2],
        }
        for month in range(1, 13)
    ]


def _active_seasons(months: list[int], latitude: float | None) -> set[str]:
    north = latitude is None or latitude >= 0
    mappings = (
        {"winter": {12, 1, 2}, "spring": {3, 4, 5}, "summer": {6, 7, 8}, "autumn": {9, 10, 11}, "fall": {9, 10, 11}}
        if north
        else {"summer": {12, 1, 2}, "autumn": {3, 4, 5}, "fall": {3, 4, 5}, "winter": {6, 7, 8}, "spring": {9, 10, 11}}
    )
    return {season for season, season_months in mappings.items() if season_months.intersection(months)}


def _sentence_relevant(sentence: str, months: list[int], latitude: float | None) -> bool:
    lowered = sentence.casefold()
    mentioned_seasons = {season for season in ("winter", "spring", "summer", "autumn", "fall") if season in lowered}
    if mentioned_seasons and not mentioned_seasons.intersection(_active_seasons(months, latitude)):
        return False
    mentioned_months = {index + 1 for index, name in enumerate(MONTH_NAMES) if re.search(rf"\b{name}\w*\b", lowered)}
    return not mentioned_months or bool(mentioned_months.intersection(months))


def _seasonal_fragments(sentence: str) -> list[str]:
    fragments = [part.strip() for part in re.split(r"[,;]", sentence) if part.strip()]
    refined: list[str] = []
    for fragment in fragments:
        seasons = [
            season
            for season in ("winter", "spring", "summer", "autumn", "fall")
            if season in fragment.casefold()
        ]
        if len(seasons) > 1 and re.search(r"\band\b", fragment, flags=re.I):
            refined.extend(part.strip() for part in re.split(r"\band\b", fragment, flags=re.I) if part.strip())
        else:
            refined.append(fragment)
    return refined


def _phrase_signals(text: str, months: list[int], latitude: float | None) -> dict[str, list[dict[str, str]]]:
    signals: dict[str, list[dict[str, str]]] = defaultdict(list)
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]
    for sentence in sentences:
        for fragment in _seasonal_fragments(sentence):
            if not _sentence_relevant(fragment, months, latitude):
                continue
            lowered = fragment.casefold()
            for dimension, rules in _PHRASE_RULES.items():
                for pattern, signal in rules:
                    if re.search(pattern, lowered):
                        signals[dimension].append(
                            {"signal": signal, "excerpt": fragment[:MAX_EVIDENCE_EXCERPT_CHARS]}
                        )
                        break
    return dict(signals)


def _signal_score(dimension: str, desired: str, signal: str) -> float:
    if dimension == "temperature":
        target, tolerance = CLIMATE_TARGETS[desired]
        signal_target, _ = CLIMATE_TARGETS[signal]
        return max(0.2, min(0.8, 0.8 - abs(signal_target - target) / (2 * tolerance)))
    desired_signal = {
        "extreme_heat": {"avoid": "absent"},
        "freezing": {"avoid": "absent"},
        "humidity": {"low": "low", "high": "high"},
        "rain": {"dry": "dry", "rainy": "rainy"},
        "sunshine": {"sunny": "sunny", "cloudy": "cloudy"},
        "snow": {"avoid": "absent", "snowy": "present"},
        "wind": {"calm": "calm", "windy": "windy"},
    }[dimension][desired]
    return 0.8 if signal == desired_signal else 0.2


def _component_scores(
    chart: list[dict[str, float | int]],
    signals: dict[str, list[dict[str, str]]],
    months: list[int],
    preferences: list[str],
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    directions = climate_preference_directions(preferences)
    selected_chart = [row for row in chart if row["month"] in months]
    scores: dict[str, float] = {}
    details: dict[str, dict[str, Any]] = {}
    for dimension, desired in directions.items():
        chart_score: float | None = None
        if dimension == "temperature":
            highs = [float(row["avg_high_c"]) for row in selected_chart if "avg_high_c" in row]
            if highs:
                target, tolerance = CLIMATE_TARGETS[desired]
                chart_score = clamp(1.0 - abs(sum(highs) / len(highs) - target) / tolerance)
        elif dimension == "rain":
            precipitation = [float(row["precipitation_mm"]) for row in selected_chart if "precipitation_mm" in row]
            if precipitation:
                wetness = scale(sum(precipitation) / len(precipitation), 20.0, 120.0)
                chart_score = 1.0 - wetness if desired == "dry" else wetness

        dimension_signals = signals.get(dimension, [])
        phrase_score = None
        if dimension_signals:
            phrase_values = [
                _signal_score(dimension, desired, item["signal"]) for item in dimension_signals
            ]
            phrase_score = sum(phrase_values) / len(phrase_values)

        available = []
        if chart_score is not None:
            available.append((0.7, chart_score))
        if phrase_score is not None:
            available.append((0.3, phrase_score))
        if not available:
            continue
        total_weight = sum(weight for weight, _ in available)
        score = sum(weight * value for weight, value in available) / total_weight
        scores[dimension] = round(score, 4)
        details[dimension] = {
            "chart_score": round(chart_score, 4) if chart_score is not None else None,
            "phrase_score": round(phrase_score, 4) if phrase_score is not None else None,
            "evidence_excerpts": [item["excerpt"] for item in dimension_signals],
        }
    return scores, details


class WikivoyageClimateTool:
    name = "WikivoyageClimateTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 10.0,
        mediawiki: MediaWikiClient | None = None,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._cache = cache
        self._mediawiki = mediawiki or MediaWikiClient(WIKIVOYAGE_API, JsonHttpClient(timeout=timeout))
        self._today = today

    async def _resolve(self, title: str) -> tuple[str, int, int, str]:
        payload = await self._mediawiki.request(
            action="query",
            prop="revisions",
            rvprop="ids|timestamp",
            titles=title,
            redirects=1,
        )
        pages = (payload.get("query") or {}).get("pages") or []
        if isinstance(pages, dict):
            pages = list(pages.values())
        page = next((item for item in pages if isinstance(item, dict) and "missing" not in item), None)
        if not page:
            raise ValueError("No Wikivoyage article was found for this destination")
        if not page.get("pageid") or not page.get("title"):
            raise ValueError("Wikivoyage did not return page identity for this article")
        revisions = page.get("revisions") or []
        revision = revisions[0] if revisions else {}
        if not revision.get("revid") or not revision.get("timestamp"):
            raise ValueError("Wikivoyage did not return revision identity for this article")
        return str(page["title"]), int(page["pageid"]), int(revision["revid"]), str(revision["timestamp"])

    async def _section(self, revision_id: int) -> tuple[dict[str, Any], str, str]:
        sections_payload = await self._mediawiki.request(
            action="parse", oldid=revision_id, prop="sections"
        )
        parsed = sections_payload.get("parse") or {}
        sections = parsed.get("sections") or []
        candidates = [
            section
            for section in sections
            if isinstance(section, dict) and re.search(r"\b(?:climate|weather)\b", str(section.get("line", "")), re.I)
        ]
        section = next((item for item in candidates if str(item.get("line", "")).casefold() == "climate"), None)
        section = section or (candidates[0] if candidates else None)
        if section is None:
            raise ValueError("The Wikivoyage article has no Climate section")
        section_payload = await self._mediawiki.request(
            action="parse",
            oldid=revision_id,
            section=str(section["index"]),
            prop="wikitext|text|revid",
        )
        section_parse = section_payload.get("parse") or {}
        if int(section_parse.get("revid") or 0) != revision_id:
            raise ValueError("Wikivoyage returned an unexpected revision for the Climate section")
        return section, _value(section_parse.get("wikitext")), _value(section_parse.get("text"))

    @staticmethod
    def _error(place: str, message: str, source_url: str | None = None) -> ToolResult:
        return ToolResult(
            tool_name=WikivoyageClimateTool.name,
            place=place,
            source_name=SOURCE_NAME,
            source_url=source_url,
            retrieved_at=datetime.now(UTC),
            confidence="low",
            error=message,
        )

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        title = candidate.canonical_name or candidate.place_name
        months = list(dict.fromkeys(profile.target_months))
        fallback_used = not months
        if fallback_used:
            months = [self._today().month]
        directions = climate_preference_directions(profile.climate_preferences)
        params = {
            "title": title,
            "months": months,
            "directions": directions,
            "latitude": candidate.lat,
            "lexicon_version": LEXICON_VERSION,
            "wikivoyage_context_contract_version": CONTEXT_CONTRACT_VERSION,
        }
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        source_url: str | None = None
        try:
            resolved_title, page_id, revision_id, revision_timestamp = await self._resolve(title)
            source_url = f"https://en.wikivoyage.org/w/index.php?oldid={revision_id}"
            section, wikitext, rendered_html = await self._section(revision_id)
            section_title = str(section.get("line") or "Climate")
            anchor = str(section.get("anchor") or section_title)
            source_url += f"#{quote(anchor)}"
            text = _plain_text(rendered_html)
            context = build_section_context(
                rendered_html,
                section_title,
                preview_chars=MAX_EXCERPT_CHARS,
                skip_tables=True,
            )
            chart = parse_climate_chart(wikitext)
            signals = _phrase_signals(text, months, candidate.lat)
            component_scores, component_details = _component_scores(
                chart, signals, months, profile.climate_preferences
            )
        except Exception as exc:  # noqa: BLE001 - stale revision evidence is preferable to losing the source
            if cached is not None:
                stale_result = ToolResult.model_validate(cached)
                stale_result.stale = True
                stale_result.warnings.append("Using stale cached Wikivoyage climate evidence; live lookup failed.")
                return stale_result
            return self._error(candidate.place_name, f"Wikivoyage climate lookup failed: {exc}", source_url)

        warnings = [
            "Wikivoyage is community-maintained; this deterministic climate reading is corroborating evidence."
        ]
        if not chart:
            warnings.append("No supported climate chart was found; only explicit climate statements were considered.")
        if not component_scores:
            warnings.append("The Climate section contained no evidence relevant to the requested climate dimensions.")
        if fallback_used:
            warnings.append("No structured target months were available; climate context uses the current month.")
        if context.truncated:
            warnings.append("The climate reasoning context was bounded with subsection coverage metadata.")
        normalized_data = {
            "resolved_title": resolved_title,
            "page_id": page_id,
            "section_title": section_title,
            "section_index": str(section["index"]),
            "section_anchor": anchor,
            "revision_id": revision_id,
            "revision_timestamp": revision_timestamp,
            "target_months": months,
            **context.normalized_data(),
            "climate_chart": chart,
            "phrase_signals": signals,
            "component_scores": component_scores,
            "component_details": component_details,
            "lexicon_version": LEXICON_VERSION,
        }
        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data=normalized_data,
            source_name=SOURCE_NAME,
            source_url=source_url,
            retrieved_at=datetime.now(UTC),
            data_date=f"Wikivoyage revision {revision_id} ({revision_timestamp})",
            confidence="medium" if component_scores else "low",
            warnings=warnings,
        )
        await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
