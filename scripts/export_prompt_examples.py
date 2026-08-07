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
import re
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


def _inline(text: str) -> str:
    """Escape, then restore the inline marks the generator actually emits."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def render_markdown(text: str) -> str:
    """Render the answer's Markdown.

    Deliberately narrow: it handles exactly what the Recommendation Generator
    emits -- headings, pipe tables, bullet and numbered lists, bold, code -- and
    nothing else. The comparison table is the centre of most answers, so leaving
    it as a wall of pipes would make the whole document harder to check than the
    JSON it came from.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    list_tag: str | None = None
    para: list[str] = []

    def close_para() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            close_para()
            close_list()
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            close_para()
            close_list()
            level = min(len(heading.group(1)) + 2, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        # A pipe table: header row, separator row, then body rows.
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(
            r"^\|[\s:|-]+\|$", lines[i + 1].strip()
        ):
            close_para()
            close_list()

            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]

            head = cells(stripped)
            i += 2
            body: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(cells(lines[i].strip()))
                i += 1
            head_html = "".join(f"<th>{_inline(c)}</th>" for c in head)
            rows_html = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in body
            )
            out.append(
                f'<div class="scroll"><table><thead><tr>{head_html}</tr></thead>'
                f"<tbody>{rows_html}</tbody></table></div>"
            )
            continue

        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        numbered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if bullet or numbered:
            close_para()
            wanted = "ul" if bullet else "ol"
            if list_tag != wanted:
                close_list()
                out.append(f"<{wanted}>")
                list_tag = wanted
            out.append(f"<li>{_inline((bullet or numbered).group(1))}</li>")
            i += 1
            continue

        close_list()
        para.append(stripped)
        i += 1

    close_para()
    close_list()
    return "\n".join(out)


def _step_html(index: int, step: dict) -> str:
    return f"""
      <details class="step">
        <summary><span class="n">{index}</span>{html.escape(step["module"])}</summary>
        <div class="body">
          <p class="label">System_prompt</p>{_pre(step["prompt"]["System_prompt"])}
          <p class="label">User_prompt</p>{_pre(step["prompt"]["User_prompt"])}
          <p class="label">response</p>{_pre(step["response"])}
        </div>
      </details>"""


def _case_html(record: dict, example: dict) -> str:
    response = record.get("response") or {}
    status = str(response.get("status"))
    steps = "".join(_step_html(i, s) for i, s in enumerate(example["steps"], 1))
    pid = html.escape(record.get("id", ""))
    return f"""
    <section class="case" id="{pid}">
      <header>
        <p class="id">{pid}</p>
        <h2>{html.escape(record.get('title', ''))}</h2>
        <ul class="meta">
          <li class="pill {'ok' if status == 'ok' else 'bad'}">status: {html.escape(status)}</li>
          <li class="pill">{record.get('elapsed_seconds', '?')}s</li>
          <li class="pill">{len(example['steps'])} LLM steps</li>
          <li class="pill">{html.escape(record.get('category', ''))}</li>
        </ul>
      </header>
      <p class="label">prompt</p>
      <blockquote class="ask">{_inline(example["prompt"])}</blockquote>
      <p class="label">full_response</p>
      <div class="answer">{render_markdown(example["full_response"])}</div>
      <p class="label">steps</p>
      {steps}
    </section>"""


