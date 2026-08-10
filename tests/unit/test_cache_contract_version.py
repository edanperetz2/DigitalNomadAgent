"""D59: a cached tool result must not outlive the code that produced it.

The cache key already carries CACHE_CONTRACT_VERSION, so bumping it retires every
row at once. Nothing made anyone bump it. D44 replaced the Overpass source URL --
the API's *documentation page* had been cited as the source of its counts -- and
the version stayed at 2, so 73 rows kept serving the old citation for the rest of
their 14-day TTL. A validation run on 2026-08-07 duly reported the documentation
page and it read like a regression in a fix that was correct.

This pins the identities that reach the reader. Change one and this test fails,
naming the constant to bump. It is a tripwire, not a guarantee: it covers source
names and URLs, not the shape of `normalized_data`. Bump the version for that
too.
"""

from app.evidence.cache import CACHE_CONTRACT_VERSION, _cache_key
from app.tools import (
    activities,
    amenities,
    language,
    local_mobility,
    terrain,
    transport_access,
    weather,
)

BUMP = (
    "A cached tool result's source identity changed. Bump CACHE_CONTRACT_VERSION "
    "in app/evidence/cache.py so existing rows are retired, then update this "
    "snapshot. See D59."
)

# What each cache-backed tool claims as its source, as of CACHE_CONTRACT_VERSION 3.
PINNED_SOURCES = {
    "activities.OVERPASS_SOURCE_URL": "https://www.openstreetmap.org/",
    "amenities.OVERPASS_SOURCE_URL": "https://www.openstreetmap.org/",
    "local_mobility.OVERPASS_SOURCE_URL": "https://www.openstreetmap.org/",
    "local_mobility.OVERPASS_SOURCE_NAME": "OpenStreetMap local mobility infrastructure",
    "transport_access.OVERPASS_SOURCE_URL": "https://www.openstreetmap.org/",
    "language.SOURCE_NAME": "DigitalNomadAgent country language reference",
    "language.SOURCE_URL": "https://github.com/edanperetz2/DigitalNomadAgent/blob/main/app/languages.py",
    "terrain.SOURCE_NAME": "Open-Meteo elevation API",
    "terrain.SOURCE_URL": "https://open-meteo.com/en/docs/elevation-api",
    "weather.SOURCE_URL": "https://open-meteo.com/en/docs/historical-weather-api",
}

MODULES = {
    "activities": activities,
    "amenities": amenities,
    "language": language,
    "local_mobility": local_mobility,
    "terrain": terrain,
    "transport_access": transport_access,
    "weather": weather,
}


def test_source_identities_match_the_pinned_cache_contract():
    actual = {
        name: getattr(MODULES[name.split(".")[0]], name.split(".")[1])
        for name in PINNED_SOURCES
    }
    assert actual == PINNED_SOURCES, BUMP


def test_no_tool_still_cites_the_overpass_documentation_page():
    """The specific mistake D44 fixed: citing the API's docs as the data source."""
    for name, url in PINNED_SOURCES.items():
        assert "wiki.openstreetmap.org" not in url, (
            f"{name} cites the Overpass documentation page rather than the data. {BUMP}"
        )


def test_the_version_is_part_of_the_key():
    """Without this the constant would be decorative and D59 could not be fixed by bumping it."""
    same = _cache_key("AmenitiesTool", "Lisbon", {"radius": 3000})
    assert _cache_key("AmenitiesTool", "Lisbon", {"radius": 3000}) == same

    import app.evidence.cache as cache_module

    original = cache_module.CACHE_CONTRACT_VERSION
    try:
        cache_module.CACHE_CONTRACT_VERSION = original + 1
        assert _cache_key("AmenitiesTool", "Lisbon", {"radius": 3000}) != same
    finally:
        cache_module.CACHE_CONTRACT_VERSION = original


def test_the_version_was_bumped_past_the_d44_gap():
    """Rows written before D44 were keyed at version 2 and must never be served again."""
    assert CACHE_CONTRACT_VERSION > 2
