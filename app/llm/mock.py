"""MockLLMClient: deterministic, zero-cost stand-in for the real provider.

Used by default (MOCK_LLM=true) so `pytest` and local development never make a
paid call. Behavior is keyed off `metadata["module"]` (set by TracedLLMClient)
plus the actual message content, so it is a real (if simplified) deterministic
interpreter/generator rather than a random or hardcoded-only stub.
"""

from __future__ import annotations

import json
import re

from app.agent.candidate_funnel import budget_comparison, estimate_affordability
from app.core.module_names import (
    AGENTIC_RESEARCH,
    DYNAMIC_EVALUATION,
    RECOMMENDATION_GENERATOR,
    REQUEST_INTERPRETER,
)
from app.core.rendering import render_recommendation_markdown
from app.geography import region_contains
from app.llm.base import BaseLLMClient, LLMRawResponse

# ---------------------------------------------------------------------------
# Request Interpreter: deterministic keyword/regex-based parser
# ---------------------------------------------------------------------------

# Word-boundary regexes, not bare substrings. The previous list-of-substrings
# form missed ordinary phrasings -- "cleared to work fully remote" matched none
# of "remote work"/"work remotely"/"remote job" -- and a miss was catastrophic,
# because interpret_prompt then returned early without extracting the budget,
# region, duration or constraints the user had actually stated.
_STUDY_PATTERN = re.compile(
    r"\b(?:study|studies|studying|student|exchange|semester|degree|university|"
    r"master'?s|phd|academic|college|undergrad(?:uate)?)\b"
)
_REMOTE_PATTERN = re.compile(
    # "work ... remote(ly)" and "remote(ly) ... work" in either order, allowing
    # a few words between ("work fully remote", "remote-first work").
    r"\bwork\w*\b[^.]{0,24}\bremote\w*\b"
    r"|\bremote\w*\b[^.]{0,24}\bwork\w*\b"
    r"|\bdigital nomad\b"
    r"|\bwork(?:ing)? from (?:home|anywhere)\b"
    r"|\bwfh\b"
    r"|\btelework\w*\b"
    r"|\bremote[- ]friendly\b"
    r"|\boverlap with\b"
    r"|\bworking hours\b"
)
_VACATION_PATTERN = re.compile(
    r"\b(?:vacation|holiday|trip|sightseeing|beach|beaches|getaway|"
    r"travell?ing|travel|tourist)\b"
    r"|\btwo weeks\b"
)

_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}

_CLIMATE_WORDS = ["warm", "hot", "cold", "mild", "sunny", "not extremely hot", "cool"]

_AMENITY_ALIASES: dict[str, tuple[str, ...]] = {
    "coworking": ("coworking", "co-working"),
    "cafe": ("cafes", "cafe", "cafés", "café", "coffee shops", "coffee shop"),
    "university": ("universities", "university"),
    "library": ("libraries", "library"),
    "park": ("green spaces", "green space", "parks", "park"),
    "pharmacy": ("pharmacies", "pharmacy"),
    "supermarket": ("supermarkets", "supermarket", "grocery stores", "grocery store"),
    "fitness_centre": (
        "fitness centres",
        "fitness centre",
        "fitness centers",
        "fitness center",
        "gyms",
        "gym",
    ),
}

_KNOWN_UNSUPPORTED_AMENITY_ALIASES: dict[str, tuple[str, ...]] = {
    "hospital": ("hospitals", "hospital"),
    "swimming pool": ("swimming pools", "swimming pool"),
}

_ACTIVITY_ALIASES: dict[str, tuple[str, ...]] = {
    "culture": (
        "culture",
        "cultural sites",
        "cultural attractions",
        "museums",
        "museum",
        "art galleries",
        "art gallery",
        "historical sites",
        "historic sites",
        "history",
    ),
    "nightlife": ("nightlife", "night clubs", "night club", "nightclubs", "nightclub", "bars", "pubs"),
    "parks": ("green spaces", "green space", "parks", "park", "gardens", "garden"),
    "beaches": ("beaches", "beach"),
    "hiking": ("hiking", "hikes", "hike", "walking trails", "walking trail", "trails", "trail"),
}

_KNOWN_UNSUPPORTED_ACTIVITY_ALIASES: dict[str, tuple[str, ...]] = {
    "surfing": ("surfing", "surf"),
    "skiing": ("skiing", "ski"),
    "diving": ("scuba diving", "diving", "scuba"),
}

_NEGATED_AMENITY_PREFIX = re.compile(
    r"(?:avoid|without|no|don't need|do not need|not interested in|don't care about|do not care about)"
    r"[^,.]{0,24}$"
)
_NEGATED_ACTIVITY_PREFIX = _NEGATED_AMENITY_PREFIX

_CRITERIA_KEYWORD_MAP = {
    "public transportation": "transportation",
    "public transport": "transportation",
    "without a car": "transportation",
    "safety": "safety",
    "safe": "safety",
    "student life": "student_life",
    "affordable housing": "cost",
    "housing": "cost",
    "hiking": "activities",
    "beach": "activities",
    "internet": "work_infrastructure",
    "coworking": "work_infrastructure",
    "wifi": "work_infrastructure",
    "time zone": "timezone",
    "timezone": "timezone",
    "overlap": "timezone",
    "working hours": "timezone",
    "weather": "climate",
    "climate": "climate",
    "museum": "culture",
    "cultural": "culture",
    "nightlife": "nightlife",
}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
}


def _detect_purposes(text: str) -> set[str]:
    found = set()
    if _REMOTE_PATTERN.search(text):
        found.add("remote_work")
    if _STUDY_PATTERN.search(text):
        found.add("study")
    if _VACATION_PATTERN.search(text):
        found.add("vacation")
    return found


_BUDGET_PERIOD_PATTERNS: tuple[tuple[str, str], ...] = (
    # "a month" and friends were missing entirely, so the most natural phrasing
    # of all -- "EUR 1,200 a month" -- fell through to "unknown".
    ("monthly", r"per month|/ ?month|a month|each month|every month|monthly|\bp/?m\b|\bpcm\b"),
    ("weekly", r"per week|/ ?week|a week|each week|every week|weekly"),
    ("daily", r"per day|/ ?day|a day|each day|every day|per night|a night|daily"),
    ("total", r"\btotal\b|\ball[- ]?in\b|\baltogether\b|\bin total\b"),
)


def _extract_budget(text: str) -> dict:
    amount = None
    currency = None
    match = re.search(r"([€$£])\s?([\d,]+)", text)
    if match:
        currency = _CURRENCY_SYMBOLS.get(match.group(1))
        amount = float(match.group(2).replace(",", ""))
    else:
        match = re.search(r"([\d,]+)\s?(EUR|USD|GBP|euros|dollars)", text, re.IGNORECASE)
        if match:
            amount = float(match.group(1).replace(",", ""))
            currency_word = match.group(2).lower()
            currency = {"euros": "EUR", "dollars": "USD"}.get(currency_word, currency_word.upper())

    # Scoped to the sentence stating the amount, not the whole prompt. Searching
    # globally let an unrelated word set the budget period: "EUR 1,200 a month
    # ... reliable internet for daily video calls" was read as a DAILY budget,
    # a 30x error on the constraint the request hinges on.
    scope = _sentence_around(text.lower(), match.start()) if match else ""
    period = "unknown"
    confidence = "low"
    for candidate_period, pattern in _BUDGET_PERIOD_PATTERNS:
        if re.search(pattern, scope, re.IGNORECASE):
            period = candidate_period
            confidence = "high"
            break

    includes_accommodation = None
    if re.search(r"excluding accommodation|not including accommodation|excluding rent", text, re.IGNORECASE):
        includes_accommodation = False
    elif re.search(r"including rent|including accommodation", text, re.IGNORECASE):
        includes_accommodation = True

    return {
        "amount": amount,
        "currency": currency,
        "period": period,
        "includes_accommodation": includes_accommodation,
        "confidence": confidence,
    }


def _extract_duration(text: str) -> str | None:
    match = re.search(r"(\d+)\s?(day|days|week|weeks|month|months|semester|semesters|year|years)", text, re.IGNORECASE)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    for word, num in _NUMBER_WORDS.items():
        match = re.search(rf"\b{word}\b\s+(day|days|week|weeks|month|months|semester|semesters)", text, re.IGNORECASE)
        if match:
            return f"{num} {match.group(1)}"
    if "one-semester" in text or "one semester" in text:
        return "1 semester"
    return None


