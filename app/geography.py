"""Static region -> member-country taxonomy. No network, no dataset dependency.

The codebase matched a stated region against a candidate's country name or ISO
code and nothing else, so "Europe" or "Scandinavia" matched no country at all.
Two defects came out of that single gap:

  * D16 -- a continental preference eliminated every candidate, so the request
    failed outright. Fixed by relaxing the preference instead.
  * D27 -- relaxing means the region stops being applied, and P08 ("somewhere in
    Scandinavia") was answered with Lisbon, Tbilisi, Chiang Mai and Bali. Not
    one of its 30 candidates was Scandinavian.

Resolving the common region words is enough to close both: a resolvable region
can filter properly, and a candidate set with nothing in the requested region
becomes a *detectable* fact rather than a silent substitution.

Deliberately small and hand-written. It covers the continents and the
sub-regions people actually type into a travel request; it is not a geography
database and does not try to be. An unrecognized region resolves to None and
every caller falls back to the previous name/ISO-code behaviour, so adding a
region here can only ever make matching better, never worse.

Country names are matched against what the geocoder returns (Nominatim's
English `country` field), lowercased.
"""

from __future__ import annotations

_NORDICS = ("denmark", "finland", "iceland", "norway", "sweden")
_SCANDINAVIA = ("denmark", "norway", "sweden")
_BALTICS = ("estonia", "latvia", "lithuania")
_BENELUX = ("belgium", "netherlands", "luxembourg")
_IBERIA = ("spain", "portugal", "andorra")
_BALKANS = (
    "albania", "bosnia and herzegovina", "bulgaria", "croatia", "greece", "kosovo",
    "montenegro", "north macedonia", "romania", "serbia", "slovenia",
)
_WESTERN_EUROPE = (
    "austria", "belgium", "france", "germany", "ireland", "liechtenstein", "luxembourg",
    "monaco", "netherlands", "switzerland", "united kingdom",
)
_CENTRAL_EUROPE = ("austria", "czechia", "germany", "hungary", "poland", "slovakia", "slovenia", "switzerland")
_EASTERN_EUROPE = (
    "belarus", "bulgaria", "czechia", "hungary", "moldova", "poland", "romania",
    "russia", "slovakia", "ukraine",
)
_SOUTHERN_EUROPE = (
    "albania", "bosnia and herzegovina", "croatia", "cyprus", "greece", "italy", "malta",
    "montenegro", "north macedonia", "portugal", "san marino", "serbia", "slovenia", "spain",
)
# Crown dependencies, overseas territories and island autonomies. Nominatim
# returns these as the `country` for places inside them, so a continent set
# built only from sovereign states silently eliminates them: "somewhere in
# Europe" would have dropped a Gibraltar candidate. Found by comparing the
# table against the 85 distinct country names 357 cached geocoding results
# actually returned, rather than by reasoning about which ones matter.
_EUROPEAN_TERRITORIES = (
    "gibraltar", "isle of man", "jersey", "guernsey", "faroe islands",
    "aland islands", "åland islands", "svalbard and jan mayen",
)
_EUROPE = tuple(
    sorted(
        set(
            _NORDICS + _BALTICS + _WESTERN_EUROPE + _CENTRAL_EUROPE + _EASTERN_EUROPE
            # _BALKANS was omitted from this union, which is how Kosovo -- present
            # in the table since it was written -- was still outside Europe.
            + _SOUTHERN_EUROPE + _BALKANS + _EUROPEAN_TERRITORIES
        )
        # Transcontinental: included because a traveller asking for "Europe"
        # would not be surprised to be offered Tbilisi, Yerevan, Istanbul or Baku.
        | {"iceland", "andorra", "vatican city", "georgia", "armenia", "turkey", "azerbaijan"}
    )
)
_SOUTHEAST_ASIA = (
    "brunei", "cambodia", "indonesia", "laos", "malaysia", "myanmar", "philippines",
    "singapore", "thailand", "timor-leste", "vietnam",
)
_EAST_ASIA = ("china", "hong kong", "japan", "macau", "mongolia", "south korea", "taiwan")
_SOUTH_ASIA = ("bangladesh", "bhutan", "india", "maldives", "nepal", "pakistan", "sri lanka")
_CENTRAL_ASIA = ("kazakhstan", "kyrgyzstan", "tajikistan", "turkmenistan", "uzbekistan")
_MIDDLE_EAST = (
    "bahrain", "cyprus", "egypt", "iran", "iraq", "israel", "jordan", "kuwait", "lebanon",
    "oman", "palestine", "qatar", "saudi arabia", "syria", "turkey",
    "united arab emirates", "yemen",
)
_ASIA = tuple(sorted(set(_SOUTHEAST_ASIA + _EAST_ASIA + _SOUTH_ASIA + _CENTRAL_ASIA + _MIDDLE_EAST)))
_CENTRAL_AMERICA = (
    "belize", "costa rica", "el salvador", "guatemala", "honduras", "nicaragua", "panama",
)
_CARIBBEAN = (
    "antigua and barbuda", "aruba", "bahamas", "barbados", "cuba", "curaçao", "curacao",
    "dominica", "dominican republic", "grenada", "guadeloupe", "haiti", "jamaica",
    "martinique", "puerto rico", "saint lucia", "sint maarten", "trinidad and tobago",
    "us virgin islands", "british virgin islands",
)
_NORTH_AMERICA = tuple(sorted({"canada", "mexico", "united states"} | set(_CENTRAL_AMERICA) | set(_CARIBBEAN)))
_SOUTH_AMERICA = (
    "argentina", "bolivia", "brazil", "chile", "colombia", "ecuador", "guyana", "paraguay",
    "peru", "suriname", "uruguay", "venezuela",
)
_LATIN_AMERICA = tuple(sorted(set(_SOUTH_AMERICA + _CENTRAL_AMERICA) | {"cuba", "dominican republic", "mexico"}))
_NORTH_AFRICA = ("algeria", "egypt", "libya", "morocco", "sudan", "tunisia")
_EAST_AFRICA = ("ethiopia", "kenya", "rwanda", "tanzania", "uganda")
_WEST_AFRICA = ("ghana", "ivory coast", "nigeria", "senegal")
_SOUTHERN_AFRICA = ("botswana", "namibia", "south africa", "zambia", "zimbabwe")
_AFRICA = tuple(sorted(set(_NORTH_AFRICA + _EAST_AFRICA + _WEST_AFRICA + _SOUTHERN_AFRICA)))
_OCEANIA = ("australia", "fiji", "new zealand", "papua new guinea", "samoa", "vanuatu")

