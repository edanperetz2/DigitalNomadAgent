"""Render assets/model_architecture.png from the code's own module and tool names.

The brief requires that every sub-module / sub-agent name match across the
architecture diagram, the `steps` trace and the written descriptions. The
previous diagram was a hand-made image with no source in the repository, and it
drifted: it was titled "PlaceMatch" long after the app became DigitalNomadAgent,
it listed three tools that had been deleted (Education Options, Accessibility,
Official Sources), and it omitted six that existed. Nothing could catch that,
because nothing could read it.

This script draws the same diagram from `app.core.module_names`, so the names on
the image are the names in the code by construction. It also writes a sidecar
`assets/model_architecture.labels.json` listing every label it drew, which is
what `tests/unit/test_architecture_png_file.py` asserts against -- a PNG cannot
be diffed, but its label manifest can.

    python scripts/render_architecture.py
    python scripts/render_architecture.py --check   # fail if the PNG is stale

Pillow is the only renderer available here (no cairo, no headless browser), so
the drawing is deliberately plain: flat rounded boxes on a dark background. It
is rendered at 2x and downsampled, which is what keeps the text crisp.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.module_names import (  # noqa: E402
    AGENTIC_RESEARCH,
    ALL_MODULES,
    DYNAMIC_EVALUATION,
    EVIDENCE_MEMORY,
    RECOMMENDATION_GENERATOR,
    RECOMMENDATION_VALIDATOR,
    REQUEST_INTERPRETER,
    TOOL_NAMES,
    tool_display_label,
)

PNG_PATH = REPO_ROOT / "assets" / "model_architecture.png"
LABELS_PATH = REPO_ROOT / "assets" / "model_architecture.labels.json"

TITLE = "DigitalNomadAgent — System Architecture"
SUBTITLE = "Autonomous Evidence-Based Place Recommendation Agent"

# Everything on the canvas that is neither a pipeline module nor a tool. Kept
# explicit so the drift test can assert the label manifest is exactly
# modules + tools + these, and nothing has quietly appeared.
DECORATIVE_LABELS: tuple[str, ...] = (
    TITLE,
    SUBTITLE,
    "Web UI",
    "POST /api/execute",
    "ENTRY POINT",
    "Natural-Language Request",
    "Ranked Recommendations",
    "Tool Registry",
    "External Data Sources",
    "AGENTIC",
    "tool selection",
    "normalized evidence",
    "API requests / evidence responses",
    "Missing evidence -> targeted research (max 1 iteration)",
    "Approved ->",
    "final Dynamic Eval pass",
    "Out of scope",
    "-> declined immediately",
    "(this request ends here)",
    "Ambiguous + interactive",
    "-> returns a question,",
    "caller resubmits",
    "(new request, max 2x)",
    "Legend",
    "DigitalNomadAgent module",
    "Research tool",
    "External data source",
    "Validator feedback loop (max 1 iteration)",
    "Out-of-scope shortcut",
    "Clarification loop",
)

SCALE = 2
WIDTH, HEIGHT = 1680, 1010

BG_TOP = (6, 11, 28)
BG_BOTTOM = (11, 22, 52)
MODULE_FILL = (15, 34, 71)
MODULE_EDGE = (47, 111, 208)
MODULE_TEXT = (234, 242, 255)
PANEL_FILL = (7, 36, 58)
PANEL_EDGE = (31, 143, 168)
PILL_FILL = (13, 58, 82)
PILL_EDGE = (42, 163, 189)
PILL_TEXT = (214, 242, 251)
EXTERNAL_FILL = (58, 36, 8)
EXTERNAL_EDGE = (208, 138, 31)
EXTERNAL_TEXT = (255, 227, 176)
ARROW = (59, 138, 230)
FEEDBACK = (224, 86, 74)
SHORTCUT = (181, 131, 246)
SHORTCUT_TEXT = (206, 178, 245)
ASK_LOOP = (233, 121, 178)
ASK_LOOP_TEXT = (240, 168, 205)
TITLE_TEXT = (255, 255, 255)
SUBTITLE_TEXT = (159, 195, 240)
NOTE_TEXT = (150, 183, 226)
APPROVED_TEXT = (110, 214, 143)

# The seven pipeline modules, in execution order, with the two non-module
# endpoints of the flow. Module strings come from module_names; the two plain
# strings are the request and the result, which are not modules.
PIPELINE: tuple[str, ...] = (
    "Natural-Language Request",
    REQUEST_INTERPRETER,
    AGENTIC_RESEARCH,
    EVIDENCE_MEMORY,
    DYNAMIC_EVALUATION,
    RECOMMENDATION_VALIDATOR,
    RECOMMENDATION_GENERATOR,
    "Ranked Recommendations",
)

_FONT_CANDIDATES = {
    "bold": ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Helvetica-Bold.ttf"),
    "regular": ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttf"),
}


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for name in _FONT_CANDIDATES[weight]:
        try:
            return ImageFont.truetype(name, size * SCALE)
        except OSError:
            continue
    return ImageFont.load_default(size=size * SCALE)


def _s(value: float) -> int:
    return int(round(value * SCALE))


def _background(draw: ImageDraw.ImageDraw) -> None:
    for y in range(HEIGHT * SCALE):
        ratio = y / (HEIGHT * SCALE - 1)
        colour = tuple(
            int(round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * ratio)) for i in range(3)
        )
        draw.line([(0, y), (WIDTH * SCALE, y)], fill=colour)


def _box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float, float, float],
    *,
    fill: tuple[int, int, int],
    edge: tuple[int, int, int],
    radius: float = 12,
    width: float = 2,
) -> None:
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(
        [_s(x0), _s(y0), _s(x1), _s(y1)],
        radius=_s(radius),
        fill=fill,
        outline=edge,
        width=_s(width),
    )


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    *,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    anchor: str = "mm",
) -> None:
    draw.text((_s(xy[0]), _s(xy[1])), text, font=font, fill=fill, anchor=anchor)


def _arrow_head(
    draw: ImageDraw.ImageDraw,
    tip: tuple[float, float],
    direction: str,
    colour: tuple[int, int, int],
    size: float = 9,
) -> None:
    x, y = tip
    if direction == "down":
        points = [(x, y), (x - size, y - size * 1.4), (x + size, y - size * 1.4)]
    elif direction == "up":
        points = [(x, y), (x - size, y + size * 1.4), (x + size, y + size * 1.4)]
    elif direction == "right":
        points = [(x, y), (x - size * 1.4, y - size), (x - size * 1.4, y + size)]
    else:
        points = [(x, y), (x + size * 1.4, y - size), (x + size * 1.4, y + size)]
    draw.polygon([(_s(px), _s(py)) for px, py in points], fill=colour)


def _line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    colour: tuple[int, int, int],
    width: float = 2.5,
) -> None:
    draw.line([(_s(x), _s(y)) for x, y in points], fill=colour, width=_s(width), joint="curve")


def _dashed(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    colour: tuple[int, int, int],
    width: float = 2.5,
    dash: float = 12,
    gap: float = 8,
) -> None:
    """Dash each segment independently; Pillow has no dashed-line primitive."""
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if length == 0:
            continue
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        travelled = 0.0
        while travelled < length:
            end = min(travelled + dash, length)
            draw.line(
                [
                    (_s(x0 + ux * travelled), _s(y0 + uy * travelled)),
                    (_s(x0 + ux * end), _s(y0 + uy * end)),
                ],
                fill=colour,
                width=_s(width),
            )
            travelled = end + gap


def render() -> list[str]:
    """Draw the diagram. Returns every label placed on the canvas."""
    image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), BG_TOP)
    draw = ImageDraw.Draw(image)
    _background(draw)

    f_title = _font("bold", 40)
    f_subtitle = _font("regular", 21)
    f_module = _font("bold", 23)
    f_panel = _font("bold", 25)
    f_pill = _font("regular", 16)
    f_note = _font("regular", 16)
    f_caption = _font("regular", 13)
    f_caption_bold = _font("bold", 14)
    f_legend = _font("regular", 16)
    f_entry = _font("bold", 19)
    f_tag = _font("bold", 11)

    labels: list[str] = []

    _text(draw, (WIDTH / 2, 46), TITLE, font=f_title, fill=TITLE_TEXT)
    _text(draw, (WIDTH / 2, 92), SUBTITLE, font=f_subtitle, fill=SUBTITLE_TEXT)
    labels += [TITLE, SUBTITLE]

    # --- entry points -----------------------------------------------------
    for x0, x1, label in ((70, 258, "Web UI"), (274, 512, "POST /api/execute")):
        _box(draw, (x0, 140, x1, 218), fill=MODULE_FILL, edge=MODULE_EDGE)
        _text(draw, ((x0 + x1) / 2, 172), label, font=f_entry, fill=MODULE_TEXT)
        _box(
            draw,
            ((x0 + x1) / 2 - 52, 190, (x0 + x1) / 2 + 52, 210),
            fill=MODULE_EDGE,
            edge=MODULE_EDGE,
            radius=10,
            width=1,
        )
        _text(draw, ((x0 + x1) / 2, 200), "ENTRY POINT", font=f_tag, fill=(255, 255, 255))
        labels.append(label)
    labels.append("ENTRY POINT")

    # --- pipeline column --------------------------------------------------
    col_x0, col_x1 = 380, 780
    col_mid = (col_x0 + col_x1) / 2
    box_h = 62
    tops = [258, 346, 434, 566, 654, 742, 840, 928]
    centres: dict[str, float] = {}
    for label, top in zip(PIPELINE, tops, strict=True):
        _box(draw, (col_x0, top, col_x1, top + box_h), fill=MODULE_FILL, edge=MODULE_EDGE)
        _text(draw, (col_mid, top + box_h / 2), label, font=f_module, fill=MODULE_TEXT)
        centres[label] = top
        labels.append(label)

    # entry arrows into the request box
    _line(draw, [(164, 218), (164, 289), (col_x0 - 14, 289)], ARROW)
    _line(draw, [(393, 218), (393, 289)], ARROW)
    _arrow_head(draw, (col_x0 - 2, 289), "right", ARROW)

    # straight arrows down the column, skipping the two that carry labels
    for label, nxt in zip(PIPELINE, PIPELINE[1:], strict=False):
        top, next_top = centres[label], centres[nxt]
        if label == AGENTIC_RESEARCH:
            continue  # drawn below, with the candidate-set note
        _line(draw, [(col_mid, top + box_h), (col_mid, next_top - 10)], ARROW)
        _arrow_head(draw, (col_mid, next_top - 2), "down", ARROW)

    research_top = centres[AGENTIC_RESEARCH]
    memory_top = centres[EVIDENCE_MEMORY]
    _line(draw, [(col_mid, research_top + box_h), (col_mid, memory_top - 10)], ARROW)
    _arrow_head(draw, (col_mid, memory_top - 2), "down", ARROW)

    validator_bottom = centres[RECOMMENDATION_VALIDATOR] + box_h
    _text(
        draw,
        (col_mid + 14, validator_bottom + 12),
        "Approved ->",
        font=f_note,
        fill=APPROVED_TEXT,
        anchor="lm",
    )
    _text(
        draw,
        (col_mid + 14, validator_bottom + 29),
        "final Dynamic Eval pass",
        font=f_caption,
        fill=APPROVED_TEXT,
        anchor="lm",
    )
    labels += ["Approved ->", "final Dynamic Eval pass"]

    # --- tool registry ----------------------------------------------------
    panel_x0, panel_x1 = 920, 1620
    panel_y0, panel_y1 = 300, 622
    _box(draw, (panel_x0, panel_y0, panel_x1, panel_y1), fill=PANEL_FILL, edge=PANEL_EDGE, radius=16)
    _text(draw, ((panel_x0 + panel_x1) / 2, panel_y0 + 36), "Tool Registry", font=f_panel, fill=PILL_TEXT)
    labels.append("Tool Registry")

    per_row = 3
    pill_w, pill_h, gap_x, gap_y = 210, 40, 14, 12
    grid_w = per_row * pill_w + (per_row - 1) * gap_x
    grid_x0 = (panel_x0 + panel_x1) / 2 - grid_w / 2
    grid_y0 = panel_y0 + 68
    for index, tool_name in enumerate(TOOL_NAMES):
        label = tool_display_label(tool_name)
        row, col = divmod(index, per_row)
        x0 = grid_x0 + col * (pill_w + gap_x)
        y0 = grid_y0 + row * (pill_h + gap_y)
        _box(draw, (x0, y0, x0 + pill_w, y0 + pill_h), fill=PILL_FILL, edge=PILL_EDGE, radius=10, width=1.5)
        _text(draw, (x0 + pill_w / 2, y0 + pill_h / 2), label, font=f_pill, fill=PILL_TEXT)
        labels.append(label)

    # --- external data sources -------------------------------------------
    ext_y0, ext_y1 = 706, 786
    _box(draw, (panel_x0, ext_y0, panel_x1, ext_y1), fill=EXTERNAL_FILL, edge=EXTERNAL_EDGE, radius=14)
    _text(
        draw,
        ((panel_x0 + panel_x1) / 2, (ext_y0 + ext_y1) / 2),
        "External Data Sources",
        font=f_panel,
        fill=EXTERNAL_TEXT,
    )
    labels.append("External Data Sources")

    panel_mid_x = (panel_x0 + panel_x1) / 2
    _line(draw, [(panel_mid_x - 24, panel_y1 + 8), (panel_mid_x - 24, ext_y0 - 8)], EXTERNAL_EDGE)
    _arrow_head(draw, (panel_mid_x - 24, ext_y0 - 2), "down", EXTERNAL_EDGE)
    _line(draw, [(panel_mid_x + 24, ext_y0 - 8), (panel_mid_x + 24, panel_y1 + 8)], EXTERNAL_EDGE)
    _arrow_head(draw, (panel_mid_x + 24, panel_y1 + 2), "up", EXTERNAL_EDGE)
    _text(
        draw,
        (panel_mid_x + 46, (panel_y1 + ext_y0) / 2),
        "API requests / evidence responses",
        font=f_note,
        fill=NOTE_TEXT,
        anchor="lm",
    )
    labels.append("API requests / evidence responses")

    # --- research <-> registry -------------------------------------------
    # The gap between the column and the panel is only ~140px, so the two flow
    # captions are stacked rather than set on one line.
    gap_mid = (col_x1 + panel_x0) / 2
    research_mid_y = research_top + box_h / 2
    _line(draw, [(col_x1, research_mid_y), (panel_x0 - 10, research_mid_y)], ARROW)
    _arrow_head(draw, (panel_x0 - 2, research_mid_y), "right", ARROW)
    _text(draw, (gap_mid, research_mid_y - 32), "AGENTIC", font=f_caption_bold, fill=APPROVED_TEXT)
    _text(draw, (gap_mid, research_mid_y - 14), "tool selection", font=f_caption, fill=NOTE_TEXT)
    labels += ["AGENTIC", "tool selection"]

    memory_mid_y = memory_top + box_h / 2
    _line(draw, [(panel_x0, memory_mid_y), (col_x1 + 10, memory_mid_y)], ARROW)
    _arrow_head(draw, (col_x1 + 2, memory_mid_y), "left", ARROW)
    _text(draw, (gap_mid, memory_mid_y - 18), "normalized evidence", font=f_caption, fill=NOTE_TEXT)
    labels.append("normalized evidence")

    # --- validator feedback loop -----------------------------------------
    validator_top = centres[RECOMMENDATION_VALIDATOR]
    loop_x = 830
    _dashed(
        draw,
        [
            (col_x1, validator_top + box_h / 2),
            (loop_x, validator_top + box_h / 2),
            (loop_x, research_mid_y + 34),
            (col_x1 + 10, research_mid_y + 34),
        ],
        FEEDBACK,
    )
    _arrow_head(draw, (col_x1 + 2, research_mid_y + 34), "left", FEEDBACK)
    # Set beside the loop's vertical run rather than in open space, so it is
    # obvious which arrow it belongs to. The free column here is ~125px wide.
    for offset, line in enumerate(("Missing evidence ->", "targeted research", "(max 1 iteration)")):
        _text(
            draw,
            (loop_x + 16, 656 + offset * 18),
            line,
            font=f_caption,
            fill=FEEDBACK,
            anchor="lm",
        )
    labels.append("Missing evidence -> targeted research (max 1 iteration)")

    # --- out-of-scope shortcut ---------------------------------------------
    # An out-of-scope request returns straight from the interpreter and never
    # enters the rest of the pipeline -- genuinely terminal for this request,
    # so it is drawn as a bypass straight to the final response. Routed
    # through the empty margin left of the column, clear of the legend
    # (legend right edge is x=358, column left edge is x=380).
    #
    # This shares its source box with the clarification loop below, but the
    # two must never read as one line (an earlier draft of this diagram did
    # exactly that): each gets its own colour, its own origin marker at a
    # clearly different height on Request Interpreter's edge, and its own
    # caption anchored at ITS origin rather than the midpoint of its route --
    # centring "Out of scope" on the full run down to Ranked Recommendations
    # previously landed the caption beside Dynamic Evaluation instead of
    # beside the module that actually makes the decision.
    shortcut_x = 368
    interpreter_top = centres[REQUEST_INTERPRETER]
    final_mid_y = centres["Ranked Recommendations"] + box_h / 2
    scope_origin_y = interpreter_top + box_h - 12
    _dashed(
        draw,
        [
            (col_x0, scope_origin_y),
            (shortcut_x, scope_origin_y),
            (shortcut_x, final_mid_y),
            (col_x0 - 10, final_mid_y),
        ],
        SHORTCUT,
    )
    _arrow_head(draw, (col_x0 - 2, final_mid_y), "right", SHORTCUT)
    draw.ellipse(
        [_s(col_x0 - 4), _s(scope_origin_y - 4), _s(col_x0 + 4), _s(scope_origin_y + 4)],
        fill=SHORTCUT,
    )
    scope_lines: tuple[tuple[str, ImageFont.FreeTypeFont], ...] = (
        ("Out of scope", f_caption_bold),
        ("-> declined immediately", f_caption),
        ("(this request ends here)", f_caption),
    )
    for offset, (line, font) in enumerate(scope_lines):
        _text(
            draw,
            (shortcut_x - 18, scope_origin_y + 14 + offset * 19),
            line,
            font=font,
            fill=SHORTCUT_TEXT,
            anchor="rm",
        )
        labels.append(line)

    # --- ambiguous + interactive: NOT a shortcut to an answer --------------
    # This one must look different from the out-of-scope bypass above: it
    # returns a bare question and stops -- every /api/execute call is
    # stateless (no round is tracked server-side), so the follow-up reply is
    # a brand-new request that re-enters at the top, not a continuation of
    # this run. Drawn as a small loop back to Natural-Language Request
    # instead of a bypass forward, so it cannot be misread as also reaching
    # Ranked Recommendations in one pass. A distinct rose colour (not a
    # shade of the violet above) plus its own origin marker, near the TOP of
    # Request Interpreter's edge rather than the bottom, so the two markers
    # visibly branch apart from the first pixel instead of running parallel.
    loop_x = 340
    nlr_mid_y = centres["Natural-Language Request"] + box_h / 2
    ask_origin_y = interpreter_top + 12
    _dashed(
        draw,
        [
            (col_x0, ask_origin_y),
            (loop_x, ask_origin_y),
            (loop_x, nlr_mid_y),
            (col_x0 - 10, nlr_mid_y),
        ],
        ASK_LOOP,
    )
    _arrow_head(draw, (col_x0 - 2, nlr_mid_y), "right", ASK_LOOP)
    draw.ellipse(
        [_s(col_x0 - 4), _s(ask_origin_y - 4), _s(col_x0 + 4), _s(ask_origin_y + 4)],
        fill=ASK_LOOP,
    )
    ask_lines: tuple[tuple[str, ImageFont.FreeTypeFont], ...] = (
        ("Ambiguous + interactive", f_caption_bold),
        ("-> returns a question,", f_caption),
        ("caller resubmits", f_caption),
        ("(new request, max 2x)", f_caption),
    )
    ask_caption_mid_y = (ask_origin_y + nlr_mid_y) / 2
    ask_top = ask_caption_mid_y - (len(ask_lines) - 1) / 2 * 19
    for offset, (line, font) in enumerate(ask_lines):
        _text(
            draw,
            (loop_x - 18, ask_top + offset * 19),
            line,
            font=font,
            fill=ASK_LOOP_TEXT,
            anchor="rm",
        )
        labels.append(line)

    # --- legend -----------------------------------------------------------
    leg_x0, leg_y0 = 48, 750
    _box(draw, (leg_x0, leg_y0, leg_x0 + 310, leg_y0 + 210), fill=(10, 20, 44), edge=(46, 76, 128), radius=14)
    _text(draw, (leg_x0 + 155, leg_y0 + 26), "Legend", font=_font("bold", 18), fill=MODULE_TEXT)
    labels.append("Legend")
    legend_rows = (
        (MODULE_FILL, MODULE_EDGE, "DigitalNomadAgent module"),
        (PILL_FILL, PILL_EDGE, "Research tool"),
        (EXTERNAL_FILL, EXTERNAL_EDGE, "External data source"),
    )
    for row, (fill, edge, label) in enumerate(legend_rows):
        y = leg_y0 + 56 + row * 28
        _box(draw, (leg_x0 + 18, y - 9, leg_x0 + 46, y + 9), fill=fill, edge=edge, radius=5, width=1.5)
        _text(draw, (leg_x0 + 58, y), label, font=f_legend, fill=MODULE_TEXT, anchor="lm")
        labels.append(label)
    dashed_legend_rows = (
        (FEEDBACK, "Validator feedback loop (max 1 iteration)"),
        (SHORTCUT, "Out-of-scope shortcut"),
        (ASK_LOOP, "Clarification loop"),
    )
    for row, (colour, label) in enumerate(dashed_legend_rows):
        y = leg_y0 + 56 + (3 + row) * 28
        _dashed(draw, [(leg_x0 + 18, y), (leg_x0 + 46, y)], colour, width=2, dash=7, gap=5)
        _text(draw, (leg_x0 + 58, y), label, font=_font("regular", 12), fill=MODULE_TEXT, anchor="lm")
        labels.append(label)

    image.resize((WIDTH, HEIGHT), Image.LANCZOS).save(PNG_PATH, "PNG", optimize=True)
    return labels


def manifest(labels: list[str]) -> dict:
    """What the diagram actually says, in a form a test can read."""
    drawn = set(labels)
    return {
        "title": TITLE,
        "modules": [name for name in ALL_MODULES if name in drawn],
        "tools": [tool_display_label(name) for name in TOOL_NAMES if tool_display_label(name) in drawn],
        "all_labels": sorted(drawn),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Render to a temporary buffer and report whether the committed manifest is stale.",
    )
    args = parser.parse_args()

    if args.check:
        existing = json.loads(LABELS_PATH.read_text(encoding="utf-8")) if LABELS_PATH.exists() else None
        labels = render()
        if existing != manifest(labels):
            print("assets/model_architecture.labels.json is stale. Re-run without --check.")
            return 1
        print("Diagram labels are up to date.")
        return 0

    labels = render()
    LABELS_PATH.write_text(
        json.dumps(manifest(labels), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {PNG_PATH.relative_to(REPO_ROOT)} ({PNG_PATH.stat().st_size:,} bytes)")
    print(f"Wrote {LABELS_PATH.relative_to(REPO_ROOT)} ({len(set(labels))} distinct labels)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
