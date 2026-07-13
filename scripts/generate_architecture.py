"""Generate assets/model_architecture.png -- a deterministic, Pillow-only
architecture diagram matching the PlaceMatch conceptual architecture.

Run manually: python scripts/generate_architecture.py
Never invoked automatically by tests, startup, or installation (though the
generated PNG is checked in so /api/model_architecture works without running
this first).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.module_names import (  # noqa: E402
    AGENTIC_RESEARCH,
    DYNAMIC_EVALUATION,
    EVIDENCE_MEMORY,
    RECOMMENDATION_GENERATOR,
    RECOMMENDATION_VALIDATOR,
    REQUEST_INTERPRETER,
    TOOL_REGISTRY,
)

OUTPUT_PATH = REPO_ROOT / "assets" / "model_architecture.png"

CANVAS_W, CANVAS_H = 1600, 1250
BG = (255, 255, 255)
MAIN_FILL = (232, 240, 254)
MAIN_BORDER = (47, 91, 215)
EXTERNAL_FILL = (253, 240, 230)
EXTERNAL_BORDER = (196, 110, 30)
TEXT_COLOR = (20, 20, 30)
ARROW_COLOR = (47, 91, 215)
FEEDBACK_COLOR = (196, 30, 30)

CENTER_X = 480
SIDE_X = 1180
MAIN_W = 460
SIDE_W = 380
BOX_H = 80


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


TITLE_FONT = _font(26)
BOX_FONT = _font(20)
LABEL_FONT = _font(16)


class Box:
    def __init__(self, name: str, cx: int, cy: int, w: int = MAIN_W, h: int = BOX_H, external: bool = False):
        self.name = name
        self.cx = cx
        self.cy = cy
        self.w = w
        self.h = h
        self.external = external

    @property
    def left(self) -> int:
        return self.cx - self.w // 2

    @property
    def right(self) -> int:
        return self.cx + self.w // 2

    @property
    def top(self) -> int:
        return self.cy - self.h // 2

    @property
    def bottom(self) -> int:
        return self.cy + self.h // 2


def draw_box(draw: ImageDraw.ImageDraw, box: Box) -> None:
    fill = EXTERNAL_FILL if box.external else MAIN_FILL
    border = EXTERNAL_BORDER if box.external else MAIN_BORDER
    draw.rounded_rectangle(
        [box.left, box.top, box.right, box.bottom], radius=12, fill=fill, outline=border, width=3
    )
    bbox = draw.textbbox((0, 0), box.name, font=BOX_FONT)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        (box.cx - text_w / 2, box.cy - text_h / 2 - bbox[1]),
        box.name,
        fill=TEXT_COLOR,
        font=BOX_FONT,
    )


def draw_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color=ARROW_COLOR,
    width: int = 3,
    dashed: bool = False,
) -> None:
    if dashed:
        _draw_dashed_line(draw, start, end, color, width)
    else:
        draw.line([start, end], fill=color, width=width)

    # Arrowhead pointing toward `end`.
    import math

    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 12
    p1 = (
        end[0] - size * math.cos(angle - math.pi / 6),
        end[1] - size * math.sin(angle - math.pi / 6),
    )
    p2 = (
        end[0] - size * math.cos(angle + math.pi / 6),
        end[1] - size * math.sin(angle + math.pi / 6),
    )
    draw.polygon([end, p1, p2], fill=color)


def _draw_dashed_line(draw, start, end, color, width, dash_len=14, gap_len=10):
    import math

    x1, y1 = start
    x2, y2 = end
    total_len = math.hypot(x2 - x1, y2 - y1)
    if total_len == 0:
        return
    dx, dy = (x2 - x1) / total_len, (y2 - y1) / total_len
    dist = 0.0
    while dist < total_len:
        seg_start = (x1 + dx * dist, y1 + dy * dist)
        seg_end_dist = min(dist + dash_len, total_len)
        seg_end = (x1 + dx * seg_end_dist, y1 + dy * seg_end_dist)
        draw.line([seg_start, seg_end], fill=color, width=width)
        dist += dash_len + gap_len


def build_diagram() -> Image.Image:
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(img)

    draw.text((40, 20), "PlaceMatch - Architecture", fill=TEXT_COLOR, font=TITLE_FONT)

    request_box = Box("Natural-Language Request", CENTER_X, 120)
    interpreter_box = Box(REQUEST_INTERPRETER, CENTER_X, 240)
    research_box = Box(AGENTIC_RESEARCH, CENTER_X, 380, h=100)
    evidence_box = Box(EVIDENCE_MEMORY, CENTER_X, 560)
    evaluation_box = Box(DYNAMIC_EVALUATION, CENTER_X, 680)
    validator_box = Box(RECOMMENDATION_VALIDATOR, CENTER_X, 800)
    generator_box = Box(RECOMMENDATION_GENERATOR, CENTER_X, 920)
    output_box = Box("Ranked Recommendations", CENTER_X, 1040)

    tool_registry_box = Box(TOOL_REGISTRY, SIDE_X, 380, w=SIDE_W)
    external_box = Box("External Data Sources", SIDE_X, 520, w=SIDE_W, external=True)

    all_boxes = [
        request_box,
        interpreter_box,
        research_box,
        evidence_box,
        evaluation_box,
        validator_box,
        generator_box,
        output_box,
        tool_registry_box,
        external_box,
    ]

    # Main vertical chain.
    draw_arrow(draw, (CENTER_X, request_box.bottom), (CENTER_X, interpreter_box.top))
    draw_arrow(draw, (CENTER_X, interpreter_box.bottom), (CENTER_X, research_box.top))
    draw_arrow(draw, (CENTER_X, research_box.bottom), (CENTER_X, evidence_box.top))
    draw_arrow(draw, (CENTER_X, evidence_box.bottom), (CENTER_X, evaluation_box.top))
    draw_arrow(draw, (CENTER_X, evaluation_box.bottom), (CENTER_X, validator_box.top))
    draw_arrow(draw, (CENTER_X, validator_box.bottom), (CENTER_X, generator_box.top))
    draw_arrow(draw, (CENTER_X, generator_box.bottom), (CENTER_X, output_box.top))

    # Agentic Research <-> Tool Registry <-> External Data Sources <-> Evidence Memory.
    draw_arrow(draw, (research_box.right, research_box.cy), (tool_registry_box.left, tool_registry_box.cy))
    draw_arrow(draw, (tool_registry_box.cx, tool_registry_box.bottom), (external_box.cx, external_box.top))
    draw_arrow(
        draw,
        (tool_registry_box.left, tool_registry_box.bottom + 30),
        (evidence_box.right, evidence_box.cy),
    )

    # Feedback loop: Recommendation Validator -> Agentic Research (dashed, labeled).
    loop_x = SIDE_X + SIDE_W // 2 + 60
    draw_arrow(
        draw,
        (validator_box.right, validator_box.cy),
        (loop_x, validator_box.cy),
        color=FEEDBACK_COLOR,
        dashed=True,
    )
    draw_arrow(
        draw,
        (loop_x, validator_box.cy),
        (loop_x, research_box.cy),
        color=FEEDBACK_COLOR,
        dashed=True,
    )
    draw_arrow(
        draw,
        (loop_x, research_box.cy),
        (research_box.right, research_box.cy - 20),
        color=FEEDBACK_COLOR,
        dashed=True,
    )
    draw.text(
        (loop_x + 12, (validator_box.cy + research_box.cy) // 2 - 10),
        "gap research\n(max 1 iteration)",
        fill=FEEDBACK_COLOR,
        font=LABEL_FONT,
    )

    for box in all_boxes:
        draw_box(draw, box)

    # Legend.
    legend_y = CANVAS_H - 70
    draw.rectangle([40, legend_y, 65, legend_y + 20], fill=MAIN_FILL, outline=MAIN_BORDER, width=2)
    draw.text((75, legend_y), "PlaceMatch module", fill=TEXT_COLOR, font=LABEL_FONT)
    draw.rectangle([320, legend_y, 345, legend_y + 20], fill=EXTERNAL_FILL, outline=EXTERNAL_BORDER, width=2)
    draw.text((355, legend_y), "External data source", fill=TEXT_COLOR, font=LABEL_FONT)
    draw.line([600, legend_y + 10, 650, legend_y + 10], fill=FEEDBACK_COLOR, width=3)
    draw.text((660, legend_y), "Validator feedback loop (max 1x)", fill=TEXT_COLOR, font=LABEL_FONT)

    return img


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    img = build_diagram()
    img.save(OUTPUT_PATH, format="PNG")
    print(f"Wrote {OUTPUT_PATH} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()
