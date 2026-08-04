"""Guards that the test suite never touches the real SQLite database.

Before the autouse `_isolate_database` fixture existed, any test that built an
app without the `app_instance` fixture (the golden-set harness went through
scripts/golden_set/runner.py) wrote to ./data/digitalnomadagent.db -- polluting
the LLM usage ledger with mock rows, storing fake tool evidence, and inserting
phantom entries into the user's saved search history.
"""

from pathlib import Path

from app.core.config import REPO_ROOT, get_settings

PRODUCTION_DB = REPO_ROOT / "data" / "digitalnomadagent.db"


def test_configured_database_is_not_the_production_database():
    assert get_settings().sqlite_path_resolved != PRODUCTION_DB


def test_configured_database_lives_outside_the_repository():
    resolved = get_settings().sqlite_path_resolved
    assert not resolved.is_relative_to(REPO_ROOT), (
        f"tests must not write inside the repo, got {resolved}"
    )


def test_golden_set_harness_does_not_write_to_the_production_database():
    """The specific path that leaked: create_app() via the golden-set runner."""
    from app.llm.mock import MockLLMClient
    from scripts.golden_set.runner import run_golden_set

    before = _ledger_row_count(PRODUCTION_DB)
    run_golden_set(MockLLMClient())
    assert _ledger_row_count(PRODUCTION_DB) == before


def _ledger_row_count(db_path: Path) -> int | None:
    """Row count of llm_usage, or None if the database does not exist."""
    import sqlite3

    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_usage'"
        ).fetchall():
            return 0
        return conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
    finally:
        conn.close()
