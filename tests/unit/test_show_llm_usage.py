"""Tests for the read-only LLM usage ledger report."""

import sqlite3

import pytest

from app.evidence.database import SCHEMA_SQL
from scripts.show_llm_usage import load_rows, summarize


def _seed(db_path, rows):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        """
        INSERT INTO llm_usage
            (request_id, timestamp, module, model, input_tokens, output_tokens,
             provider_cost_usd, estimated_cost_usd, is_estimated, running_total_usd,
             remaining_budget_usd, success)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


def test_missing_database_reports_no_rows(tmp_path):
    assert load_rows(tmp_path / "nope.db") == []


def test_database_without_ledger_table_reports_no_rows(tmp_path):
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()
    assert load_rows(db_path) == []


def test_summary_totals_prefer_provider_cost(tmp_path):
    db_path = tmp_path / "usage.db"
    _seed(
        db_path,
        [
            ("req1", "2026-08-04T10:00:00Z", "Request Interpreter", "m", 100, 50, 0.10, None, 0, 0.10, 12.90, 1),
            ("req1", "2026-08-04T10:00:05Z", "Agentic Research", "m", 200, 300, 0.20, None, 0, 0.30, 12.70, 1),
        ],
    )

    summary = summarize(load_rows(db_path), max_budget=13.0)

    assert summary["calls"] == 2
    assert summary["requests"] == 1
    assert summary["input_tokens"] == 300
    assert summary["output_tokens"] == 350
    assert summary["total_cost_usd"] == pytest.approx(0.30)
    assert summary["remaining_usd"] == pytest.approx(12.70)
    assert summary["locally_estimated_calls"] == 0


def test_estimated_rows_are_counted_and_flagged(tmp_path):
    db_path = tmp_path / "usage.db"
    _seed(
        db_path,
        [
            ("req1", "2026-08-04T10:00:00Z", "Request Interpreter", "m", 100, 50, None, 0.05, 1, 0.05, 12.95, 1),
            ("req1", "2026-08-04T10:00:05Z", "Agentic Research", "m", 100, 50, 0.10, None, 0, 0.15, 12.85, 1),
        ],
    )

    summary = summarize(load_rows(db_path), max_budget=13.0)

    assert summary["total_cost_usd"] == pytest.approx(0.15)
    assert summary["locally_estimated_calls"] == 1
    assert summary["locally_estimated_cost_usd"] == pytest.approx(0.05)


def test_zero_priced_estimates_are_surfaced_not_hidden(tmp_path):
    """The understated-spend case: estimated rows priced at $0.00."""
    db_path = tmp_path / "usage.db"
    _seed(
        db_path,
        [("req1", "2026-08-04T10:00:00Z", "Request Interpreter", "m", 1000, 900, None, 0.0, 1, 0.0, 13.0, 1)],
    )

    summary = summarize(load_rows(db_path), max_budget=13.0)

    assert summary["total_cost_usd"] == 0.0
    assert summary["locally_estimated_calls"] == 1
    assert summary["locally_estimated_cost_usd"] == 0.0
    # Tokens were still consumed even though the recorded cost is zero.
    assert summary["input_tokens"] == 1000
    assert summary["output_tokens"] == 900


def test_failed_calls_are_counted_and_still_cost(tmp_path):
    db_path = tmp_path / "usage.db"
    _seed(
        db_path,
        [
            ("req1", "2026-08-04T10:00:00Z", "Request Interpreter", "m", 100, 0, 0.02, None, 0, 0.02, 12.98, 0),
            ("req2", "2026-08-04T10:01:00Z", "Request Interpreter", "m", 100, 50, 0.03, None, 0, 0.05, 12.95, 1),
        ],
    )

    summary = summarize(load_rows(db_path), max_budget=13.0)

    assert summary["failed_calls"] == 1
    assert summary["requests"] == 2
    assert summary["total_cost_usd"] == pytest.approx(0.05)


def test_report_is_read_only(tmp_path):
    """Reading the ledger must never modify it."""
    db_path = tmp_path / "usage.db"
    _seed(
        db_path,
        [("req1", "2026-08-04T10:00:00Z", "Request Interpreter", "m", 100, 50, 0.10, None, 0, 0.10, 12.90, 1)],
    )

    load_rows(db_path)
    load_rows(db_path)

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
    conn.close()
    assert count == 1