CSS = """
:root {
  color-scheme: light dark;
  --ink:#16202b; --paper:#fbfcfd; --raise:#f2f5f8; --muted:#5d6b7a;
  --rule:#dde3e9; --accent:#1a6a66; --ok:#2f6f4f; --bad:#a4402f;
  --serif: Georgia, "Iowan Old Style", "Palatino Linotype", "Times New Roman", serif;
  --sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root { --ink:#e4eaf0; --paper:#11171d; --raise:#182029; --muted:#93a3b3;
          --rule:#2a343f; --accent:#63bdb4; --ok:#6bbf8e; --bad:#e08a76; }
}
:root[data-theme="dark"] { --ink:#e4eaf0; --paper:#11171d; --raise:#182029; --muted:#93a3b3;
                           --rule:#2a343f; --accent:#63bdb4; --ok:#6bbf8e; --bad:#e08a76; }
:root[data-theme="light"] { --ink:#16202b; --paper:#fbfcfd; --raise:#f2f5f8; --muted:#5d6b7a;
                            --rule:#dde3e9; --accent:#1a6a66; --ok:#2f6f4f; --bad:#a4402f; }

* { box-sizing: border-box; }
body { margin:0; background: var(--paper); color: var(--ink);
       font: 16px/1.65 var(--sans); -webkit-text-size-adjust: 100%; }
.wrap { display:grid; grid-template-columns: minmax(0,1fr); gap: 2rem;
        max-width: 1180px; margin: 0 auto; padding: 2.5rem 1.25rem 6rem; }
@media (min-width: 940px) { .wrap { grid-template-columns: 9.5rem minmax(0,1fr); gap: 3rem; } }

.masthead { grid-column: 1/-1; border-bottom: 2px solid var(--ink); padding-bottom: 1.25rem; }
.masthead h1 { font-family: var(--serif); font-weight: 600; font-size: clamp(1.5rem,3.4vw,2.1rem);
               line-height:1.18; margin:0 0 .4rem; text-wrap: balance; letter-spacing:-.01em; }
.kicker { font-size:.7rem; text-transform:uppercase; letter-spacing:.14em; color: var(--accent);
          margin:0 0 .55rem; font-weight:600; }
.lede { color: var(--muted); margin:0; max-width: 62ch; font-size:.95rem; }
.tally { display:flex; flex-wrap:wrap; gap:1.4rem; margin-top:1.1rem; }
.tally div { display:flex; flex-direction:column; }
.tally b { font-family: var(--serif); font-size:1.35rem; font-variant-numeric: tabular-nums;
           font-weight:600; line-height:1; }
.tally span { font-size:.68rem; text-transform:uppercase; letter-spacing:.1em; color:var(--muted);
              margin-top:.3rem; }

.index { font-size:.82rem; }
@media (min-width: 940px) { .index { position: sticky; top: 1.5rem; align-self: start; } }
.index ol { list-style:none; margin:0; padding:0; display:flex; flex-wrap:wrap; gap:.3rem; }
@media (min-width: 940px) { .index ol { flex-direction:column; gap:.1rem; } }
.index a { display:flex; gap:.45rem; align-items:baseline; text-decoration:none; color:var(--muted);
           padding:.18rem .3rem; border-radius:3px; font-variant-numeric: tabular-nums; }
.index a:hover, .index a:focus-visible { color: var(--accent); background: var(--raise); }
.index .dot { width:.42rem; height:.42rem; border-radius:50%; background:var(--ok); flex:none;
              transform: translateY(-.12em); }
.index .dot.bad { background: var(--bad); }

.cases { display:flex; flex-direction:column; gap:3.5rem; min-width:0; }
.case { min-width:0; scroll-margin-top:1.5rem; }
.case > header { border-top:1px solid var(--rule); padding-top:1.4rem; }
.case h2 { font-family: var(--serif); font-weight:600; font-size:1.28rem; margin:.1rem 0 .5rem;
           text-wrap:balance; letter-spacing:-.005em; }
.case .id { font-family: var(--mono); font-size:.72rem; letter-spacing:.08em; color:var(--accent); }
.meta { display:flex; flex-wrap:wrap; gap:.4rem; margin:0; padding:0; list-style:none; }
.pill { font-size:.7rem; padding:.13rem .5rem; border:1px solid var(--rule); border-radius:2px;
        color:var(--muted); font-variant-numeric: tabular-nums; }
.pill.ok { color:var(--ok); border-color:color-mix(in srgb, var(--ok) 40%, transparent); }
.pill.bad { color:var(--bad); border-color:color-mix(in srgb, var(--bad) 40%, transparent); }

.label { font-family: var(--mono); font-size:.66rem; text-transform:uppercase; letter-spacing:.12em;
         color:var(--muted); margin:1.9rem 0 .5rem; }
.ask { font-family: var(--serif); font-size:1.02rem; line-height:1.6; margin:0;
       padding:.9rem 0 .9rem 1.1rem; border-left:2px solid var(--accent); max-width:68ch; }

.answer { max-width:68ch; }
.answer h3 { font-family:var(--serif); font-size:1.08rem; margin:1.7rem 0 .5rem; font-weight:600; }
.answer h4 { font-size:.78rem; text-transform:uppercase; letter-spacing:.09em; color:var(--muted);
             margin:1.3rem 0 .35rem; }
.answer h5, .answer h6 { font-size:.9rem; margin:1rem 0 .3rem; }
.answer p { margin:.65rem 0; }
.answer ul, .answer ol { margin:.6rem 0; padding-left:1.3rem; }
.answer li { margin:.28rem 0; }
.answer code { font-family:var(--mono); font-size:.86em; background:var(--raise); padding:.05em .3em;
               border-radius:2px; }
.scroll { overflow-x:auto; margin:1rem 0; }
.answer table { border-collapse:collapse; width:100%; font-size:.84rem; min-width:34rem; }
.answer th, .answer td { text-align:left; vertical-align:top; padding:.5rem .7rem;
                         border-bottom:1px solid var(--rule); }
.answer th { font-family:var(--sans); font-size:.68rem; text-transform:uppercase;
             letter-spacing:.09em; color:var(--muted); border-bottom:1px solid var(--ink); }
.answer tbody tr:hover { background: var(--raise); }

pre { font-family:var(--mono); font-size:.78rem; line-height:1.55; background:var(--raise);
      border:1px solid var(--rule); border-radius:3px; padding:.8rem .9rem; margin:0;
      overflow-x:auto; white-space:pre-wrap; word-wrap:break-word; }
details.step { border:1px solid var(--rule); border-radius:3px; margin-bottom:.45rem;
               background:var(--paper); }
details.step > summary { cursor:pointer; padding:.55rem .8rem; font-size:.85rem; font-weight:600;
                         display:flex; gap:.6rem; align-items:baseline; }
details.step > summary::marker { color: var(--muted); }
details.step[open] > summary { border-bottom:1px solid var(--rule); }
details.step .body { padding:.8rem; display:flex; flex-direction:column; gap:.3rem; }
details.step .n { font-family:var(--mono); font-size:.72rem; color:var(--accent); }
a:focus-visible, summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
@media (prefers-reduced-motion: reduce) { * { transition:none !important; animation:none !important; } }
"""


