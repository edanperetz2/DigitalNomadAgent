"""Tests for the region -> member-country taxonomy (D16/D27)."""

from app.agent.dynamic_evaluation import check_geocoded_constraints
from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.geography import region_contains, resolve_region


def _candidate(name: str, country: str, code: str = "") -> CandidatePlace:
    return CandidatePlace(
        place_name=name, country=country, country_code=code, reason_for_inclusion="t", verified=True
    )


def test_the_regions_people_actually_type_resolve():
    assert "norway" in resolve_region("Scandinavia")
    assert "thailand" in resolve_region("Southeast Asia")
    assert "thailand" in resolve_region("south-east asia")
    assert "spain" in resolve_region("Europe")
    assert "estonia" in resolve_region("the Baltics")
    assert "japan" in resolve_region("East Asia")


def test_a_more_specific_region_wins_over_a_broader_one():
    """"Southeast Asia" contains "asia"; it must not resolve to all of Asia."""
    southeast = resolve_region("Southeast Asia")
    assert "japan" not in southeast
    assert "vietnam" in southeast


def test_an_unknown_region_resolves_to_none_not_to_nothing():
    """None means "fall back to matching the country name", never "matches nothing"."""
    assert resolve_region("mid-sized city") is None
    assert resolve_region("") is None
    assert resolve_region("Spain") is None  # a country is not a region
    assert region_contains("mid-sized city", "Spain") is None


def test_scandinavia_excludes_the_places_p08_was_actually_offered():
    for country in ("Portugal", "Georgia", "Thailand", "Indonesia", "Mexico"):
        assert region_contains("Scandinavia", country) is False
    for country in ("Norway", "Sweden", "Denmark"):
        assert region_contains("Scandinavia", country) is True


def test_a_continent_now_filters_candidates():
    """The D16 workaround existed because "Europe" matched no country at all."""
    profile = PlaceRequestProfile(purpose="vacation", preferred_regions=["Europe"])

    eliminated, _ = check_geocoded_constraints(profile, _candidate("Porto", "Portugal", "PT"))
    assert eliminated is False

    eliminated, reason = check_geocoded_constraints(profile, _candidate("Bali", "Indonesia", "ID"))
    assert eliminated is True
    assert "outside the preferred regions" in reason


def test_a_country_named_as_the_region_still_works():
    """The fallback path -- "Spain" is not in the table and must match by name."""
    profile = PlaceRequestProfile(purpose="vacation", preferred_regions=["Spain"])

    assert check_geocoded_constraints(profile, _candidate("Valencia", "Spain", "ES"))[0] is False
    assert check_geocoded_constraints(profile, _candidate("Porto", "Portugal", "PT"))[0] is True


def test_several_preferred_regions_are_an_or():
    profile = PlaceRequestProfile(purpose="vacation", preferred_regions=["Scandinavia", "Iberia"])

    assert check_geocoded_constraints(profile, _candidate("Oslo", "Norway", "NO"))[0] is False
    assert check_geocoded_constraints(profile, _candidate("Porto", "Portugal", "PT"))[0] is False
    assert check_geocoded_constraints(profile, _candidate("Bali", "Indonesia", "ID"))[0] is True


def test_an_excluded_region_eliminates_its_whole_membership():
    profile = PlaceRequestProfile(purpose="vacation", excluded_regions=["Southeast Asia"])

    eliminated, reason = check_geocoded_constraints(profile, _candidate("Bali", "Indonesia", "ID"))
    assert eliminated is True
    assert "excluded region" in reason
    assert check_geocoded_constraints(profile, _candidate("Porto", "Portugal", "PT"))[0] is False


def test_a_candidate_without_a_country_is_never_eliminated():
    """Missing evidence must not produce a positive constraint result."""
    profile = PlaceRequestProfile(purpose="vacation", preferred_regions=["Scandinavia"])
    assert check_geocoded_constraints(profile, _candidate("Nowhere", ""))[0] is False


def test_the_mock_generator_leads_with_the_requested_region():
    """P08 asked for Scandinavia and got Lisbon, Tbilisi, Chiang Mai and Bali."""
    from app.llm.mock import generate_candidates

    without = generate_candidates({"purpose": "vacation"})
    assert not any(
        region_contains("Scandinavia", c["country"]) for c in without[:3]
    ), "fixture assumption: the vacation pool does not already lead with Nordics"

    with_region = generate_candidates(
        {"purpose": "vacation", "preferred_regions": ["Scandinavia"]}
    )
    assert region_contains("Scandinavia", with_region[0]["country"]) is True
    # Recall stays wide -- the funnel still gets the rest of the pool to choose from.
    assert len(with_region) >= len(without)


def test_the_mock_generator_falls_back_when_no_seed_is_in_the_region():
    """An unresolvable or unstocked region must not empty the candidate set."""
    from app.llm.mock import _SEED_CANDIDATES, generate_candidates

    pool = generate_candidates({"purpose": "vacation"})

    # Unresolvable: not a region at all, so there is nothing to lead with.
    assert generate_candidates({"purpose": "vacation", "preferred_regions": ["mid-sized city"]}) == pool

    # Resolvable but unstocked anywhere in the seed data.
    every_seed = [c for seeds in _SEED_CANDIDATES.values() for c in seeds]
    unstocked = next(
        region
        for region in ("Central Asia", "West Africa", "Caribbean")
        if not any(region_contains(region, c["country"]) for c in every_seed)
    )
    assert generate_candidates({"purpose": "vacation", "preferred_regions": [unstocked]}) == pool


def test_the_mock_generator_never_loses_a_candidate_to_region_ordering():
    """Leading with the region reorders recall; it must not shrink it."""
    from app.llm.mock import generate_candidates

    pool = generate_candidates({"purpose": "vacation"})
    with_region = generate_candidates(
        {"purpose": "vacation", "preferred_regions": ["Scandinavia"]}
    )
    names = {c["place_name"] for c in with_region}
    assert {c["place_name"] for c in pool} <= names
    assert len(names) == len(with_region), "no duplicates introduced by the top-up"


def test_a_place_name_is_not_read_as_a_region_by_substring():
    """"Seoul" contains "eu"; resolving it to Europe would filter Seoul out.

    The interpreter is told to keep cities out of preferred_regions and has
    been observed putting them there anyway ("mid-sized city"), so this path
    has to be safe.
    """
    assert resolve_region("Seoul") is None
    assert resolve_region("Deutschland") is None
    assert resolve_region("Beulah") is None
    # The exact spelling is still a region.
    assert resolve_region("EU") == resolve_region("Europe")


def test_a_region_named_inside_a_phrase_still_resolves():
    assert resolve_region("somewhere in Scandinavia") == resolve_region("Scandinavia")
    assert resolve_region("the Nordics") == resolve_region("Nordics")
    assert resolve_region("anywhere in Southeast Asia") == resolve_region("Southeast Asia")