_MONTH_NUMBERS: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "sept": 9, "october": 10,
    "november": 11, "december": 12,
}
# "may" and "march" are ordinary English words far more often than months, so
# they only count when capitalised. The rest are safe case-insensitively.
_CASE_SENSITIVE_MONTHS = frozenset({"may", "march"})
_MONTH_ALTERNATION = "|".join(sorted(_MONTH_NUMBERS, key=len, reverse=True))
_MONTH_RANGE_PATTERN = re.compile(
    rf"\b({_MONTH_ALTERNATION})\b\s*(?:through|thru|to|until|till|-|–|—)\s*\b({_MONTH_ALTERNATION})\b",
    re.IGNORECASE,
)
_MONTH_TOKEN_PATTERN = re.compile(rf"\b({_MONTH_ALTERNATION})\b", re.IGNORECASE)
# Northern-hemisphere reading; disclosed as an assumption wherever it is used.
_SEASON_MONTHS: dict[str, list[int]] = {
    "spring": [3, 4, 5],
    "summer": [6, 7, 8],
    "autumn": [9, 10, 11],
    "fall": [9, 10, 11],
    "winter": [12, 1, 2],
}
_SEASON_PATTERN = re.compile(r"\b(" + "|".join(_SEASON_MONTHS) + r")\b", re.IGNORECASE)
_MONTH_COUNT_PATTERN = re.compile(
    r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")[\s-]+months?\b", re.IGNORECASE
)


def _month_token_value(token: str) -> int | None:
    """A month name only counts if it is unambiguous, or capitalised when not."""
    lowered = token.lower()
    if lowered in _CASE_SENSITIVE_MONTHS and not token[:1].isupper():
        return None
    return _MONTH_NUMBERS.get(lowered)


def _month_span(start: int, count: int) -> list[int]:
    return [(start - 1 + offset) % 12 + 1 for offset in range(count)]


def _stated_month_count(text: str) -> int | None:
    match = _MONTH_COUNT_PATTERN.search(text)
    if not match:
        return None
    raw = match.group(1).lower()
    if raw.isdigit():
        return int(raw)
    value = _NUMBER_WORDS.get(raw)
    return int(value) if value else None


def _extract_target_months(text: str) -> tuple[list[int], str | None]:
    """Calendar months the stay covers, plus an assumption line when one is owed.

    Nothing populated this before: the field existed on the profile but was
    absent from the interpreter's field list, so it arrived empty on every
    request and WeatherTool silently fell back to *the current calendar month*.
    An October trip and a November-April winter escape were both scored against
    August climatology, which is why "climate fit is weak" was the stated main
    drawback almost everywhere (D31).
    """
    span = _MONTH_RANGE_PATTERN.search(text)
    if span:
        start = _month_token_value(span.group(1))
        end = _month_token_value(span.group(2))
        if start and end:
            count = (end - start) % 12 + 1
            return _month_span(start, count), None

    named = [
        value
        for match in _MONTH_TOKEN_PATTERN.finditer(text)
        if (value := _month_token_value(match.group(1))) is not None
    ]
    if named:
        unique = list(dict.fromkeys(named))
        count = _stated_month_count(text)
        if len(unique) == 1 and count and count > 1:
            return _month_span(unique[0], min(count, 12)), None
        return unique, None

    season = _SEASON_PATTERN.search(text)
    if season:
        name = season.group(1).lower()
        return (
            list(_SEASON_MONTHS[name]),
            f"Read \"{name}\" as the northern-hemisphere months; say so if you meant the southern hemisphere.",
        )

    return [], None


_EXCLUDED_REGION_PATTERN = re.compile(
    r"\b(?:avoid|skip|excluding|not interested in)\s+"
    r"([A-Z][\w'-]*(?:\s+(?:and|,)\s+[A-Z][\w'-]*)*)"
)


# Macro-regions a user is likely to name. Deliberately a fixed vocabulary: the
# real interpreter reasons about geography properly, and a loose "capitalised
# word after 'in'" heuristic would happily read "in October" as a region.
_KNOWN_REGIONS = (
    "western europe", "eastern europe", "northern europe", "southern europe",
    "central europe", "scandinavia", "the balkans", "the mediterranean", "europe",
    "southeast asia", "south east asia", "south asia", "east asia", "central asia",
    "the middle east", "asia",
    "north africa", "sub-saharan africa", "africa",
    "central america", "south america", "latin america", "north america",
    "the caribbean", "oceania",
)
_PREFERRED_REGION_PATTERN = re.compile(
    r"\b(?:somewhere |anywhere )?(?:in|within|around|across)\s+(" + "|".join(_KNOWN_REGIONS) + r")\b",
    re.IGNORECASE,
)
# "not in Europe", "avoid Southeast Asia" must never read as a preference.
_NEGATED_REGION_PREFIX = re.compile(r"(?:avoid|not|except|outside|skip|excluding|no)\s+\w*\s*$", re.IGNORECASE)


def _extract_preferred_regions(text: str) -> list[str]:
    """Positive region preferences, e.g. "somewhere in Europe".

    Previously hard-coded to [] -- so a stated region was silently ignored and
    candidates could come from anywhere in the world.
    """
    regions: list[str] = []
    for match in _PREFERRED_REGION_PATTERN.finditer(text):
        if _NEGATED_REGION_PREFIX.search(text[max(0, match.start() - 24) : match.start()]):
            continue
        region = match.group(1).strip()
        canonical = region.title() if not region.lower().startswith("the ") else region
        if canonical not in regions:
            regions.append(canonical)
    # Keep only the most specific match when both "Europe" and "Southern Europe"
    # were found, so the profile does not carry a redundant broader region.
    return [r for r in regions if not any(r != other and r.lower() in other.lower() for other in regions)]


# "I've settled on Lisbon", "thinking about Porto", "is Berlin a good fit"
# Case-insensitivity is scoped to the trigger phrase only -- the place name
# itself must stay capitalised, which is what distinguishes it from ordinary
# prose ("thinking about the weather" must not yield a destination).
_PLACE = r"([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)"
_NAMED_DESTINATION_PATTERNS = (
    re.compile(r"\b(?i:settled on|decided on|thinking about|considering|leaning towards?|set on)\s+" + _PLACE),
    re.compile(r"\b(?i:is)\s+" + _PLACE + r"\s+(?i:actually\s+)?(?i:a good|the right|right|suitable)\b"),
    re.compile(r"\b(?i:how|what)\s+(?i:about)\s+" + _PLACE),
    # A bare place after a destination preposition: "prices for Bali", "go to
    # Tokyo", "about Buenos Aires". Every trigger above needs the user to
    # deliberate out loud, so a request that simply *names* where it means went
    # unrecorded -- P10 asked about Bali and was answered with a ranked list of
    # Israeli cities, the named place never having reached the profile (D32).
    # "of"/"from" are deliberately excluded: they mark an origin, not a target.
    re.compile(r"\b(?i:for|to|about|visit|visiting)\s+" + _PLACE),
)

# Capitalised words that follow those prepositions without naming a place.
_NOT_A_DESTINATION = frozenset(
    set(_MONTH_NUMBERS)
    | set(_SEASON_MONTHS)
    | {region.casefold() for region in _KNOWN_REGIONS}
    | {region.removeprefix("the ").casefold() for region in _KNOWN_REGIONS}
)


def _extract_named_destinations(text: str) -> list[str]:
    """Specific places the user named and wants judged.

    Kept out of preferred_regions, which is only ever matched against a
    candidate's country: a city placed there matches nothing, so the named
    place was eliminated and the user's actual question went unanswered.
    """
    names: list[str] = []
    for pattern in _NAMED_DESTINATION_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1).strip()
            if not name or name.casefold() in _NOT_A_DESTINATION:
                continue
            if name not in names:
                names.append(name)
    return names


def _extract_excluded_regions(text: str) -> list[str]:
    """Best-effort capitalized-phrase heuristic; the real LLM reasons about this properly."""
    regions: list[str] = []
    for match in _EXCLUDED_REGION_PATTERN.finditer(text):
        for part in re.split(r"\s*(?:,|\band\b)\s*", match.group(1)):
            if part.strip():
                regions.append(part.strip())
    return regions


