"""PlaceContextTool had no dedicated test file at all before the final
pre-deadline audit -- and a real bug: the cache key was built from the
geocoding-verified canonical name, but the live Wikivoyage fetch (and the
source URL) used the raw, unverified place_name instead, letting the cache
key and the live query diverge whenever geocoding normalizes a name (e.g.
"NYC" -> "New York City")."""

import pytest

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.cache import ToolCache
from app.tools.place_context import PlaceContextTool


class _FakeMediaWikiClient:
    def __init__(self, extract: str = "A concise destination summary."):
        self.extract = extract
        self.requested_titles: list[str] = []

    async def request(self, **kwargs):
        self.requested_titles.append(kwargs["titles"])
        # formatversion=1 shape (what the real _fetch call requests): pages is a
        # dict keyed by page id, not a list.
        return {"query": {"pages": {"12345": {"extract": self.extract}}}}


@pytest.mark.asyncio
async def test_fetch_and_cache_both_use_the_canonical_name_not_the_raw_name(tmp_path):
    from app.evidence.database import Database

    db = Database(tmp_path / "cache_test.db")
    await db.connect()
    cache = ToolCache(db, default_ttl_hours=1)
    fake_client = _FakeMediaWikiClient()
    tool = PlaceContextTool(cache, mediawiki=fake_client)

    candidate = CandidatePlace(
        place_name="NYC", canonical_name="New York City", country="United States",
        reason_for_inclusion="test",
    )
    result = await tool.run(candidate, PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    # The live fetch must have queried the canonical name, not the raw one.
    assert fake_client.requested_titles == ["New York City"]
    # The source URL must point at the same canonical-name page that was fetched.
    assert "New_York_City" in result.source_url
    assert "NYC" not in result.source_url

    await db.close()


@pytest.mark.asyncio
async def test_a_second_call_hits_the_cache_keyed_on_the_same_canonical_name(tmp_path):
    from app.evidence.database import Database

    db = Database(tmp_path / "cache_test2.db")
    await db.connect()
    cache = ToolCache(db, default_ttl_hours=1)
    fake_client = _FakeMediaWikiClient()
    tool = PlaceContextTool(cache, mediawiki=fake_client)

    candidate = CandidatePlace(
        place_name="NYC", canonical_name="New York City", country="United States",
        reason_for_inclusion="test",
    )
    profile = PlaceRequestProfile(purpose="vacation")
    await tool.run(candidate, profile)
    await tool.run(candidate, profile)

    # Second call served from cache -- exactly one live fetch happened.
    assert len(fake_client.requested_titles) == 1

    await db.close()


@pytest.mark.asyncio
async def test_falls_back_to_place_name_when_no_canonical_name_is_available(tmp_path):
    from app.evidence.database import Database

    db = Database(tmp_path / "cache_test3.db")
    await db.connect()
    cache = ToolCache(db, default_ttl_hours=1)
    fake_client = _FakeMediaWikiClient()
    tool = PlaceContextTool(cache, mediawiki=fake_client)

    candidate = CandidatePlace(place_name="Porto", country="Portugal", reason_for_inclusion="test")
    result = await tool.run(candidate, PlaceRequestProfile(purpose="vacation"))

    assert result.error is None
    assert fake_client.requested_titles == ["Porto"]

    await db.close()
