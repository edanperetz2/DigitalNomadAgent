"""Render a captured e2e run as readable Markdown, one file per prompt.

Step 3 of the validation loop is "read the answers, do not trust the status":
`10/10 ok` has twice concealed bad advice, because the golden set checks
structure -- four modules ran, a table exists, no banned claim leaked -- and
nothing in it checks whether the recommendation is *correct*. That only comes
out by reading the prose, and JSON records are not readable prose.

    python scripts/render_answers.py validation_runs/<run-dir>
    python scripts/render_answers.py validation_runs/<run-dir> --out validation_runs/<name>-answers

Each prompt becomes `<ID>.md` carrying the case metadata, the months the
interpreter read from the request (the D31 regression that scored all ten
prompts against the current calendar month), the prompt itself, and the answer
verbatim. Nothing is scored or judged here -- that is the reader's job.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

INTERPRETER_MODULE = "Request Interpreter"


def _target_months(record: dict) -> str:
    """The months the interpreter resolved, as recorded in the trace.

    Note this is the interpreter's *raw* output: the orchestrator applies
    further guards (dropping indifferent deal-breakers, resolving ambiguous
    profiles) after this is captured, so the trace is what the model said, not
    necessarily what the pipeline went on to use.
    """
    for step in record.get("response", {}).get("steps", []):
        if step.get("module") != INTERPRETER_MODULE:
            continue
        response = step.get("response")
        if isinstance(response, dict):
            months = response.get("target_months")
            if months:
                return f"`{months}`"
            return "_empty_ — climate is not scored"
    return "_not recorded_"


def render(record: dict) -> str:
    response = record.get("response", {})
    answer = response.get("response") or "_(no answer returned)_"
    error = response.get("error")

    lines = [
        f"# {record.get('id', '?')} — {record.get('title', '')}".rstrip(" —"),
        "",
        f"**What this case tests:** {record.get('focus', '—')}",
        "",
        f"**Run:** {record.get('started_at', '?')} · "
        f"{record.get('elapsed_seconds', '?')}s · "
        f"status `{response.get('status', '?')}`",
        f"**Months the agent read from the request:** {_target_months(record)}",
    ]
    if error:
        lines.append(f"**Error:** `{error}`")
    lines += [
        "",
        "---",
        "",
        "## The prompt",
        "",
        *[f"> {line}" for line in record.get("prompt", "").splitlines()],
        "",
        "---",
        "",
        "## The agent's answer",
        "",
        answer,
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # place names die on cp1252

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="a validation_runs/<timestamp>-<label> directory")
    parser.add_argument("--out", type=Path, default=None, help="defaults to <run_dir>-answers")
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        print(f"not a directory: {run_dir}")
        return 1

    out_dir: Path = args.out or run_dir.with_name(f"{run_dir.name}-answers")
    out_dir.mkdir(parents=True, exist_ok=True)

    records = sorted(p for p in run_dir.glob("P*.json"))
    if not records:
        print(f"no P*.json records in {run_dir}")
        return 1

    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        target = out_dir / f"{path.stem}.md"
        target.write_text(render(record), encoding="utf-8")
        status = record.get("response", {}).get("status", "?")
        print(f"  {path.stem}  status={status}  -> {target.name}")

    print(f"\n{len(records)} answer(s) written to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
