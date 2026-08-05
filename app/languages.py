"""Static country -> spoken-language reference. No network, no dataset dependency.

Nothing measured language at all, so "English being widely spoken matters a lot"
was collected into hard_constraints and then reported as missing evidence for
every candidate. P06 ranked Lisbon, Barcelona and Seville above London, Dublin,
Bristol and Belfast on a criterion whose answer was free (D34).

Two things are recorded per country: the languages actually spoken day to day,
and how far English gets a visitor who speaks nothing else. The second is a
judgement, not a measurement, and is deliberately coarse -- three bands, sourced
from the EF EPI/Eurobarometer picture rather than a per-country statistic we
cannot cite. It is used to score a *stated* language requirement, never to rank
places that never asked about language.

Country names match what the geocoder returns (Nominatim's English `country`
field), lowercased. An unlisted country resolves to None and the criterion is
reported unmeasured, which is the honest answer -- exactly the behaviour every
country had before this file existed.
"""

from __future__ import annotations

from typing import Literal

EnglishReach = Literal["native", "widespread", "limited"]

# Countries where English is an official or de-facto first language.
_ENGLISH_NATIVE = (
    "united kingdom", "ireland", "united states", "canada", "australia",
    "new zealand", "malta", "singapore", "jamaica", "barbados", "bahamas",
    "trinidad and tobago", "belize", "guyana",
)

# Countries where a visitor gets by in English in cities without friction.
_ENGLISH_WIDESPREAD = (
    "netherlands", "denmark", "norway", "sweden", "finland", "iceland",
    "austria", "germany", "belgium", "luxembourg", "switzerland",
    "estonia", "latvia", "lithuania", "slovenia", "croatia", "portugal",
    "poland", "czechia", "cyprus", "greece", "israel", "philippines",
    "kenya", "south africa", "india", "nigeria", "ghana", "uganda",
    "united arab emirates", "qatar", "malaysia", "hong kong",
)

_LANGUAGES: dict[str, tuple[str, ...]] = {
    "albania": ("Albanian",),
    "argentina": ("Spanish",),
    "australia": ("English",),
    "austria": ("German",),
    "bahamas": ("English",),
    "barbados": ("English",),
    "belgium": ("Dutch", "French", "German"),
    "belize": ("English", "Spanish"),
    "bosnia and herzegovina": ("Bosnian", "Croatian", "Serbian"),
    "brazil": ("Portuguese",),
    "bulgaria": ("Bulgarian",),
    "canada": ("English", "French"),
    "chile": ("Spanish",),
    "china": ("Mandarin Chinese",),
    "colombia": ("Spanish",),
    "costa rica": ("Spanish",),
    "croatia": ("Croatian",),
    "cyprus": ("Greek", "Turkish"),
    "czechia": ("Czech",),
    "denmark": ("Danish",),
    "dominican republic": ("Spanish",),
    "ecuador": ("Spanish",),
    "egypt": ("Arabic",),
    "estonia": ("Estonian",),
    "finland": ("Finnish", "Swedish"),
    "france": ("French",),
    "georgia": ("Georgian",),
    "germany": ("German",),
    "ghana": ("English",),
    "greece": ("Greek",),
    "guyana": ("English",),
    "hong kong": ("Cantonese", "English"),
    "hungary": ("Hungarian",),
    "iceland": ("Icelandic",),
    "india": ("Hindi", "English"),
    "indonesia": ("Indonesian",),
    "ireland": ("English", "Irish"),
    "israel": ("Hebrew", "Arabic"),
    "italy": ("Italian",),
    "jamaica": ("English",),
    "japan": ("Japanese",),
    "jordan": ("Arabic",),
    "kenya": ("Swahili", "English"),
    "latvia": ("Latvian",),
    "lithuania": ("Lithuanian",),
    "luxembourg": ("Luxembourgish", "French", "German"),
    "malaysia": ("Malay", "English"),
    "malta": ("Maltese", "English"),
    "mexico": ("Spanish",),
    "montenegro": ("Montenegrin",),
    "morocco": ("Arabic", "French"),
    "netherlands": ("Dutch",),
    "new zealand": ("English", "Maori"),
    "nigeria": ("English",),
    "north macedonia": ("Macedonian",),
    "norway": ("Norwegian",),
    "panama": ("Spanish",),
    "peru": ("Spanish",),
    "philippines": ("Filipino", "English"),
    "poland": ("Polish",),
    "portugal": ("Portuguese",),
    "qatar": ("Arabic",),
    "romania": ("Romanian",),
    "serbia": ("Serbian",),
    "singapore": ("English", "Mandarin Chinese", "Malay", "Tamil"),
    "slovakia": ("Slovak",),
    "slovenia": ("Slovene",),
    "south africa": ("English", "Afrikaans", "Zulu"),
    "south korea": ("Korean",),
    "spain": ("Spanish",),
    "sweden": ("Swedish",),
    "switzerland": ("German", "French", "Italian"),
    "taiwan": ("Mandarin Chinese",),
    "thailand": ("Thai",),
    "trinidad and tobago": ("English",),
    "turkey": ("Turkish",),
    "uganda": ("English", "Swahili"),
    "united arab emirates": ("Arabic", "English"),
    "united kingdom": ("English",),
    "united states": ("English",),
    "uruguay": ("Spanish",),
    "vietnam": ("Vietnamese",),
}


def _normalize(country: str) -> str:
    return " ".join(country.casefold().split())


def spoken_languages(country: str) -> tuple[str, ...] | None:
    """Languages used day to day, or None when the country is not in the table."""
    return _LANGUAGES.get(_normalize(country))


def english_reach(country: str) -> EnglishReach | None:
    """How far English alone gets a visitor, or None when unknown.

    Coarse on purpose. "widespread" means a visitor manages in cities without
    the local language; it is not a claim about the whole country or about
    dealing with officialdom.
    """
    normalized = _normalize(country)
    if normalized in _ENGLISH_NATIVE:
        return "native"
    if normalized in _ENGLISH_WIDESPREAD:
        return "widespread"
    if normalized in _LANGUAGES:
        return "limited"
    return None
