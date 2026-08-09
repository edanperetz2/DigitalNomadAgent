"""The shipped examples stand in for a history, and only where there is none.

Vercel keeps the database in /tmp, wiped on every cold start, so a deployment
greets its first visitor with an empty sidebar unless something puts these back.
Seeding is therefore per-boot -- which is exactly why it has to be inert once a
visitor has conversations of their own.
"""

from __future__ import annotations

import pytest

from app.evidence.database import Database
from app.evidence.example_sessions import load_example_sessions
from app.evidence.saved_searches import SavedSearchStore

EXAMPLE_RESULT = {"status": "ok", "error": None, "response": "## Best matches\n", "steps": []}


@pytest.fixture
async def store(tmp_path):
    db = Database(tmp_path / "seed.db")
    await db.connect()
    yield SavedSearchStore(db)
    await db.close()


def _example(name: str, prompt: str) -> dict:
    return {
        "id": f"example-{name}",
        "title": f"Example 1: {prompt}",
        "prompt": prompt,
        "generated_at": "2026-08-08T14:45:52.776840+00:00",
        "result": EXAMPLE_RESULT,
    }


async def test_examples_are_written_into_an_empty_history(store):
    seeded = await store.seed_examples([_example("p01", "Somewhere warm for a month.")])

    assert seeded == 1
    sessions = await store.list_sessions()
    assert [s["title"] for s in sessions] == ["Example 1: Somewhere warm for a month."]
    assert sessions[0]["response"] == EXAMPLE_RESULT["response"]


async def test_a_history_with_anything_in_it_is_left_alone(store):
    await store.save_session(prompt="My own search.", result_data=EXAMPLE_RESULT)

    seeded = await store.seed_examples([_example("p01", "Somewhere warm for a month.")])

    assert seeded == 0
    sessions = await store.list_sessions()
    assert [s["original_request"] for s in sessions] == ["My own search."]


async def test_seeding_twice_does_not_duplicate(store):
    examples = [_example("p01", "Somewhere warm for a month.")]

    await store.seed_examples(examples)
    await store.seed_examples(examples)

    assert len(await store.list_sessions()) == 1


async def test_the_date_shown_is_when_the_answer_was_produced(store):
    """Stamping them "now" on every cold start would date a 2026-08-08 answer as
    freshly generated."""
    await store.seed_examples([_example("p01", "Somewhere warm for a month.")])

    session = (await store.list_sessions())[0]
    assert session["created_at"].startswith("2026-08-08")
    assert session["updated_at"].startswith("2026-08-08")


async def test_the_listing_order_follows_the_example_numbering(store):
    """Example 1 first, whatever order the underlying runs happened in."""
    examples = [
        {**_example("p01", "Run first, listed first."), "listed_at": "2026-08-08T15:00:00+00:00"},
        {**_example("p06", "Run last, listed last."), "listed_at": "2026-08-08T14:57:00+00:00"},
    ]
    # The one produced *later* must still be listed second.
    examples[1]["generated_at"] = "2026-08-08T14:59:18+00:00"

    await store.seed_examples(examples)

    sessions = await store.list_sessions()
    assert [s["id"] for s in sessions] == ["example-p01", "example-p06"]


async def test_running_an_example_prompt_updates_it_rather_than_duplicating(store):
    """The row carries the true hash of its prompt, so save_session finds it."""
    prompt = "Somewhere warm for a month."
    await store.seed_examples([_example("p01", prompt)])

    await store.save_session(prompt=prompt, result_data=EXAMPLE_RESULT)

    sessions = await store.list_sessions()
    assert len(sessions) == 1
    assert not sessions[0]["title"].startswith("Example ")


def test_the_shipped_file_parses_and_carries_complete_answers():
    examples = load_example_sessions()

    assert len(examples) >= 1
    for example in examples:
        assert example["title"].startswith("Example ")
        assert example["result"]["status"] == "ok"
        assert "## " in example["result"]["response"]
        # The execution-steps panel is part of what an example demonstrates.
        assert example["result"]["steps"], example["id"]


def test_the_examples_are_numbered_in_the_order_they_are_listed():
    """The sidebar sorts on updated_at, newest first, so Example 1 needs the
    latest listed_at for the numbering to read 1, 2, 3, 4 downwards."""
    examples = load_example_sessions()
    numbers = [int(e["title"].split(":")[0].removeprefix("Example ")) for e in examples]
    listed = [e["listed_at"] for e in examples]

    assert numbers == sorted(numbers)
    assert listed == sorted(listed, reverse=True)


def test_an_example_is_dated_when_it_was_produced_not_when_it_is_listed():
    """listed_at only orders the sidebar; it must not restate the answer's age
    as newer than it is."""
    for example in load_example_sessions():
        assert example["generated_at"].startswith("2026-08-08")
        assert example["listed_at"].startswith("2026-08-08")
