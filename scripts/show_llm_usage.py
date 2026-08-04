"""Read-only report of the local LLM usage ledger.

Makes no LLM calls and no network requests -- it only reads the `llm_usage`
table written by BudgetManager. Safe to run at any time, costs $0.

    python scripts/show_llm_usage.py              # summary + per-request totals
    python scripts/show_llm_usage.py --calls      # every individual call
    python scripts/show_llm_usage.py --json       # machine-readable

The totals here are a LOCAL ledger, not the official LLMod.ai balance. Rows
marked "est" were priced by local estimation because the provider reported no
cost; if LLM_INPUT_COST_PER_1M / LLM_OUTPUT_COST_PER_1M are 0, those rows are
$0.00 and the real spend is understated. Reconcile against the provider with
`python scripts/probe_llmod_account.py`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import get_settings  # noqa: E402

_CALL_COLUMNS = (
    "id, request_id, timestamp, module, model, input_tokens, output_tokens, "
    "provider_cost_usd, estimated_cost_usd, is_estimated, running_total_usd, "
    "remaining_budget_usd, success"
)


def _effective_cost(row: sqlite3.Row) -> float:
    if row["provider_cost_usd"] is not None:
        return float(row["provider_cost_usd"])
    return float(row["estimated_cost_usd"] or 0.0)


def load_rows(db_path: Path) -> list[sqlite3.Row]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_usage'"
        ).fetchall()
        if not tables:
            return []
        return conn.execute(f"SELECT {_CALL_COLUMNS} FROM llm_usage ORDER BY id").fetchall()
    finally:
        conn.close()


def summarize(rows: list[sqlite3.Row], max_budget: float) -> dict:
    total = sum(_effective_cost(r) for r in rows)
    estimated_rows = [r for r in rows if r["is_estimated"]]
    return {
        "calls": len(rows),
        "failed_calls": sum(1 for r in rows if not r["success"]),
        "requests": len({r["request_id"] for r in rows}),
        "input_tokens": sum(r["input_tokens"] or 0 for r in rows),
        "output_tokens": sum(r["output_tokens"] or 0 for r in rows),
        "total_cost_usd": total,
        "max_budget_usd": max_budget,
        "remaining_usd": max_budget - total,
        "locally_estimated_calls": len(estimated_rows),
        "locally_estimated_cost_usd": sum(_effective_cost(r) for r in estimated_rows),
    }


def _format_summary(summary: dict) -> str:
    pct = (summary["total_cost_usd"] / summary["max_budget_usd"] * 100) if summary["max_budget_usd"] else 0.0
    lines = [
        "=" * 66,
        "LLM usage ledger (local -- not the official LLMod.ai balance)",
        "=" * 66,
        f"  Requests            : {summary['requests']}",
        f"  LLM calls           : {summary['calls']}  ({summary['failed_calls']} failed)",
        f"  Input tokens        : {summary['input_tokens']:,}",
        f"  Output tokens       : {summary['output_tokens']:,}",
        f"  Total cost          : ${summary['total_cost_usd']:.4f}",
        f"  Budget cap          : ${summary['max_budget_usd']:.2f}",
        f"  Remaining           : ${summary['remaining_usd']:.4f}  ({pct:.1f}% of cap used)",
    ]
    if summary["locally_estimated_calls"]:
        lines.append(
            f"  Locally estimated   : {summary['locally_estimated_calls']} call(s), "
            f"${summary['locally_estimated_cost_usd']:.4f} -- provider reported no cost"
        )
        if summary["locally_estimated_cost_usd"] == 0:
            lines.append(
                "    WARNING: those calls are priced at $0.00. Set LLM_INPUT_COST_PER_1M /"
            )
            lines.append(
                "    LLM_OUTPUT_COST_PER_1M, or real spend is understated by this report."
            )
    return "\n".join(lines)


def _format_per_request(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return ""
    per_request: dict[str, dict] = {}
    for row in rows:
        entry = per_request.setdefault(
            row["request_id"], {"calls": 0, "cost": 0.0, "input": 0, "output": 0, "timestamp": row["timestamp"]}
        )
        entry["calls"] += 1
        entry["cost"] += _effective_cost(row)
        entry["input"] += row["input_tokens"] or 0
        entry["output"] += row["output_tokens"] or 0

    lines = ["", "Per request:", f"  {'request_id':<38} {'calls':>5} {'in':>8} {'out':>8} {'cost':>10}"]
    for request_id, entry in per_request.items():
        lines.append(
            f"  {request_id:<38} {entry['calls']:>5} {entry['input']:>8,} "
            f"{entry['output']:>8,} {entry['cost']:>9.4f}$"
        )
    return "\n".join(lines)


def _format_calls(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return ""
    lines = [
        "",
        "Individual calls:",
        f"  {'#':>3} {'module':<28} {'in':>7} {'out':>7} {'cost':>10} {'src':<5} {'ok':<3} timestamp",
    ]
    for row in rows:
        lines.append(
            f"  {row['id']:>3} {(row['module'] or '')[:28]:<28} "
            f"{row['input_tokens'] or 0:>7,} {row['output_tokens'] or 0:>7,} "
            f"{_effective_cost(row):>9.4f}$ {'est' if row['is_estimated'] else 'prov':<5} "
            f"{'yes' if row['success'] else 'NO':<3} {row['timestamp']}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calls", action="store_true", help="List every individual LLM call.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--db", type=Path, default=None, help="Override the SQLite path.")
    args = parser.parse_args()

    settings = get_settings()
    db_path = args.db or settings.sqlite_path_resolved
    rows = load_rows(db_path)
    summary = summarize(rows, settings.max_project_budget_usd)

    if args.json:
        print(json.dumps({"db_path": str(db_path), "summary": summary,
                          "calls": [dict(r) for r in rows]}, indent=2))
        return 0

    print(f"Database: {db_path}")
    if not rows:
        print("No LLM usage recorded yet (no ledger rows).")
        print("This is expected when only MOCK_LLM=true runs have happened -- the mock")
        print("client is never routed through the budget ledger.")
        return 0

    print(_format_summary(summary))
    print(_format_per_request(rows))
    if args.calls:
        print(_format_calls(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
