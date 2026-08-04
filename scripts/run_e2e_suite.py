"""End-to-end capture harness. Records what the system produces; judges nothing.

Drives the ten evaluation prompts against a RUNNING server over real HTTP, so
real research tools, real latency, and the real 285-second deadline are all
exercised -- unlike scripts/golden_set/runner.py, which uses fake tools and an
in-process TestClient.

Start the server yourself first, so the run configuration is explicit:

    # zero cost -- mock LLM, real tools
    $env:MOCK_LLM="true";  uvicorn app.main:app --port 8000

    # SPENDS REAL MONEY -- real LLM, real tools
    $env:MOCK_LLM="false"; uvicorn app.main:app --port 8000

Then:

    python scripts/run_e2e_suite.py --label mock-api
    python scripts/run_e2e_suite.py --label mock-api-interactive --interactive
    python scripts/run_e2e_suite.py --only P01,P07 --label spot-check
    python scripts/run_e2e_suite.py --contract-checks --label contract

Output goes to validation_runs/<timestamp>-<label>/ : one JSON file per prompt
with the full response and steps, a run-level manifest, and the LLM ledger
delta for the run. Nothing is scored -- the analysis is done by reading.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import get_settings  # noqa: E402
from scripts.e2e.prompts import CONTRACT_CHECKS, E2E_PROMPTS, E2EPrompt  # noqa: E402
from scripts.show_llm_usage import load_rows, summarize  # noqa: E402

OUTPUT_ROOT = REPO_ROOT / "validation_runs"
# The browser aborts at 295s (app/static/app.js EXECUTE_TIMEOUT_MS); match it so
# the harness observes the same ceiling a real user would.
CLIENT_TIMEOUT_SECONDS = 295.0


def _server_mode(base_url: str) -> dict:
    """Ask the running server what it is, so artifacts are self-describing."""
    try:
        response = httpx.get(f"{base_url}/api/agent_info", timeout=10.0)
        response.raise_for_status()
        return {"reachable": True}
    except httpx.HTTPError as exc:
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}


def run_prompt(client: httpx.Client, prompt: E2EPrompt, *, interactive: bool) -> dict:
    headers = {"X-Interactive-Mode": "true"} if interactive else {}
    started = time.monotonic()
    record: dict = {
        "id": prompt.id,
        "title": prompt.title,
        "category": prompt.category,
        "prompt": prompt.prompt,
        "focus": prompt.focus,
        "interactive": interactive,
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        response = client.post("/api/execute", json={"prompt": prompt.prompt}, headers=headers)
        record["elapsed_seconds"] = round(time.monotonic() - started, 2)
        record["http_status"] = response.status_code
        record["session_id"] = response.headers.get("X-DigitalNomadAgent-Session-Id")
        try:
            record["response"] = response.json()
        except ValueError:
            record["response_text"] = response.text[:5000]
            record["error"] = "response body was not JSON"
    except httpx.HTTPError as exc:
        record["elapsed_seconds"] = round(time.monotonic() - started, 2)
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def run_contract_check(client: httpx.Client, check_id: str, description: str, value: object) -> dict:
    """Malformed/boundary requests. Each must yield the strict four-field envelope."""
    if value is None:
        body: dict = {}
    elif value == "__EXTRA_FIELD__":
        body = {"prompt": "A short valid prompt.", "unexpected_field": "should be rejected"}
    else:
        body = {"prompt": value}

    record: dict = {"id": check_id, "description": description, "request_body": body}
    try:
        response = client.post("/api/execute", json=body)
        record["http_status"] = response.status_code
        try:
            payload = response.json()
            record["response"] = payload
            record["envelope_keys"] = sorted(payload.keys()) if isinstance(payload, dict) else None
            record["leaked_detail_key"] = isinstance(payload, dict) and "detail" in payload
        except ValueError:
            record["response_text"] = response.text[:2000]
    except httpx.HTTPError as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def _ledger_snapshot() -> dict:
    settings = get_settings()
    rows = load_rows(settings.sqlite_path_resolved)
    return summarize(rows, settings.max_project_budget_usd)


def _print_ledger_delta(before: dict, after: dict) -> None:
    calls = after["calls"] - before["calls"]
    cost = after["total_cost_usd"] - before["total_cost_usd"]
    tokens_in = after["input_tokens"] - before["input_tokens"]
    tokens_out = after["output_tokens"] - before["output_tokens"]
    print()
    print("-" * 66)
    print("LLM ledger delta for this run")
    print(f"  Calls          : {calls}")
    print(f"  Input tokens   : {tokens_in:,}")
    print(f"  Output tokens  : {tokens_out:,}")
    print(f"  Cost           : ${cost:.4f}")
    print(f"  Ledger total   : ${after['total_cost_usd']:.4f} of ${after['max_budget_usd']:.2f}")
    print(f"  Remaining      : ${after['remaining_usd']:.4f}")
    if calls and cost == 0:
        print("  NOTE: $0.00 across real calls means pricing is unset and the provider")
        print("        reported no cost. Reconcile with scripts/probe_llmod_account.py.")
    print("-" * 66)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Running server URL.")
    parser.add_argument("--label", default="run", help="Label for the output directory.")
    parser.add_argument("--only", default=None, help="Comma-separated prompt IDs, e.g. P01,P07.")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Send X-Interactive-Mode: true, enabling clarification questions (what the UI does).",
    )
    parser.add_argument("--contract-checks", action="store_true", help="Run the API-contract checks instead.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    reachability = _server_mode(base_url)
    if not reachability["reachable"]:
        print(f"Cannot reach a server at {base_url}: {reachability['error']}")
        print("Start one first, e.g.  uvicorn app.main:app --port 8000")
        return 1

    prompts = list(E2E_PROMPTS)
    if args.only:
        wanted = {p.strip().upper() for p in args.only.split(",")}
        prompts = [p for p in prompts if p.id in wanted]
        if not prompts:
            print(f"No prompts matched {sorted(wanted)}.")
            return 1

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = OUTPUT_ROOT / f"{timestamp}-{args.label}"
    out_dir.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    ledger_before = _ledger_snapshot()

    print(f"Server        : {base_url}")
    print(f"MOCK_LLM      : {settings.mock_llm}   (as this process reads .env; the SERVER's value governs)")
    print(f"Interactive   : {args.interactive}")
    print(f"Output        : {out_dir}")
    print()

    records: list[dict] = []
    with httpx.Client(base_url=base_url, timeout=CLIENT_TIMEOUT_SECONDS) as client:
        if args.contract_checks:
            for check_id, description, value in CONTRACT_CHECKS:
                print(f"  {check_id}  {description} ... ", end="", flush=True)
                record = run_contract_check(client, check_id, description, value)
                print(f"HTTP {record.get('http_status', 'ERR')}")
                records.append(record)
                (out_dir / f"{check_id}.json").write_text(
                    json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
                )
        else:
            for prompt in prompts:
                print(f"  {prompt.id}  {prompt.title} ... ", end="", flush=True)
                record = run_prompt(client, prompt, interactive=args.interactive)
                status = record.get("response", {}).get("status") if "response" in record else "ERROR"
                print(f"{record.get('elapsed_seconds', '?')}s  status={status}")
                records.append(record)
                (out_dir / f"{prompt.id}.json").write_text(
                    json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
                )

    ledger_after = _ledger_snapshot()
    manifest = {
        "timestamp": timestamp,
        "label": args.label,
        "base_url": base_url,
        "interactive": args.interactive,
        "contract_checks": args.contract_checks,
        "mock_llm_local_view": settings.mock_llm,
        "model": settings.llmod_model,
        "temperature": settings.llm_temperature,
        "ledger_before": ledger_before,
        "ledger_after": ledger_after,
        "records": [
            {k: v for k, v in r.items() if k not in {"response", "response_text"}} for r in records
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    _print_ledger_delta(ledger_before, ledger_after)
    print(f"\nCaptured {len(records)} record(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
