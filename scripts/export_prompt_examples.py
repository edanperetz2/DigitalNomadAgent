"""Export a captured run in the brief's `prompt_examples` shape.

The brief specifies exactly one structure for showing what the agent does with a
prompt (`GET /api/agent_info`):

    {"prompt": "...", "full_response": "...", "steps": [
        {"module": "...", "prompt": {"System_prompt": "...", "User_prompt": "..."},
         "response": {}}]}

Emits that as JSON, and the same content as a readable HTML page -- the JSON is
what the format requires, the page is what a person can actually read. Steps are
collapsed by default because a single system prompt runs to thousands of
characters and would otherwise bury the answer.

    python scripts/export_prompt_examples.py validation_runs/<run-dir>
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def to_prompt_example(record: dict) -> dict[str, Any]:
    """One captured record in the brief's shape, and nothing else."""
    response = record.get("response") or {}
    return {
        "prompt": record.get("prompt", ""),
        "full_response": response.get("response") or "",
        "steps": [
            {
                "module": step.get("module", ""),
                "prompt": {
                    "System_prompt": (step.get("prompt") or {}).get("System_prompt", ""),
                    "User_prompt": (step.get("prompt") or {}).get("User_prompt", ""),
                },
                "response": step.get("response", {}),
            }
            for step in response.get("steps", [])
        ],
    }


def _pre(text: Any) -> str:
    if not isinstance(text, str):
        text = json.dumps(text, indent=2, ensure_ascii=False)
    return f"<pre>{html.escape(text)}</pre>"


def _step_html(index: int, step: dict) -> str:
    return f"""
    <details class="step">
      <summary><span class="n">{index}</span> {html.escape(step["module"])}</summary>
      <h5>System_prompt</h5>{_pre(step["prompt"]["System_prompt"])}
      <h5>User_prompt</h5>{_pre(step["prompt"]["User_prompt"])}
      <h5>response</h5>{_pre(step["response"])}
    </details>"""


def _case_html(record: dict, example: dict) -> str:
    response = record.get("response") or {}
    steps = "".join(_step_html(i, s) for i, s in enumerate(example["steps"], 1))
    return f"""
  <section class="case" id="{html.escape(record.get('id', ''))}">
    <h2>{html.escape(record.get('id', ''))} — {html.escape(record.get('title', ''))}</h2>
    <p class="meta">
      <span class="pill {'ok' if response.get('status') == 'ok' else 'bad'}">
        status: {html.escape(str(response.get('status')))}</span>
      <span class="pill">{record.get('elapsed_seconds', '?')}s</span>
      <span class="pill">{len(example['steps'])} LLM steps</span>
      <span class="pill">{html.escape(record.get('category', ''))}</span>
    </p>
    <h4>prompt</h4>{_pre(example["prompt"])}
    <h4>full_response</h4>
    <div class="answer">{_pre(example["full_response"])}</div>
    <h4>steps</h4>{steps}
  </section>"""