def _extract_amenity_preferences(text: str) -> list[str]:
    preferences: list[str] = []
    category_aliases = {**_AMENITY_ALIASES, **_KNOWN_UNSUPPORTED_AMENITY_ALIASES}
    for category, aliases in category_aliases.items():
        requested = False
        for alias in aliases:
            for match in re.finditer(rf"\b{re.escape(alias)}\b", text):
                prefix = text[max(0, match.start() - 64) : match.start()]
                if not _NEGATED_AMENITY_PREFIX.search(prefix):
                    requested = True
                    break
            if requested:
                break
        if requested:
            preferences.append(category)
    return preferences


def _extract_activity_preferences(text: str) -> list[str]:
    preferences: list[str] = []
    aliases_by_category = {**_ACTIVITY_ALIASES, **_KNOWN_UNSUPPORTED_ACTIVITY_ALIASES}
    for category, aliases in aliases_by_category.items():
        requested = False
        for alias in aliases:
            for match in re.finditer(rf"\b{re.escape(alias)}\b", text):
                prefix = text[max(0, match.start() - 64) : match.start()]
                if not _NEGATED_ACTIVITY_PREFIX.search(prefix):
                    requested = True
                    break
            if requested:
                break
        if requested:
            preferences.append(category)
    return preferences


# Intensity phrases, strongest first. Matched in a window around each criterion
# rather than across the whole prompt: the previous form tested the entire text,
# so one "most important" anywhere set *every* criterion to 0.9 and the common
# case set them all to a flat 0.5 -- ranking could not reflect priorities at all.
_INTENSITY_PATTERNS: tuple[tuple[float, str], ...] = (
    (1.0, r"top priority|matters most|most important|number one|single most|absolutely must|"
          r"non-negotiable|deal ?breaker"),
    (0.9, r"very important|really important|genuinely important|crucial|essential|vital|"
          r"matters a lot|big constraint|\bmust\b|\brequired\b|\bneed\b"),
    (0.6, r"\bprefer\b|would like|\bimportant\b|\bcare about\b|\bwant\b|\bideally\b"),
    (0.3, r"would be nice|nice to have|\bbonus\b|not fussy|don'?t mind|if possible|\bslight"),
)
_RANKED_ORDER_PATTERN = re.compile(r"\bin order\b|\bin priority order\b|\bmatters,? (?:roughly )?in\b")
_SENTENCE_BREAKS = ".;!?\n"


def _sentence_around(lowered: str, position: int) -> str:
    """The sentence containing `position`.

    Scoped to a sentence rather than a fixed character window so intensity does
    not bleed across clauses: in "Safety is my top priority. Mild weather would
    be nice", a fixed window let "top priority" capture the climate criterion.
    """
    start = max((lowered.rfind(c, 0, position) for c in _SENTENCE_BREAKS), default=-1) + 1
    ends = [e for e in (lowered.find(c, position) for c in _SENTENCE_BREAKS) if e != -1]
    return lowered[start : min(ends, default=len(lowered))]


def _weight_near(lowered: str, position: int, length: int) -> float:
    """Weight for one criterion, from the strongest intensity phrase near it."""
    del length
    sentence = _sentence_around(lowered, position)
    for weight, pattern in _INTENSITY_PATTERNS:
        if re.search(pattern, sentence):
            return weight
    return 0.5


def _apply_ranked_order(
    lowered: str, relevant_criteria: list[str], inferred_weights: dict[str, float]
) -> None:
    """Honour an explicitly ranked list ("what matters, roughly in order: ...").

    Criteria are re-weighted by the order they appear in the prompt, descending,
    so an ordering the user spelled out is actually reflected in the ranking.
    """
    if not _RANKED_ORDER_PATTERN.search(lowered) or len(relevant_criteria) < 2:
        return
    ordered = sorted(
        relevant_criteria,
        key=lambda c: min(
            (lowered.find(kw) for kw, crit in _CRITERIA_KEYWORD_MAP.items() if crit == c and kw in lowered),
            default=len(lowered),
        ),
    )
    for rank, criterion in enumerate(ordered):
        inferred_weights[criterion] = round(max(0.4, 0.95 - 0.1 * rank), 2)


def interpret_prompt(prompt: str) -> dict:
    """Deterministic, rule-based interpretation used by MockLLMClient."""
    text = prompt.strip()
    lowered = text.lower()

    purposes_found = _detect_purposes(lowered)
    secondary_purposes: list[str] = []
    unknown_purpose = not purposes_found
    if unknown_purpose:
        # Deliberately falls through to the full extraction below rather than
        # returning a stub. An unrecognised purpose says nothing about whether
        # the user stated a budget, a region or a hard constraint, and returning
        # early discarded all of them -- the request then ran with a completely
        # empty profile and produced confident recommendations that ignored
        # every stated requirement.
        purpose = "unknown"
    elif len(purposes_found) == 1:
        purpose = next(iter(purposes_found))
    else:
        purpose = "mixed"
        secondary_purposes = sorted(purposes_found)

    budget_info = _extract_budget(text)
    assumptions: list[str] = []
    if budget_info["amount"] is not None and budget_info["confidence"] == "low":
        inferred_period = "total" if purpose == "vacation" else "monthly"
        budget_info["period"] = inferred_period
        assumptions.append(f"Assumed the stated budget of {budget_info['amount']} is a {inferred_period} amount.")
    if budget_info["includes_accommodation"] is None and budget_info["amount"] is not None:
        budget_info["includes_accommodation"] = True
        assumptions.append("Assumed the budget includes accommodation unless stated otherwise.")

    duration = _extract_duration(text)
    target_months, month_assumption = _extract_target_months(text)
    if month_assumption:
        assumptions.append(month_assumption)
    climate_preferences = [w for w in _CLIMATE_WORDS if w in lowered]
    activity_preferences = _extract_activity_preferences(lowered)
    amenity_preferences = _extract_amenity_preferences(lowered)

    mobility_requirements = []
    if re.search(
        r"without a car|car-free|car free|no car|"
        r"(?:won'?t|will not|do ?n'?t|do not) (?:have|need|be bringing) a car|"
        r"(?:do ?n'?t|do not) drive",
        lowered,
    ):
        mobility_requirements.append("car-free")
    if "public transport" in lowered:
        mobility_requirements.append("public_transport_reliant")
    if "walkable" in lowered:
        mobility_requirements.append("walkable")

    origin = None
    if "israeli" in lowered or "israel" in lowered:
        origin = "Israel"
    from_match = re.search(r"\bfrom ([A-Z][a-zA-Z]+)", text)
    if from_match:
        origin = from_match.group(1)

    hard_constraints = []
    for m in re.finditer(r"\b(must|required|non-negotiable)\b[^.]{0,60}", lowered):
        hard_constraints.append(m.group(0).strip())
    # A stated coordination target ("four hours of overlap with US Eastern") is a
    # hard requirement, and TimezoneFitTool reads the named timezone from it.
    for m in re.finditer(r"\b(?:\w+[ -])?hours? of overlap with\b[^.]{0,60}", lowered):
        hard_constraints.append(m.group(0).strip())
    deal_breakers = []
    for m in re.finditer(r"\b(avoid|never)\b[^.]{0,60}", lowered):
        deal_breakers.append(m.group(0).strip())
    preferred_regions = _extract_preferred_regions(text)
    named_destinations = _extract_named_destinations(text)
    excluded_regions = _extract_excluded_regions(text)
    soft_preferences = []
    for m in re.finditer(r"\bprefer\b[^.]{0,60}", lowered):
        soft_preferences.append(m.group(0).strip())

    relevant_criteria = []
    inferred_weights = {}
    for kw, criterion in _CRITERIA_KEYWORD_MAP.items():
        position = lowered.find(kw)
        if position == -1 or criterion in relevant_criteria:
            continue
        relevant_criteria.append(criterion)
        inferred_weights[criterion] = _weight_near(lowered, position, len(kw))
    _apply_ranked_order(lowered, relevant_criteria, inferred_weights)
    if re.search(r"do not care about (\w+)|don't care about (\w+)", lowered):
        for m in re.finditer(r"do not care about (\w+)|don't care about (\w+)", lowered):
            dropped = (m.group(1) or m.group(2))
            inferred_weights.pop(dropped, None)
            if dropped in relevant_criteria:
                relevant_criteria.remove(dropped)

    missing_information: list[str] = []
    clarification_required = False
    clarification_question = None
    if unknown_purpose:
        clarification_required = True
        clarification_question = (
            "Could you clarify the main purpose of this trip (remote work, study, "
            "vacation, or something else), your approximate budget, and how long "
            "you plan to stay?"
        )
        missing_information.append("purpose")
    if "visa" in lowered and origin is None:
        missing_information.append("nationality (for visa considerations)")

    return {
        "purpose": purpose,
        "secondary_purposes": secondary_purposes,
        "duration": duration,
        "dates_or_season": None,
        "target_months": target_months,
        "origin": origin,
        "nationality": None,
        "preferred_regions": preferred_regions,
        "excluded_regions": excluded_regions,
        "named_destinations": named_destinations,
        "preferred_languages": [],
        "mobility_requirements": mobility_requirements,
        "climate_preferences": climate_preferences,
        "activity_preferences": activity_preferences,
        "amenity_preferences": amenity_preferences,
        "budget": budget_info,
        "hard_constraints": hard_constraints,
        "soft_preferences": soft_preferences,
        "deal_breakers": deal_breakers,
        "relevant_criteria": relevant_criteria,
        "inferred_weights": inferred_weights,
        "missing_information": missing_information,
        "assumptions": assumptions,
        "clarification_required": clarification_required,
        "clarification_question": clarification_question,
    }