# Ordered longest-key-first at lookup time, so "southeast asia" wins over "asia".
REGION_COUNTRIES: dict[str, tuple[str, ...]] = {
    "scandinavia": _SCANDINAVIA,
    "scandinavian": _SCANDINAVIA,
    "nordics": _NORDICS,
    "nordic countries": _NORDICS,
    "nordic": _NORDICS,
    "baltics": _BALTICS,
    "baltic states": _BALTICS,
    "benelux": _BENELUX,
    "iberia": _IBERIA,
    "iberian peninsula": _IBERIA,
    "balkans": _BALKANS,
    "the balkans": _BALKANS,
    "western europe": _WESTERN_EUROPE,
    "central europe": _CENTRAL_EUROPE,
    "eastern europe": _EASTERN_EUROPE,
    "southern europe": _SOUTHERN_EUROPE,
    "mediterranean": _SOUTHERN_EUROPE,
    "europe": _EUROPE,
    "european union": _EUROPE,
    "eu": _EUROPE,
    "southeast asia": _SOUTHEAST_ASIA,
    "south east asia": _SOUTHEAST_ASIA,
    "south-east asia": _SOUTHEAST_ASIA,
    "east asia": _EAST_ASIA,
    "south asia": _SOUTH_ASIA,
    "central asia": _CENTRAL_ASIA,
    "middle east": _MIDDLE_EAST,
    "asia": _ASIA,
    "central america": _CENTRAL_AMERICA,
    "caribbean": _CARIBBEAN,
    "north america": _NORTH_AMERICA,
    "latin america": _LATIN_AMERICA,
    "south america": _SOUTH_AMERICA,
    "north africa": _NORTH_AFRICA,
    "east africa": _EAST_AFRICA,
    "west africa": _WEST_AFRICA,
    "southern africa": _SOUTHERN_AFRICA,
    "africa": _AFRICA,
    "oceania": _OCEANIA,
    "australasia": _OCEANIA,
}

# Longest first, so "southeast asia" wins over "asia". Short keys are excluded
# from the phrase pass entirely: "eu" is a fine exact spelling of Europe, but as
# a substring it appears in "Seoul" and "Deutschland", which would resolve a
# place name to a continent and then filter the place itself out.
_PHRASE_LOOKUP_ORDER = tuple(
    sorted((key for key in REGION_COUNTRIES if len(key) > 3), key=len, reverse=True)
)

# Every country any region here contains. `resolve_region` returns None for a
# bare country name as much as for a phrase that names no place at all, and
# callers sometimes need to tell those two apart: a country is something the
# geocoded check can filter on, "party destinations" is not.
KNOWN_COUNTRIES: frozenset[str] = frozenset(
    country for countries in REGION_COUNTRIES.values() for country in countries
)


def resolve_region(region: str) -> frozenset[str] | None:
    """Member countries of a named region, or None if the name is not one.

    None means "not a region this table knows", which callers must treat as
    "fall back to matching the country name directly" -- never as "matches
    nothing". A country name passed in here is not a region and returns None.
    """
    normalized = " ".join(region.replace("-", " ").casefold().split())
    if not normalized:
        return None
    if normalized in REGION_COUNTRIES:
        return frozenset(REGION_COUNTRIES[normalized])
    # "somewhere in Scandinavia", "the Nordics" -- the interpreter is supposed to
    # emit a bare region, but it is an LLM and sometimes emits the phrase. Whole
    # words only, so "Seoul" is not read as Europe.
    words = normalized.split()
    for key in _PHRASE_LOOKUP_ORDER:
        key_words = key.split()
        if any(words[i : i + len(key_words)] == key_words for i in range(len(words))):
            return frozenset(REGION_COUNTRIES[key])
    return None


def region_contains(region: str, country: str) -> bool | None:
    """Whether `country` is in `region`. None when the region is unresolvable."""
    members = resolve_region(region)
    if members is None:
        return None
    return " ".join(country.casefold().split()) in members