CSS = """
:root { color-scheme: light dark; --fg:#111; --bg:#fff; --muted:#666; --line:#d8d8d8;
        --card:#fafafa; --accent:#2b5fd9; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e8e8e8; --bg:#151515; --muted:#9a9a9a; --line:#333; --card:#1d1d1d;
          --accent:#7aa2f7; } }
:root[data-theme="dark"] { --fg:#e8e8e8; --bg:#151515; --muted:#9a9a9a; --line:#333;
                           --card:#1d1d1d; --accent:#7aa2f7; }
:root[data-theme="light"] { --fg:#111; --bg:#fff; --muted:#666; --line:#d8d8d8;
                            --card:#fafafa; --accent:#2b5fd9; }
body { font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
       color: var(--fg); background: var(--bg); margin: 0; padding: 2rem 1.25rem 4rem; }
main { max-width: 900px; margin: 0 auto; }
h1 { font-size: 1.6rem; margin-bottom: .25rem; }
h2 { font-size: 1.15rem; margin: 0 0 .5rem; }
h4 { font-size: .78rem; text-transform: uppercase; letter-spacing: .07em;
     color: var(--muted); margin: 1.4rem 0 .4rem; }
h5 { font-size: .72rem; text-transform: uppercase; letter-spacing: .06em;
     color: var(--muted); margin: .9rem 0 .3rem; }
.lede { color: var(--muted); margin-top: 0; }
.case { border-top: 1px solid var(--line); padding-top: 1.75rem; margin-top: 2.25rem; }
.meta { margin: 0; display: flex; flex-wrap: wrap; gap: .4rem; }
.pill { font-size: .74rem; padding: .12rem .5rem; border: 1px solid var(--line);
        border-radius: 999px; color: var(--muted); }
.pill.ok { color: #197a3d; border-color: #197a3d55; }
.pill.bad { color: #b3261e; border-color: #b3261e55; }
pre { background: var(--card); border: 1px solid var(--line); border-radius: 6px;
      padding: .75rem .9rem; overflow-x: auto; white-space: pre-wrap;
      word-wrap: break-word; font-size: .84rem; margin: 0; }
.answer pre { background: transparent; border-left: 3px solid var(--accent);
              border-radius: 0; }
details.step { border: 1px solid var(--line); border-radius: 6px; margin-bottom: .5rem;
               background: var(--card); }
details.step > summary { cursor: pointer; padding: .55rem .8rem; font-weight: 600;
                         font-size: .9rem; }
details.step[open] > summary { border-bottom: 1px solid var(--line); }
details.step > *:not(summary) { margin-left: .8rem; margin-right: .8rem; }
details.step > *:last-child { margin-bottom: .8rem; }
.n { display: inline-block; min-width: 1.4em; color: var(--muted); }
nav a { display: inline-block; margin: 0 .5rem .4rem 0; font-size: .84rem;
        color: var(--accent); text-decoration: none; }
"""


def build_html(records: list[dict], examples: list[dict], title: str) -> str:
    nav = "".join(
        f'<a href="#{html.escape(r.get("id",""))}">{html.escape(r.get("id",""))}</a>'
        for r in records
    )
    cases = "".join(_case_html(r, e) for r, e in zip(records, examples, strict=True))
    ok = sum(1 for r in records if (r.get("response") or {}).get("status") == "ok")
    return f"""<title>{html.escape(title)}</title>
<style>{CSS}</style>
<main>
  <h1>{html.escape(title)}</h1>
  <p class="lede">{len(records)} prompts, {ok} returned <code>status: ok</code>.
     Each case below is one <code>prompt_examples</code> entry as the brief defines it:
     <code>prompt</code>, <code>full_response</code>, and every LLM
     <code>step</code> with its <code>System_prompt</code>, <code>User_prompt</code>
     and <code>response</code>. Steps are collapsed — open one to read it.</p>
  <nav>{nav}</nav>
  {cases}
</main>"""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        print(f"not a directory: {run_dir}")
        return 1

    paths = sorted(run_dir.glob("P*.json"))
    if not paths:
        print(f"no P*.json records in {run_dir}")
        return 1

    records = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    examples = [to_prompt_example(r) for r in records]

    out_dir: Path = args.out_dir or run_dir.with_name(f"{run_dir.name}-examples")
    out_dir.mkdir(parents=True, exist_ok=True)
    title = args.title or f"DigitalNomadAgent — {len(records)} prompt examples"

    json_path = out_dir / "prompt_examples.json"
    json_path.write_text(
        json.dumps({"prompt_examples": examples}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    html_path = out_dir / "prompt_examples.html"
    html_path.write_text(build_html(records, examples, title), encoding="utf-8")

    print(f"  {len(examples)} examples")
    print(f"  {json_path}  ({json_path.stat().st_size / 1024:.0f} KB)")
    print(f"  {html_path}  ({html_path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