# ---------------------------------------------------------------------------
# Agentic Research: deterministic candidate seed pool
# ---------------------------------------------------------------------------

_SEED_CANDIDATES: dict[str, list[dict]] = {
    "remote_work": [
        {
            "place_name": "Lisbon", "country": "Portugal",
            "reason_for_inclusion": "Strong conventional remote-work hub with established digital-nomad infra.",
            "expected_strengths": ["coworking spaces", "public transport", "mild climate"],
            "likely_weakness": "Rising cost of living in central areas.",
            "criteria_to_verify": ["work_infrastructure", "cost", "climate"],
        },
        {
            "place_name": "Tbilisi", "country": "Georgia",
            "reason_for_inclusion": "Budget-oriented alternative with a growing remote-work and coworking scene.",
            "expected_strengths": ["low cost of living", "growing coworking scene"],
            "likely_weakness": "Fewer direct international flight connections.",
            "criteria_to_verify": ["cost", "transportation"],
        },
        {
            "place_name": "Berlin", "country": "Germany",
            "reason_for_inclusion": "Strongest match for public-transport reliance and work infrastructure.",
            "expected_strengths": ["extensive public transport", "large coworking network"],
            "likely_weakness": "Higher cost and colder winters.",
            "criteria_to_verify": ["transportation", "work_infrastructure", "climate"],
        },
        {
            "place_name": "Tirana", "country": "Albania",
            "reason_for_inclusion": "Less obvious discovery with a fast-growing remote-work community.",
            "expected_strengths": ["low cost", "warm climate"],
            "likely_weakness": "Limited coworking options outside the capital.",
            "criteria_to_verify": ["cost", "work_infrastructure"],
        },
        {
            "place_name": "Mexico City", "country": "Mexico",
            "reason_for_inclusion": "Different compromise: excellent culture/food scene, favorable US-hours overlap.",
            "expected_strengths": ["timezone overlap with the Americas", "vibrant culture"],
            "likely_weakness": "Air quality and traffic congestion.",
            "criteria_to_verify": ["timezone", "culture"],
        },
        {
            "place_name": "Chiang Mai", "country": "Thailand",
            "reason_for_inclusion": "Long-established digital-nomad base with a very low cost of living.",
            "expected_strengths": ["low cost", "large nomad community"],
            "likely_weakness": "Seasonal air-quality issues from agricultural burning.",
            "criteria_to_verify": ["cost", "climate"],
        },
        {
            "place_name": "Canggu", "country": "Indonesia",
            "reason_for_inclusion": "Coastal remote-work hub with a dense coworking/cafe scene.",
            "expected_strengths": ["coworking cafes", "warm climate"],
            "likely_weakness": "Traffic congestion and infrastructure strain.",
            "criteria_to_verify": ["work_infrastructure", "climate"],
        },
        {
            "place_name": "Budapest", "country": "Hungary",
            "reason_for_inclusion": "Central-European hub with strong transit and moderate costs.",
            "expected_strengths": ["public transport", "moderate cost"],
            "likely_weakness": "Air quality dips in winter inversions.",
            "criteria_to_verify": ["transportation", "cost"],
        },
        {
            "place_name": "Buenos Aires", "country": "Argentina",
            "reason_for_inclusion": "Large coworking scene with favorable currency-driven affordability.",
            "expected_strengths": ["affordability", "vibrant culture"],
            "likely_weakness": "Economic volatility can affect pricing stability.",
            "criteria_to_verify": ["cost", "culture"],
        },
        {
            "place_name": "Medellin", "country": "Colombia",
            "reason_for_inclusion": "Mild year-round climate and an established nomad community.",
            "expected_strengths": ["mild climate", "coworking spaces"],
            "likely_weakness": "Security varies notably by neighborhood.",
            "criteria_to_verify": ["climate", "safety"],
        },
        {
            "place_name": "Prague", "country": "Czechia",
            "reason_for_inclusion": "Reliable infrastructure and extensive public transport network.",
            "expected_strengths": ["public transport", "coworking network"],
            "likely_weakness": "Higher cost than nearby regional alternatives.",
            "criteria_to_verify": ["transportation", "cost"],
        },
        {
            "place_name": "Ho Chi Minh City", "country": "Vietnam",
            "reason_for_inclusion": "Very low cost of living with a fast-growing coworking scene.",
            "expected_strengths": ["low cost", "growing coworking scene"],
            "likely_weakness": "Dense traffic and limited pedestrian infrastructure.",
            "criteria_to_verify": ["cost", "transportation"],
        },
        {
            "place_name": "Cape Town", "country": "South Africa",
            "reason_for_inclusion": "Strong coworking infrastructure with a favorable cost/climate mix.",
            "expected_strengths": ["mild climate", "coworking spaces"],
            "likely_weakness": "Periodic power-supply interruptions.",
            "criteria_to_verify": ["climate", "work_infrastructure"],
        },
        {
            "place_name": "Split", "country": "Croatia",
            "reason_for_inclusion": "Coastal alternative with a dedicated digital-nomad visa program.",
            "expected_strengths": ["coastal setting", "nomad-visa support"],
            "likely_weakness": "Costs rise sharply during the summer tourist season.",
            "criteria_to_verify": ["cost", "climate"],
        },
        {
            "place_name": "Riga", "country": "Latvia",
            "reason_for_inclusion": "Compact, walkable city with reliable public transport.",
            "expected_strengths": ["public transport", "walkable center"],
            "likely_weakness": "Long, dark winters.",
            "criteria_to_verify": ["transportation", "climate"],
        },
        {
            "place_name": "Da Nang", "country": "Vietnam",
            "reason_for_inclusion": "Beach-adjacent nomad hub with low living costs.",
            "expected_strengths": ["low cost", "beach access"],
            "likely_weakness": "Smaller coworking ecosystem than Ho Chi Minh City.",
            "criteria_to_verify": ["cost", "work_infrastructure"],
        },
        {
            "place_name": "Bansko", "country": "Bulgaria",
            "reason_for_inclusion": "Purpose-built nomad community with very low costs.",
            "expected_strengths": ["low cost", "dedicated coworking spaces"],
            "likely_weakness": "Limited outside the core nomad-focused area.",
            "criteria_to_verify": ["cost", "work_infrastructure"],
        },
        {
            "place_name": "Krakow", "country": "Poland",
            "reason_for_inclusion": "Established tech scene with moderate costs and solid transit.",
            "expected_strengths": ["tech scene", "public transport"],
            "likely_weakness": "Winter air-quality concerns.",
            "criteria_to_verify": ["work_infrastructure", "climate"],
        },
        {
            "place_name": "Valencia", "country": "Spain",
            "reason_for_inclusion": "Mediterranean climate with a growing remote-work community.",
            "expected_strengths": ["mild climate", "coworking spaces"],
            "likely_weakness": "Rising rents in central neighborhoods.",
            "criteria_to_verify": ["climate", "cost"],
        },
        {
            "place_name": "Taipei", "country": "Taiwan",
            "reason_for_inclusion": "Excellent infrastructure and a very high safety profile.",
            "expected_strengths": ["safety", "public transport"],
            "likely_weakness": "Humid, hot summers.",
            "criteria_to_verify": ["safety", "climate"],
        },
        {
            "place_name": "Barcelona", "country": "Spain",
            "reason_for_inclusion": "Large coworking and cafe density with strong transit.",
            "expected_strengths": ["coworking density", "public transport"],
            "likely_weakness": "High demand drives up housing costs.",
            "criteria_to_verify": ["work_infrastructure", "cost"],
        },
        {
            "place_name": "Playa del Carmen", "country": "Mexico",
            "reason_for_inclusion": "Beach-adjacent nomad hub with US-timezone overlap.",
            "expected_strengths": ["timezone overlap", "beach access"],
            "likely_weakness": "Tourist-driven price spikes in high season.",
            "criteria_to_verify": ["timezone", "cost"],
        },
        {
            "place_name": "Tallinn", "country": "Estonia",
            "reason_for_inclusion": "Digital-first city with strong infrastructure and e-residency ties.",
            "expected_strengths": ["digital infrastructure", "coworking spaces"],
            "likely_weakness": "Cold, dark winters.",
            "criteria_to_verify": ["work_infrastructure", "climate"],
        },
        {
            "place_name": "Cluj-Napoca", "country": "Romania",
            "reason_for_inclusion": "Growing tech hub with low costs relative to Western Europe.",
            "expected_strengths": ["low cost", "tech scene"],
            "likely_weakness": "Fewer direct international flights.",
            "criteria_to_verify": ["cost", "transportation"],
        },
        {
            "place_name": "Ubud", "country": "Indonesia",
            "reason_for_inclusion": "Quieter alternative to the coastal nomad hubs, still well-connected.",
            "expected_strengths": ["coworking cafes", "low cost"],
            "likely_weakness": "Further from the main international airport.",
            "criteria_to_verify": ["cost", "transportation"],
        },
        {
            "place_name": "Belgrade", "country": "Serbia",
            "reason_for_inclusion": "Low costs with a fast-growing IT and coworking scene.",
            "expected_strengths": ["low cost", "growing coworking scene"],
            "likely_weakness": "Limited English signage outside central areas.",
            "criteria_to_verify": ["cost", "work_infrastructure"],
        },
        {
            "place_name": "Bucharest", "country": "Romania",
            "reason_for_inclusion": "Strong internet infrastructure and low costs.",
            "expected_strengths": ["internet speed", "low cost"],
            "likely_weakness": "Traffic congestion in central districts.",
            "criteria_to_verify": ["work_infrastructure", "cost"],
        },
        {
            "place_name": "Kuala Lumpur", "country": "Malaysia",
            "reason_for_inclusion": "Modern infrastructure with a favorable cost-to-quality ratio.",
            "expected_strengths": ["public transport", "coworking spaces"],
            "likely_weakness": "Hot, humid climate year-round.",
            "criteria_to_verify": ["work_infrastructure", "climate"],
        },
        {
            "place_name": "Antalya", "country": "Turkey",
            "reason_for_inclusion": "Warm climate alternative with a growing nomad presence.",
            "expected_strengths": ["warm climate", "low cost"],
            "likely_weakness": "Coworking scene is smaller than in major cities.",
            "criteria_to_verify": ["climate", "cost"],
        },
        {
            "place_name": "Montevideo", "country": "Uruguay",
            "reason_for_inclusion": "Stable, safe alternative in South America with mild climate.",
            "expected_strengths": ["safety", "mild climate"],
            "likely_weakness": "Smaller coworking scene than regional peers.",
            "criteria_to_verify": ["safety", "climate"],
        },
    ],
    "study": [
        {
            "place_name": "Berlin", "country": "Germany",
            "reason_for_inclusion": "Strong conventional match for academic exchange with a large student population.",
            "expected_strengths": ["large student population", "public transport"],
            "likely_weakness": "Competitive housing market for students.",
            "criteria_to_verify": ["student_life", "transportation", "cost"],
        },
        {
            "place_name": "Warsaw", "country": "Poland",
            "reason_for_inclusion": "Budget-oriented alternative with a growing tech/academic scene.",
            "expected_strengths": ["low cost of living", "active student life"],
            "likely_weakness": "Winters can be harsh.",
            "criteria_to_verify": ["cost", "student_life"],
        },
        {
            "place_name": "Dublin", "country": "Ireland",
            "reason_for_inclusion": "English-speaking study environment with a large student community.",
            "expected_strengths": ["student community", "English-speaking environment"],
            "likely_weakness": "High accommodation costs.",
            "criteria_to_verify": ["safety", "cost"],
        },
        {
            "place_name": "Porto", "country": "Portugal",
            "reason_for_inclusion": "Less obvious discovery: lower costs than the capital, growing student community.",
            "expected_strengths": ["affordability", "compact walkable city"],
            "likely_weakness": "Smaller university ecosystem than larger capitals.",
            "criteria_to_verify": ["cost", "student_life"],
        },
        {
            "place_name": "Melbourne", "country": "Australia",
            "reason_for_inclusion": "Alternative compromise: excellent student life and safety, higher cost/distance.",
            "expected_strengths": ["student life", "safety"],
            "likely_weakness": "Higher cost and long-haul travel distance.",
            "criteria_to_verify": ["student_life", "cost", "transportation"],
        },
        {
            "place_name": "Amsterdam", "country": "Netherlands",
            "reason_for_inclusion": "Large international student population with excellent cycling/transit access.",
            "expected_strengths": ["student community", "public transport"],
            "likely_weakness": "Very tight student housing market.",
            "criteria_to_verify": ["student_life", "cost"],
        },
        {
            "place_name": "Barcelona", "country": "Spain",
            "reason_for_inclusion": "Popular exchange destination with strong student life and mild climate.",
            "expected_strengths": ["student life", "mild climate"],
            "likely_weakness": "Rising rents for short-term student housing.",
            "criteria_to_verify": ["student_life", "cost"],
        },
        {
            "place_name": "Vienna", "country": "Austria",
            "reason_for_inclusion": "High safety and quality of life for international students.",
            "expected_strengths": ["safety", "student life"],
            "likely_weakness": "Higher cost than Central-European peers.",
            "criteria_to_verify": ["safety", "cost"],
        },
        {
            "place_name": "Krakow", "country": "Poland",
            "reason_for_inclusion": "Large historic university with low living costs.",
            "expected_strengths": ["low cost", "student community"],
            "likely_weakness": "Winter air quality can be poor.",
            "criteria_to_verify": ["cost", "student_life"],
        },
        {
            "place_name": "Lyon", "country": "France",
            "reason_for_inclusion": "Major student city with lower costs than Paris.",
            "expected_strengths": ["student life", "public transport"],
            "likely_weakness": "Fewer English-speaking options outside major campuses.",
            "criteria_to_verify": ["student_life", "transportation"],
        },
        {
            "place_name": "Bologna", "country": "Italy",
            "reason_for_inclusion": "One of Europe's oldest university cities with a dense student scene.",
            "expected_strengths": ["student community", "walkable center"],
            "likely_weakness": "Aging housing stock in the historic center.",
            "criteria_to_verify": ["student_life", "cost"],
        },
        {
            "place_name": "Edinburgh", "country": "United Kingdom",
            "reason_for_inclusion": "Strong English-speaking academic environment with high safety.",
            "expected_strengths": ["safety", "student community"],
            "likely_weakness": "High accommodation costs.",
            "criteria_to_verify": ["safety", "cost"],
        },
        {
            "place_name": "Montreal", "country": "Canada",
            "reason_for_inclusion": "Large bilingual student population with relatively low tuition.",
            "expected_strengths": ["student life", "public transport"],
            "likely_weakness": "Very cold winters.",
            "criteria_to_verify": ["student_life", "transportation"],
        },
        {
            "place_name": "Toronto", "country": "Canada",
            "reason_for_inclusion": "Large, diverse student population with strong transit.",
            "expected_strengths": ["student community", "public transport"],
            "likely_weakness": "High cost of living.",
            "criteria_to_verify": ["student_life", "cost"],
        },
        {
            "place_name": "Seoul", "country": "South Korea",
            "reason_for_inclusion": "Extensive student infrastructure with very high transit reliability.",
            "expected_strengths": ["public transport", "student life"],
            "likely_weakness": "Language barrier outside major English-speaking campuses.",
            "criteria_to_verify": ["transportation", "student_life"],
        },
        {
            "place_name": "Singapore", "country": "Singapore",
            "reason_for_inclusion": "Very high safety with strong university infrastructure.",
            "expected_strengths": ["safety", "student life"],
            "likely_weakness": "Higher cost of living than regional peers.",
            "criteria_to_verify": ["safety", "cost"],
        },
        {
            "place_name": "Utrecht", "country": "Netherlands",
            "reason_for_inclusion": "Compact, bike-friendly student city.",
            "expected_strengths": ["student life", "public transport"],
            "likely_weakness": "Limited housing supply for students.",
            "criteria_to_verify": ["student_life", "cost"],
        },
        {
            "place_name": "Leuven", "country": "Belgium",
            "reason_for_inclusion": "Small, historic university town with a dense student community.",
            "expected_strengths": ["student community", "walkable center"],
            "likely_weakness": "Limited nightlife/activity variety outside term time.",
            "criteria_to_verify": ["student_life", "activities"],
        },
        {
            "place_name": "Coimbra", "country": "Portugal",
            "reason_for_inclusion": "Historic university city with low costs.",
            "expected_strengths": ["low cost", "student community"],
            "likely_weakness": "Smaller city with fewer amenities.",
            "criteria_to_verify": ["cost", "student_life"],
        },
        {
            "place_name": "Groningen", "country": "Netherlands",
            "reason_for_inclusion": "One of the most bike/transit-friendly student cities in Europe.",
            "expected_strengths": ["public transport", "student life"],
            "likely_weakness": "Remote from major international airports.",
            "criteria_to_verify": ["transportation", "student_life"],
        },
        {
            "place_name": "Uppsala", "country": "Sweden",
            "reason_for_inclusion": "High safety and strong student community near Stockholm.",
            "expected_strengths": ["safety", "student life"],
            "likely_weakness": "High cost of living.",
            "criteria_to_verify": ["safety", "cost"],
        },
        {
            "place_name": "Copenhagen", "country": "Denmark",
            "reason_for_inclusion": "Excellent cycling/transit infrastructure and student safety.",
            "expected_strengths": ["public transport", "safety"],
            "likely_weakness": "Very high cost of living.",
            "criteria_to_verify": ["transportation", "cost"],
        },
        {
            "place_name": "Milan", "country": "Italy",
            "reason_for_inclusion": "Major academic and business hub with a large student population.",
            "expected_strengths": ["student community", "public transport"],
            "likely_weakness": "Higher cost than other Italian university cities.",
            "criteria_to_verify": ["student_life", "cost"],
        },
        {
            "place_name": "Brno", "country": "Czechia",
            "reason_for_inclusion": "Large student city with notably low costs.",
            "expected_strengths": ["low cost", "student community"],
            "likely_weakness": "Smaller international community than Prague.",
            "criteria_to_verify": ["cost", "student_life"],
        },
        {
            "place_name": "Ljubljana", "country": "Slovenia",
            "reason_for_inclusion": "Compact, safe student city with low costs.",
            "expected_strengths": ["safety", "low cost"],
            "likely_weakness": "Small international student network.",
            "criteria_to_verify": ["safety", "cost"],
        },
        {
            "place_name": "Wellington", "country": "New Zealand",
            "reason_for_inclusion": "High safety and quality of life for international students.",
            "expected_strengths": ["safety", "student life"],
            "likely_weakness": "Long-haul travel distance for most students.",
            "criteria_to_verify": ["safety", "transportation"],
        },
        {
            "place_name": "Auckland", "country": "New Zealand",
            "reason_for_inclusion": "Large university with a diverse international student body.",
            "expected_strengths": ["student community", "safety"],
            "likely_weakness": "Higher cost of living.",
            "criteria_to_verify": ["student_life", "cost"],
        },
        {
            "place_name": "Kyoto", "country": "Japan",
            "reason_for_inclusion": "Historic academic city with very high safety.",
            "expected_strengths": ["safety", "student community"],
            "likely_weakness": "Language barrier outside major English-speaking campuses.",
            "criteria_to_verify": ["safety", "student_life"],
        },
        {
            "place_name": "Cork", "country": "Ireland",
            "reason_for_inclusion": "English-speaking alternative to Dublin with lower costs.",
            "expected_strengths": ["low cost", "student community"],
            "likely_weakness": "Smaller international student network than Dublin.",
            "criteria_to_verify": ["cost", "student_life"],
        },
        {
            "place_name": "Glasgow", "country": "United Kingdom",
            "reason_for_inclusion": "Large student population with lower costs than Edinburgh.",
            "expected_strengths": ["student life", "low cost"],
            "likely_weakness": "Frequent rain and overcast weather.",
            "criteria_to_verify": ["student_life", "climate"],
        },
    ],
    "vacation": [
        {
            "place_name": "Valencia", "country": "Spain",
            "reason_for_inclusion": "Strong conventional match for warm-but-not-extreme weather and beach access.",
            "expected_strengths": ["mild warm climate", "beaches"],
            "likely_weakness": "Can be crowded in peak season.",
            "criteria_to_verify": ["climate", "activities"],
        },
        {
            "place_name": "Tirana Riviera (Sarande)", "country": "Albania",
            "reason_for_inclusion": "Budget-oriented alternative beach destination.",
            "expected_strengths": ["low cost", "coastal scenery"],
            "likely_weakness": "Less developed tourism infrastructure.",
            "criteria_to_verify": ["cost", "activities"],
        },
        {
            "place_name": "Santorini", "country": "Greece",
            "reason_for_inclusion": "Strongest match for scenic beaches and hiking-adjacent coastal trails.",
            "expected_strengths": ["scenery", "hiking trails", "beaches"],
            "likely_weakness": "Higher prices and crowds in high season.",
            "criteria_to_verify": ["activities", "cost"],
        },
        {
            "place_name": "Kotor", "country": "Montenegro",
            "reason_for_inclusion": "Less obvious discovery combining beaches with nearby mountain hiking.",
            "expected_strengths": ["hiking access", "coastal views"],
            "likely_weakness": "Smaller town with fewer amenities.",
            "criteria_to_verify": ["activities", "climate"],
        },
        {
            "place_name": "Nice", "country": "France",
            "reason_for_inclusion": "Different compromise: strong infrastructure and culture, at a higher cost.",
            "expected_strengths": ["culture", "accessibility"],
            "likely_weakness": "More expensive than other Mediterranean options.",
            "criteria_to_verify": ["cost", "culture"],
        },
        {
            "place_name": "Dubrovnik", "country": "Croatia",
            "reason_for_inclusion": "Coastal old-town destination with strong cultural attractions.",
            "expected_strengths": ["culture", "beaches"],
            "likely_weakness": "Very crowded in peak summer season.",
            "criteria_to_verify": ["activities", "cost"],
        },
        {
            "place_name": "Split", "country": "Croatia",
            "reason_for_inclusion": "Beach access combined with easy island-hopping activities.",
            "expected_strengths": ["beaches", "activities"],
            "likely_weakness": "Accommodation prices spike in summer.",
            "criteria_to_verify": ["activities", "cost"],
        },
        {
            "place_name": "Positano", "country": "Italy",
            "reason_for_inclusion": "Scenic coastal hiking with dramatic Amalfi Coast views.",
            "expected_strengths": ["hiking", "scenery"],
            "likely_weakness": "Among the most expensive Mediterranean options.",
            "criteria_to_verify": ["cost", "activities"],
        },
        {
            "place_name": "Lagos", "country": "Portugal",
            "reason_for_inclusion": "Algarve beach town with lower costs than Spanish/Italian coastal peers.",
            "expected_strengths": ["beaches", "low cost"],
            "likely_weakness": "Smaller nightlife scene outside peak season.",
            "criteria_to_verify": ["cost", "activities"],
        },
        {
            "place_name": "Ibiza", "country": "Spain",
            "reason_for_inclusion": "Strongest match for nightlife alongside beach access.",
            "expected_strengths": ["nightlife", "beaches"],
            "likely_weakness": "High prices and crowds in peak season.",
            "criteria_to_verify": ["activities", "cost"],
        },
        {
            "place_name": "Mykonos", "country": "Greece",
            "reason_for_inclusion": "Well-known beach and nightlife destination.",
            "expected_strengths": ["beaches", "nightlife"],
            "likely_weakness": "Among the most expensive Greek islands.",
            "criteria_to_verify": ["cost", "activities"],
        },
        {
            "place_name": "Chania", "country": "Greece",
            "reason_for_inclusion": "Crete's combination of beaches, hiking, and historical sites.",
            "expected_strengths": ["beaches", "hiking", "culture"],
            "likely_weakness": "Requires a connecting flight/ferry from most origins.",
            "criteria_to_verify": ["activities", "transportation"],
        },
        {
            "place_name": "Phuket", "country": "Thailand",
            "reason_for_inclusion": "Long-haul beach destination with extensive tourism infrastructure.",
            "expected_strengths": ["beaches", "activities"],
            "likely_weakness": "Long travel distance for most origins.",
            "criteria_to_verify": ["transportation", "activities"],
        },
        {
            "place_name": "Ubud", "country": "Indonesia",
            "reason_for_inclusion": "Cultural and hiking-oriented alternative to Bali's coastal towns.",
            "expected_strengths": ["hiking", "culture"],
            "likely_weakness": "Not directly on the coast.",
            "criteria_to_verify": ["activities", "culture"],
        },
        {
            "place_name": "Da Nang", "country": "Vietnam",
            "reason_for_inclusion": "Beach destination with a very low cost of living.",
            "expected_strengths": ["beaches", "low cost"],
            "likely_weakness": "Long travel distance for most origins.",
            "criteria_to_verify": ["cost", "transportation"],
        },
        {
            "place_name": "Queenstown", "country": "New Zealand",
            "reason_for_inclusion": "Strongest match for hiking and outdoor adventure activities.",
            "expected_strengths": ["hiking", "outdoor activities"],
            "likely_weakness": "Very long travel distance for most origins.",
            "criteria_to_verify": ["activities", "transportation"],
        },
        {
            "place_name": "Interlaken", "country": "Switzerland",
            "reason_for_inclusion": "Mountain hiking destination with excellent transit access.",
            "expected_strengths": ["hiking", "public transport"],
            "likely_weakness": "Among the most expensive hiking destinations in Europe.",
            "criteria_to_verify": ["activities", "cost"],
        },
        {
            "place_name": "Monterosso al Mare", "country": "Italy",
            "reason_for_inclusion": "Cinque Terre coastal hiking trails between villages.",
            "expected_strengths": ["hiking", "scenery"],
            "likely_weakness": "Limited accommodation capacity drives up prices.",
            "criteria_to_verify": ["activities", "cost"],
        },
        {
            "place_name": "Zakynthos", "country": "Greece",
            "reason_for_inclusion": "Beach-focused island destination with notable natural scenery.",
            "expected_strengths": ["beaches", "scenery"],
            "likely_weakness": "Limited activity variety outside beach-related options.",
            "criteria_to_verify": ["activities", "cost"],
        },
        {
            "place_name": "Valletta", "country": "Malta",
            "reason_for_inclusion": "Compact destination combining beaches, culture, and history.",
            "expected_strengths": ["culture", "beaches"],
            "likely_weakness": "Small size limits activity variety.",
            "criteria_to_verify": ["activities", "culture"],
        },
        {
            "place_name": "Malaga", "country": "Spain",
            "reason_for_inclusion": "Costa del Sol beach access with strong cultural attractions.",
            "expected_strengths": ["beaches", "culture"],
            "likely_weakness": "Very crowded in peak summer months.",
            "criteria_to_verify": ["activities", "cost"],
        },
        {
            "place_name": "Taormina", "country": "Italy",
            "reason_for_inclusion": "Sicilian coastal town combining beaches with historical sites.",
            "expected_strengths": ["beaches", "culture"],
            "likely_weakness": "Higher prices than mainland southern Italy.",
            "criteria_to_verify": ["cost", "culture"],
        },
        {
            "place_name": "Ponta Delgada", "country": "Portugal",
            "reason_for_inclusion": "Azores hiking and volcanic-landscape activities.",
            "expected_strengths": ["hiking", "scenery"],
            "likely_weakness": "Fewer direct international flight connections.",
            "criteria_to_verify": ["activities", "transportation"],
        },
        {
            "place_name": "Corfu", "country": "Greece",
            "reason_for_inclusion": "Greenery-backed beaches with a distinct cultural history.",
            "expected_strengths": ["beaches", "culture"],
            "likely_weakness": "Can be crowded near the main tourist areas.",
            "criteria_to_verify": ["activities", "culture"],
        },
        {
            "place_name": "Cagliari", "country": "Italy",
            "reason_for_inclusion": "Sardinian beaches with lower prices than mainland Italian coast.",
            "expected_strengths": ["beaches", "low cost"],
            "likely_weakness": "Fewer direct international flight connections.",
            "criteria_to_verify": ["cost", "transportation"],
        },
        {
            "place_name": "Bodrum", "country": "Turkey",
            "reason_for_inclusion": "Beach and nightlife destination at a lower cost than Greek islands.",
            "expected_strengths": ["beaches", "nightlife", "low cost"],
            "likely_weakness": "Very crowded in peak summer months.",
            "criteria_to_verify": ["activities", "cost"],
        },
        {
            "place_name": "Tenerife", "country": "Spain",
            "reason_for_inclusion": "Combines beach access with volcanic-landscape hiking.",
            "expected_strengths": ["beaches", "hiking"],
            "likely_weakness": "Southern resort zones can feel heavily touristic.",
            "criteria_to_verify": ["activities", "cost"],
        },
        {
            "place_name": "Paphos", "country": "Cyprus",
            "reason_for_inclusion": "Beach destination with strong historical/cultural sites.",
            "expected_strengths": ["beaches", "culture"],
            "likely_weakness": "Fewer direct international flight connections.",
            "criteria_to_verify": ["activities", "transportation"],
        },
        {
            "place_name": "Budva", "country": "Montenegro",
            "reason_for_inclusion": "Lower-cost Adriatic beach town with nearby mountain hiking.",
            "expected_strengths": ["beaches", "low cost"],
            "likely_weakness": "Smaller tourism infrastructure than larger resort towns.",
            "criteria_to_verify": ["cost", "activities"],
        },
        {
            "place_name": "Galle", "country": "Sri Lanka",
            "reason_for_inclusion": "Beach access combined with strong colonial-era cultural sites.",
            "expected_strengths": ["beaches", "culture"],
            "likely_weakness": "Very long travel distance for most origins.",
            "criteria_to_verify": ["activities", "transportation"],
        },
    ],
}
_SEED_CANDIDATES["mixed"] = _SEED_CANDIDATES["remote_work"][:15] + _SEED_CANDIDATES["vacation"][:15]
_SEED_CANDIDATES["unknown"] = _SEED_CANDIDATES["vacation"]


