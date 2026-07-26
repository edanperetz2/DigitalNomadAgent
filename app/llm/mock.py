"""MockLLMClient: deterministic, zero-cost stand-in for the real provider.

Used by default (MOCK_LLM=true) so `pytest` and local development never make a
paid call. Behavior is keyed off `metadata["module"]` (set by TracedLLMClient)
plus the actual message content, so it is a real (if simplified) deterministic
interpreter/generator rather than a random or hardcoded-only stub.
"""

from __future__ import annotations

import json
import re

from app.core.module_names import AGENTIC_RESEARCH, RECOMMENDATION_GENERATOR, REQUEST_INTERPRETER
from app.core.rendering import render_recommendation_markdown
from app.llm.base import BaseLLMClient, LLMRawResponse

# ---------------------------------------------------------------------------
# Request Interpreter: deterministic keyword/regex-based parser
# ---------------------------------------------------------------------------

_STUDY_KEYWORDS = [
    "study", "student", "exchange", "semester", "degree", "university",
    "master's", "masters", "phd", "academic", "college",
]
_REMOTE_KEYWORDS = [
    "remote work", "work remotely", "remote job", "digital nomad",
    "working remotely", "overlap with", "working hours", "remote-friendly",
]
_VACATION_KEYWORDS = [
    "vacation", "holiday", "trip", "sightseeing", "beach",
    "two weeks", "getaway",
]

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
    if any(kw in text for kw in _REMOTE_KEYWORDS):
        found.add("remote_work")
    if any(kw in text for kw in _STUDY_KEYWORDS):
        found.add("study")
    if any(kw in text for kw in _VACATION_KEYWORDS):
        found.add("vacation")
    return found


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

    if re.search(r"per month|/month|monthly", text, re.IGNORECASE):
        period = "monthly"
        confidence = "high"
    elif re.search(r"per week|/week|weekly", text, re.IGNORECASE):
        period = "weekly"
        confidence = "high"
    elif re.search(r"per day|/day|daily", text, re.IGNORECASE):
        period = "daily"
        confidence = "high"
    elif re.search(r"\btotal\b", text, re.IGNORECASE):
        period = "total"
        confidence = "high"
    else:
        period = "unknown"
        confidence = "low"

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


def interpret_prompt(prompt: str) -> dict:
    """Deterministic, rule-based interpretation used by MockLLMClient."""
    text = prompt.strip()
    lowered = text.lower()

    purposes_found = _detect_purposes(lowered)
    if not purposes_found:
        return {
            "purpose": "unknown",
            "clarification_required": True,
            "clarification_question": (
                "Could you clarify the main purpose of this trip (remote work, study, "
                "vacation, or something else), your approximate budget, and how long "
                "you plan to stay?"
            ),
            "missing_information": ["purpose"],
        }

    if len(purposes_found) == 1:
        purpose = next(iter(purposes_found))
        secondary_purposes: list[str] = []
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
    climate_preferences = [w for w in _CLIMATE_WORDS if w in lowered]
    activity_preferences = _extract_activity_preferences(lowered)
    amenity_preferences = _extract_amenity_preferences(lowered)

    mobility_requirements = []
    if re.search(r"without a car|car-free|no car|car free", lowered):
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
    deal_breakers = []
    for m in re.finditer(r"\b(avoid|never)\b[^.]{0,60}", lowered):
        deal_breakers.append(m.group(0).strip())
    soft_preferences = []
    for m in re.finditer(r"\bprefer\b[^.]{0,60}", lowered):
        soft_preferences.append(m.group(0).strip())

    relevant_criteria: list[str] = []
    inferred_weights: dict[str, float] = {}
    for kw, criterion in _CRITERIA_KEYWORD_MAP.items():
        if kw in lowered and criterion not in relevant_criteria:
            relevant_criteria.append(criterion)
            if "most important" in lowered:
                inferred_weights[criterion] = 0.9
            elif "prefer" in lowered:
                inferred_weights[criterion] = 0.6
            elif "would be nice" in lowered:
                inferred_weights[criterion] = 0.3
            else:
                inferred_weights[criterion] = 0.5
    if re.search(r"do not care about (\w+)|don't care about (\w+)", lowered):
        for m in re.finditer(r"do not care about (\w+)|don't care about (\w+)", lowered):
            dropped = (m.group(1) or m.group(2))
            inferred_weights.pop(dropped, None)
            if dropped in relevant_criteria:
                relevant_criteria.remove(dropped)

    missing_information: list[str] = []
    clarification_required = False
    clarification_question = None
    if "visa" in lowered and origin is None:
        missing_information.append("nationality (for visa considerations)")

    return {
        "purpose": purpose,
        "secondary_purposes": secondary_purposes,
        "duration": duration,
        "dates_or_season": None,
        "origin": origin,
        "nationality": None,
        "preferred_regions": [],
        "excluded_regions": [],
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
    ],
}
_SEED_CANDIDATES["mixed"] = _SEED_CANDIDATES["remote_work"][:2] + _SEED_CANDIDATES["vacation"][:3]
_SEED_CANDIDATES["unknown"] = _SEED_CANDIDATES["vacation"]


def generate_candidates(profile: dict) -> list[dict]:
    purpose = profile.get("purpose", "unknown")
    return _SEED_CANDIDATES.get(purpose, _SEED_CANDIDATES["vacation"])


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
            candidates = generate_candidates(profile)
            text = json.dumps({"candidates": candidates})
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
