"""The example conversations shipped with the app.

These are real recorded responses from the validation run of 2026-08-08, not
generated at startup: reproducing them would mean four LLM calls per cold start
and would not reproduce them faithfully anyway. They exist so a deployment has
something to show. On Vercel the database is `/tmp/digitalnomadagent.db`, wiped
whenever the function goes cold, so without them the first visitor finds an
empty sidebar and no way to see what an answer looks like without paying for
one.

Refreshed by hand from a validation run; the app only ever reads the file.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

EXAMPLES_PATH = Path(__file__).resolve().parents[2] / "assets" / "example_sessions.json"


@lru_cache
def load_example_sessions() -> tuple[dict[str, Any], ...]:
    """The shipped examples, or nothing at all if the file is missing.

    A missing or unreadable file is not worth failing startup over -- the
    examples are a convenience, and an app that serves an empty sidebar is far
    better than one that will not boot.
    """
    try:
        payload = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()

    examples = []
    for example in payload.get("examples", []):
        result = example.get("result") or {}
        if not example.get("prompt") or not result.get("response"):
            continue
        examples.append(example)
    return tuple(examples)