def generate_candidates(profile: dict) -> list[dict]:
    """Seed pool for the purpose, with any requested region brought to the front.

    This used to key off purpose alone and ignore preferred_regions outright,
    which is how P08 asked for Scandinavia and received Lisbon, Tbilisi, Chiang
    Mai and Bali -- D27. In-region seeds now lead, pulled from the other purpose
    pools when the purpose's own pool has none, and the rest still follow so
    bulk recall stays wide and the Stage-2 funnel keeps its choices.
    """
    purpose = profile.get("purpose", "unknown")
    pool = _SEED_CANDIDATES.get(purpose, _SEED_CANDIDATES["vacation"])
    regions = [r for r in (profile.get("preferred_regions") or []) if isinstance(r, str)]
    if not regions:
        return pool

    def in_region(candidate: dict) -> bool:
        country = candidate.get("country") or ""
        return any(region_contains(region, country) for region in regions)

    leading = [c for c in pool if in_region(c)]
    if not leading:
        # The purpose's own pool has nothing in the region; borrow from the others.
        taken = {c["place_name"] for c in pool}
        for other in _SEED_CANDIDATES.values():
            for candidate in other:
                if candidate["place_name"] not in taken and in_region(candidate):
                    taken.add(candidate["place_name"])
                    leading.append(candidate)
    if not leading:
        return pool

    leading_names = {c["place_name"] for c in leading}
    return leading + [c for c in pool if c["place_name"] not in leading_names]