def build_html(records: list[dict], examples: list[dict], title: str, subtitle: str) -> str:
    index = "".join(
        f'<li><a href="#{html.escape(r.get("id",""))}">'
        f'<span class="dot{"" if (r.get("response") or {}).get("status") == "ok" else " bad"}"></span>'
        f'{html.escape(r.get("id",""))}</a></li>'
        for r in records
    )
    cases = "".join(_case_html(r, e) for r, e in zip(records, examples, strict=True))
    ok = sum(1 for r in records if (r.get("response") or {}).get("status") == "ok")
    steps = sum(len(e["steps"]) for e in examples)
    slowest = max((r.get("elapsed_seconds") or 0) for r in records)
    return f"""<title>{html.escape(title)}</title>
<style>{CSS}</style>
<div class="wrap">
  <header class="masthead">
    <p class="kicker">Evaluation transcript</p>
    <h1>{html.escape(title)}</h1>
    <p class="lede">{html.escape(subtitle)} Every case below is one
      <code>prompt_examples</code> entry in the shape the brief defines:
      <code>prompt</code>, <code>full_response</code>, and each LLM
      <code>step</code> with its <code>System_prompt</code>,
      <code>User_prompt</code> and <code>response</code>.</p>
    <div class="tally">
      <div><b>{ok}/{len(records)}</b><span>status ok</span></div>
      <div><b>{steps}</b><span>LLM steps</span></div>
      <div><b>{slowest:.0f}s</b><span>slowest</span></div>
    </div>
  </header>
  <nav class="index" aria-label="Prompts"><ol>{index}</ol></nav>
  <main class="cases">{cases}</main>
</div>"""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--subtitle", default="")
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
    html_path.write_text(build_html(records, examples, title, args.subtitle), encoding="utf-8")

    print(f"  {len(examples)} examples")
    print(f"  {json_path}  ({json_path.stat().st_size / 1024:.0f} KB)")
    print(f"  {html_path}  ({html_path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
