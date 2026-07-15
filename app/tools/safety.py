"""Composite, source-visible destination safety evidence."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult
from app.tools.http_client import JsonHttpClient
from app.tools.mediawiki_client import WIKIVOYAGE_API, MediaWikiClient
from app.tools.wikivoyage_sections import (
    CONTEXT_CONTRACT_VERSION,
    WikivoyageSection,
    WikivoyageSectionClient,
    WikivoyageSectionNotFound,
)

FCDO_API_ROOT = "https://www.gov.uk/api/content/foreign-travel-advice"
WORLD_BANK_API_ROOT = "https://api.worldbank.org/v2/country"
WORLD_BANK_INDICATOR = "VC.IHR.PSRC.P5"
WORLD_BANK_INDICATOR_NAME = "Intentional homicides (per 100,000 people)"
WORLD_BANK_SOURCE_URL = f"https://data.worldbank.org/indicator/{WORLD_BANK_INDICATOR}"

COMPONENT_WEIGHTS = {"fcdo_advisory": 0.40, "homicide_rate": 0.35, "wikivoyage_stay_safe": 0.25}
LEXICON_VERSION = 1
SUBLOOKUP_TIMEOUT_SECONDS = 12.0

FCDO_STATUS_SCORES = {
    "avoid_all_but_essential_travel_to_parts": 0.75,
    "avoid_all_travel_to_parts": 0.50,
    "avoid_all_but_essential_travel_to_whole_country": 0.25,
    "avoid_all_travel_to_whole_country": 0.0,
}
FCDO_SLUG_BY_COUNTRY_CODE = {
    "CI": "cote-d-ivoire",
    "CZ": "czechia",
    "GB": "united-kingdom",
    "KR": "south-korea",
    "KP": "north-korea",
    "US": "usa",
}

_LEXICON: dict[str, tuple[str, ...]] = {
    "reassurance": (
        r"\bgenerally safe\b",
        r"\brelatively safe\b",
        r"\bvery safe\b",
        r"\bcrime (?:rate )?is low\b",
        r"\blow crime (?:rate|level)?\b",
    ),
    "petty_crime": (
        r"\bpetty crime\b",
        r"\bpickpocket(?:ing|s)?\b",
        r"\bscams?\b",
        r"\btheft\b",
        r"\bharassment\b",
    ),
    "serious_risk": (
        r"\bviolent crime\b",
        r"\bkidnapp(?:ing|ed|ings)?\b",
        r"\barmed robbery\b",
        r"\bsexual assault\b",
        r"\bdangerous areas?\b",
        r"\bavoid (?:the |this |these |those )?(?:area|areas|district|districts|neighbourhood|neighbourhoods)\b",
    ),
}
_CATEGORY_DELTAS = {"reassurance": 0.05, "petty_crime": -0.08, "serious_risk": -0.18}
_CATEGORY_CAPS = {"reassurance": 0.10, "petty_crime": -0.24, "serious_risk": -0.54}


@dataclass(frozen=True)
class ComponentResult:
    score: float
    normalized_data: dict[str, Any]
    source_name: str
    source_url: str
    data_date: str | None
    confidence: Literal["high", "medium", "low"]
    warnings: list[str]


def _slugify_country(country: str) -> str:
    normalized = unicodedata.normalize("NFKD", country)
    ascii_text = "".join(character for character in normalized if not unicodedata.combining(character))
    return "-".join(re.findall(r"[a-z0-9]+", ascii_text.casefold()))


def fcdo_slug(country: str, country_code: str | None) -> str:
    code = (country_code or "").strip().upper()
    return FCDO_SLUG_BY_COUNTRY_CODE.get(code) or _slugify_country(country)


def score_fcdo_statuses(statuses: list[str]) -> tuple[float, str]:
    if not statuses:
        return 1.0, "no_active_warning"
    unknown = sorted(set(statuses) - FCDO_STATUS_SCORES.keys())
    if unknown:
        raise ValueError(f"GOV.UK returned unknown travel-advice status(es): {', '.join(unknown)}")
    most_severe = min(statuses, key=lambda status: FCDO_STATUS_SCORES[status])
    return FCDO_STATUS_SCORES[most_severe], most_severe


def homicide_score(rate: float) -> float:
    if rate < 0:
        raise ValueError("World Bank returned a negative homicide rate")
    points = ((1.0, 1.0), (3.0, 0.85), (6.0, 0.70), (10.0, 0.55), (20.0, 0.35))
    if rate <= points[0][0]:
        return points[0][1]
    if rate > points[-1][0]:
        return 0.15
    for (lower_rate, lower_score), (upper_rate, upper_score) in zip(points, points[1:], strict=False):
        if rate <= upper_rate:
            fraction = (rate - lower_rate) / (upper_rate - lower_rate)
            return round(lower_score + fraction * (upper_score - lower_score), 4)
    raise AssertionError("unreachable homicide threshold")


def _phrase_is_negated(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 45) : start]
    after = text[end : min(len(text), end + 45)]
    if re.search(r"\bnot\s+(?:rare|uncommon|unlikely)\b", before[-25:] + after[:25]):
        return False
    return bool(
        re.search(r"\b(?:no|not|never|rarely|hardly|little|low)\b(?:\W+\w+){0,3}\W*$", before)
        or re.match(r"^(?:\W+\w+){0,3}\W+\b(?:rare|uncommon|unlikely|low)\b", after)
    )


def analyze_stay_safe(text: str) -> tuple[float, dict[str, list[str]], dict[str, float]]:
    lowered = " ".join(text.casefold().split())
    matches: dict[str, list[str]] = {category: [] for category in _LEXICON}
    for category, patterns in _LEXICON.items():
        for pattern in patterns:
            for match in re.finditer(pattern, lowered):
                if _phrase_is_negated(lowered, match.start(), match.end()):
                    continue
                phrase = match.group(0)
                if phrase not in matches[category]:
                    matches[category].append(phrase)

    adjustments: dict[str, float] = {}
    for category, phrases in matches.items():
        raw = len(phrases) * _CATEGORY_DELTAS[category]
        cap = _CATEGORY_CAPS[category]
        adjustments[category] = round(min(raw, cap) if raw > 0 else max(raw, cap), 4)
    score = max(0.0, min(1.0, 0.75 + sum(adjustments.values())))
    return round(score, 4), matches, adjustments


def _all_context_text(section: WikivoyageSection) -> str:
    return "\n".join(chunk.text for chunk in section.context.context_chunks)


def _exception_text(exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _http_status(exc: BaseException) -> int | None:
    return exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None


def _mark_stale(result: ToolResult) -> ToolResult:
    warning = "Live safety sources failed; returning expired cached evidence."
    result.stale = True
    result.confidence = "low"
    result.warnings.append(warning)
    for item in result.evidence_items:
        item.source.stale = True
        item.source.confidence = "low"
        item.warnings.append(warning)
    return result


class SafetyTool:
    name = "SafetyTool"

    def __init__(
        self,
        cache: ToolCache,
        timeout: float = 10.0,
        *,
        http: JsonHttpClient | None = None,
        wikivoyage: WikivoyageSectionClient | None = None,
        sublookup_timeout: float = SUBLOOKUP_TIMEOUT_SECONDS,
    ) -> None:
        self._cache = cache
        self._http = http or JsonHttpClient(timeout=timeout)
        self._wikivoyage = wikivoyage or WikivoyageSectionClient(MediaWikiClient(WIKIVOYAGE_API, self._http))
        self._sublookup_timeout = sublookup_timeout

    async def _fcdo(self, candidate: CandidatePlace) -> ComponentResult:
        slug = fcdo_slug(candidate.country, candidate.country_code)
        if not slug:
            raise ValueError("A verified destination country is required for FCDO evidence")
        url = f"{FCDO_API_ROOT}/{slug}"
        payload = await self._http.get_json(url)
        if not isinstance(payload, dict):
            raise ValueError("GOV.UK returned a non-object response")
        details = payload.get("details")
        if not isinstance(details, dict) or not isinstance(details.get("alert_status", []), list):
            raise ValueError("GOV.UK returned malformed travel-advice details")
        statuses = [str(status) for status in details.get("alert_status", [])]
        score, most_severe = score_fcdo_statuses(statuses)
        country_data = details.get("country") if isinstance(details.get("country"), dict) else {}
        reviewed_at = details.get("reviewed_at")
        updated_at = payload.get("public_updated_at")
        return ComponentResult(
            score=score,
            normalized_data={
                "score": score,
                "active_statuses": statuses,
                "most_severe_status": most_severe,
                "resolved_country": country_data.get("name"),
                "country_slug": slug,
                "reviewed_at": reviewed_at,
                "public_updated_at": updated_at,
            },
            source_name="UK FCDO foreign travel advice",
            source_url=url,
            data_date=str(updated_at or reviewed_at) if (updated_at or reviewed_at) else None,
            confidence="high",
            warnings=["Country-level government travel advice is not a city crime rating."],
        )

    async def _homicide(self, candidate: CandidatePlace) -> ComponentResult:
        country_code = (candidate.country_code or "").strip().upper()
        if len(country_code) != 2:
            raise ValueError("A verified ISO alpha-2 country code is required for homicide evidence")
        url = f"{WORLD_BANK_API_ROOT}/{country_code}/indicator/{WORLD_BANK_INDICATOR}"
        payload = await self._http.get_json(url, params={"format": "json", "per_page": 100})
        if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
            raise ValueError("World Bank returned a malformed indicator response")
        observations = [
            item
            for item in payload[1]
            if isinstance(item, dict) and isinstance(item.get("value"), (int, float))
        ]
        if not observations:
            raise LookupError("World Bank has no homicide observation for this country")
        latest = max(observations, key=lambda item: int(str(item.get("date"))))
        rate = float(latest["value"])
        score = homicide_score(rate)
        return ComponentResult(
            score=score,
            normalized_data={
                "score": score,
                "rate_per_100k": rate,
                "year": str(latest.get("date")),
                "country": (latest.get("country") or {}).get("value"),
                "country_code": country_code,
                "indicator_code": WORLD_BANK_INDICATOR,
                "indicator_name": WORLD_BANK_INDICATOR_NAME,
                "indicator_page_url": WORLD_BANK_SOURCE_URL,
            },
            source_name="World Bank / UNODC intentional homicide indicator",
            source_url=f"{url}?format=json&per_page=100",
            data_date=str(latest.get("date")),
            confidence="medium",
            warnings=[
                "This is the latest available country-level annual homicide rate, not a current city-level measure."
            ],
        )

    async def _stay_safe(self, title: str) -> ComponentResult:
        section = await self._wikivoyage.fetch(title, ("Stay safe", "Stay Safe"))
        score, matches, adjustments = analyze_stay_safe(_all_context_text(section))
        return ComponentResult(
            score=score,
            normalized_data={
                **section.normalized_data(),
                "score": score,
                "lexicon_version": LEXICON_VERSION,
                "matched_phrases": matches,
                "category_adjustments": adjustments,
                "baseline_score": 0.75,
            },
            source_name=f"Wikivoyage {section.section_title} section",
            source_url=section.source_url,
            data_date=f"Wikivoyage revision {section.revision_id} ({section.revision_timestamp})",
            confidence="medium",
            warnings=[
                "Wikivoyage is community-maintained context; phrase analysis is deterministic and not universal."
            ],
        )

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult:
        del profile
        now = datetime.now(UTC)
        title = candidate.canonical_name or candidate.place_name
        params = {
            "country": candidate.country,
            "country_code": candidate.country_code,
            "title": title,
            "indicator": WORLD_BANK_INDICATOR,
            "fcdo_mapping_version": 1,
            "lexicon_version": LEXICON_VERSION,
            "wikivoyage_context_contract_version": CONTEXT_CONTRACT_VERSION,
        }
        cached, stale = await self._cache.get(self.name, candidate.place_name, params)
        if cached is not None and not stale:
            return ToolResult.model_validate(cached)

        task_names = ("fcdo_advisory", "homicide_rate", "wikivoyage_stay_safe")
        calls = (self._fcdo(candidate), self._homicide(candidate), self._stay_safe(title))
        outcomes = dict(
            zip(
                task_names,
                await asyncio.gather(
                    *(asyncio.wait_for(call, timeout=self._sublookup_timeout) for call in calls),
                    return_exceptions=True,
                ),
                strict=True,
            )
        )

        components: dict[str, ComponentResult] = {}
        statuses: dict[str, str] = {}
        warnings = ["This composite supports comparison; it is not an objective or universal city-safety rating."]
        transient_failure = False
        for name, outcome in outcomes.items():
            if isinstance(outcome, ComponentResult):
                components[name] = outcome
                statuses[name] = "available"
                continue
            missing = isinstance(outcome, (LookupError, WikivoyageSectionNotFound)) or _http_status(outcome) == 404
            statuses[name] = "missing" if missing else "error"
            transient_failure = transient_failure or not missing
            warnings.append(f"{name.replace('_', ' ').title()} is unavailable: {_exception_text(outcome)}")

        if not components and cached is not None:
            return _mark_stale(ToolResult.model_validate(cached))

        available_weight = sum(COMPONENT_WEIGHTS[name] for name in components)
        composite_score = (
            round(
                sum(COMPONENT_WEIGHTS[name] * component.score for name, component in components.items())
                / available_weight,
                4,
            )
            if len(components) >= 2
            else None
        )
        if composite_score is None:
            warnings.append("At least two independent safety components are required for a composite score.")

        evidence_items = [
            EvidenceItem(
                criterion="safety",
                component=name,
                value=component.score,
                normalized_data=component.normalized_data,
                source=EvidenceSource(
                    source_name=component.source_name,
                    source_url=component.source_url,
                    retrieved_at=now,
                    data_date=component.data_date,
                    confidence=component.confidence,
                ),
                warnings=component.warnings,
            )
            for name, component in components.items()
        ]
        confidence: Literal["medium", "low"] = "medium" if len(components) == 3 else "low"
        result = ToolResult(
            tool_name=self.name,
            place=candidate.place_name,
            normalized_data={
                "composite_score": composite_score,
                "component_scores": {name: component.score for name, component in components.items()},
                "configured_weights": COMPONENT_WEIGHTS,
                "renormalized_weights": (
                    {name: round(COMPONENT_WEIGHTS[name] / available_weight, 4) for name in components}
                    if available_weight
                    else {}
                ),
                "component_status": statuses,
                "available_component_count": len(components),
                "minimum_components_required": 2,
                "rating_scope": "comparative_destination_evidence_not_universal_city_rating",
            },
            source_name="FCDO, World Bank / UNODC, and Wikivoyage",
            retrieved_at=now,
            confidence=confidence,
            warnings=warnings,
            error="No safety evidence could be retrieved." if not components else None,
            evidence_items=evidence_items,
        )
        if not transient_failure:
            await self._cache.set(self.name, candidate.place_name, params, result.model_dump(mode="json"))
        return result