_SEED_FIELDS = ("place_name", "country", "reason_for_inclusion")


def generate_candidate_seeds(profile: dict) -> list[dict]:
    """Lean-schema (Stage-1 bulk recall) variant of generate_candidates -- only
    the fields CandidatePlaceSeed accepts, since that model forbids extras.
    """
    return [{field: c[field] for field in _SEED_FIELDS} for c in generate_candidates(profile)]


# ---------------------------------------------------------------------------
# Dynamic Evaluation: deterministic stand-in for the batched scoring call
# ---------------------------------------------------------------------------

_COUNT_SATURATION = 20.0


def _counts_sum(counts: dict | None) -> float:
    if not isinstance(counts, dict):
        return 0.0
    return sum(v for v in counts.values() if isinstance(v, (int, float)))


# A failed count lookup must never surface as "count (0)" -- a genuine dict of
# zero counts is real evidence and still scores, but an absent one is not.
# Descriptive prose is also real evidence, just not countable: when the OSM half
# of a tool stalls the Wikivoyage half still arrives, and since the D8b fix
# deliberately degrades to context-only rather than losing the run, leaving that
# unscored discards the very evidence the degradation preserved. So prose scores
# below counts, the rationale always says the reading is descriptive, and a
# criterion with neither still returns None and renders as "not assessed".
_PROSE_BASE_SCORE = 0.4
_PROSE_INTEREST_BONUS = 0.15
_PROSE_MAX_SCORE = 0.9


def _score_from_prose(evidence: dict, subject: str) -> tuple[float, str] | None:
    text = evidence.get("wikivoyage_excerpt")
    if not isinstance(text, str) or not text.strip():
        return None
    matched = evidence.get("wikivoyage_matched_interests")
    matched = [str(m) for m in matched] if isinstance(matched, list) else []
    score = min(_PROSE_MAX_SCORE, _PROSE_BASE_SCORE + _PROSE_INTEREST_BONUS * len(matched))
    if matched:
        return score, (
            f"{subject} evidence is descriptive rather than counted; the text mentions "
            f"{', '.join(matched)}."
        )
    return score, f"{subject} evidence is descriptive rather than counted, so this score is approximate."


# "informs this score" was doing the work of a reason in every one of these, and
# for cost there was not even a number: "Cost evidence compared against the
# stated budget informs this score" was the *why it fits* for most candidates in
# a mock run, so a reader could not tell why rank 1 beat rank 4 (E1). Each
# rationale now states its actual reading. Real mode does not use any of this --
# the LLM writes its own -- but $0 mock runs are how most verification here gets
# done, and they were being read through a renderer that said nothing.


def _cost_rationale(evidence: dict) -> str:
    comparison = budget_comparison(evidence)
    if comparison is None:
        status = (evidence.get("budget_context") or {}).get("status")
        if status in (None, "not_provided"):
            return "No budget was stated, so cost is not counted against this place."
        return "Cost evidence was collected but could not be compared with the stated budget."

    monthly, remaining, currency = comparison
    unit = f" {currency}" if currency else ""
    budget = monthly + remaining
    if remaining >= 0:
        return (
            f"About {monthly:,.0f}{unit} a month against a {budget:,.0f}{unit} budget — "
            f"{remaining:,.0f}{unit} to spare."
        )
    over = -remaining
    multiple = monthly / budget if budget > 0 else 0.0
    scale = f", about {multiple:.1f}x the budget" if multiple >= 1.5 else ""
    return (
        f"About {monthly:,.0f}{unit} a month against a {budget:,.0f}{unit} budget — "
        f"over by {over:,.0f}{unit}{scale}."
    )


def _score_transportation(evidence: dict) -> tuple[float, str] | None:
    counts = evidence.get("counts_by_component")
    if not isinstance(counts, dict) or not counts:
        return _score_from_prose(evidence, "Local mobility")
    total = _counts_sum(counts)
    score = min(1.0, total / _COUNT_SATURATION)
    named = _largest_components(counts)
    return score, f"{total:g} mapped local-transport features nearby{named}."


def _score_accessibility(evidence: dict) -> tuple[float, str] | None:
    distance_km = evidence.get("straight_line_distance_km")
    if isinstance(distance_km, (int, float)):
        return max(0.0, min(1.0, 1.0 - distance_km / 3000.0)), (
            f"About {distance_km:g}km from the stated origin in a straight line."
        )
    counts = evidence.get("counts_by_component")
    if not isinstance(counts, dict) or not counts:
        return _score_from_prose(evidence, "Arrival-infrastructure")
    total = _counts_sum(counts)
    score = min(1.0, total / _COUNT_SATURATION)
    named = _largest_components(counts)
    return score, f"{total:g} mapped arrival points nearby{named}; no origin was given to measure from."


def _score_activities(evidence: dict, activity_preferences: list) -> tuple[float, str] | None:
    counts = evidence.get("counts_by_category")
    if not isinstance(counts, dict) or not counts:
        return _score_from_prose(evidence, "Activity")
    total = _counts_sum(counts)
    matched = [pref for pref in activity_preferences if counts.get(pref)]
    base = min(1.0, total / _COUNT_SATURATION)
    score = min(1.0, base + 0.1 * len(matched))
    if matched:
        detail = "covering " + ", ".join(f"{pref} ({counts[pref]:g})" for pref in matched)
    elif activity_preferences:
        detail = "none of them in the categories asked for"
    else:
        detail = "no particular activity was requested"
    return score, f"{total:g} mapped activity sites nearby, {detail}."


def _largest_components(counts: dict) -> str:
    """The two biggest contributors, so a bare total is traceable to something."""
    ranked = sorted(
        ((name, value) for name, value in counts.items() if isinstance(value, int | float) and value),
        key=lambda row: row[1],
        reverse=True,
    )
    if not ranked:
        return ""
    named = ", ".join(f"{name.replace('_', ' ')} {value:g}" for name, value in ranked[:2])
    return f" (mostly {named})"


def score_unresolved_mock(payload: dict) -> list[dict]:
    """Deterministic stand-in for the real Dynamic Evaluation scoring call.

    Reuses candidate_funnel.estimate_affordability for "cost" so mock and real
    scoring share the same budget-comparison reading, and simple count-based
    saturation heuristics for the other three criteria.
    """
    scores: list[dict] = []
    for candidate in payload.get("candidates", []):
        place = candidate.get("place", "")
        criteria = candidate.get("criteria", {})
        preferences = candidate.get("preferences", {})

        if "cost" in criteria:
            score = estimate_affordability(criteria["cost"])
            rationale = _cost_rationale(criteria["cost"])
            scores.append({"place": place, "criterion": "cost", "score": round(score, 4), "rationale": rationale})
        if "transportation" in criteria:
            scored = _score_transportation(criteria["transportation"])
            if scored is not None:
                score, rationale = scored
                scores.append(
                    {"place": place, "criterion": "transportation", "score": round(score, 4), "rationale": rationale}
                )
        if "accessibility" in criteria:
            scored = _score_accessibility(criteria["accessibility"])
            if scored is not None:
                score, rationale = scored
                scores.append(
                    {"place": place, "criterion": "accessibility", "score": round(score, 4), "rationale": rationale}
                )
        if "activities" in criteria:
            scored = _score_activities(criteria["activities"], preferences.get("activity_preferences", []))
            if scored is not None:
                score, rationale = scored
                scores.append(
                    {"place": place, "criterion": "activities", "score": round(score, 4), "rationale": rationale}
                )
    return scores


class MockLLMClient(BaseLLMClient):
    """Deterministic client used whenever MOCK_LLM=true (the default)."""

    async def complete(
        self,
        messages: list[dict],
        *,
        max_output_tokens: int,
        metadata: dict | None = None,
    ) -> LLMRawResponse:
        metadata = metadata or {}
        module = metadata.get("module")
        user_content = ""
        for msg in messages:
            if msg.get("role") == "user":
                user_content = msg.get("content", "")

        if module == REQUEST_INTERPRETER:
            profile_dict = interpret_prompt(user_content)
            text = json.dumps(profile_dict)
        elif module == AGENTIC_RESEARCH:
            try:
                payload = json.loads(user_content)
                profile = payload.get("profile", {})
            except json.JSONDecodeError:
                profile = {}
            candidates = generate_candidate_seeds(profile)
            text = json.dumps({"candidates": candidates})
        elif module == DYNAMIC_EVALUATION:
            try:
                payload = json.loads(user_content)
            except json.JSONDecodeError:
                payload = {}
            text = json.dumps({"scores": score_unresolved_mock(payload)})
        elif module == RECOMMENDATION_GENERATOR:
            try:
                payload = json.loads(user_content)
            except json.JSONDecodeError:
                payload = {}
            markdown = render_recommendation_markdown(payload)
            text = json.dumps({"markdown": markdown})
        else:
            text = json.dumps({"error": f"Unknown module for MockLLMClient: {module}"})

        approx_input_tokens = max(1, len(user_content) // 4)
        approx_output_tokens = max(1, len(text) // 4)
        return LLMRawResponse(
            text=text,
            input_tokens=approx_input_tokens,
            output_tokens=approx_output_tokens,
            provider_cost_usd=0.0,
            model="mock-llm",
        )
