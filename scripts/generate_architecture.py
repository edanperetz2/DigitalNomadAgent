"""Generate assets/model_architecture.png -- a deterministic, Pillow-only
architecture diagram matching the PlaceMatch conceptual architecture.

Run manually: python scripts/generate_architecture.py
Never invoked automatically by tests, startup, or installation (though the
generated PNG is checked in so /api/model_architecture works without running
this first).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.module_names import (  # noqa: E402
    AGENTIC_RESEARCH,
    DYNAMIC_EVALUATION,
    EVIDENCE_MEMORY,
    LLM_CALLING_MODULES,
    RECOMMENDATION_GENERATOR,
    RECOMMENDATION_VALIDATOR,
    REQUEST_INTERPRETER,
    TOOL_REGISTRY,
)

OUTPUT_PATH = REPO_ROOT / "assets" / "model_architecture.png"

CANVAS_W, CANVAS_H = 1700, 1350
BG_TOP = (247, 249, 255)
BG_BOTTOM = (225, 232, 250)

DETERMINISTIC_FILL = (225, 236, 255)
DETERMINISTIC_BORDER = (40, 84, 200)
LLM_FILL = (255, 238, 214)
LLM_BORDER = (196, 122, 15)
EXTERNAL_FILL = (253, 240, 230)
EXTERNAL_BORDER = (196, 110, 30)
SHADOW_COLOR = (30, 40, 70, 60)
TEXT_COLOR = (24, 24, 34)
BADGE_TEXT = (255, 255, 255)
ARROW_COLOR = (40, 84, 200)
THIN_ARROW_COLOR = (150, 130, 90)
FEEDBACK_COLOR = (200, 35, 35)

CENTER_X = 520
SIDE_X = 1260
MAIN_W = 480
SIDE_W = 340
BOX_H = 84

ICON_MARGIN = 34


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


TITLE_FONT = _font(30)
SUBTITLE_FONT = _font(17)
BOX_FONT = _font(20)
LABEL_FONT = _font(16)
BADGE_FONT = _font(13)
MINI_FONT = _font(15)


class Box:
    def __init__(
        self,
        name: str,
        cx: int,
        cy: int,
        w: int = MAIN_W,
        h: int = BOX_H,
        kind: str = "deterministic",
        icon: str | None = None,
    ):
        self.name = name
        self.cx = cx
        self.cy = cy
        self.w = w
        self.h = h
        self.kind = kind  # "deterministic" | "llm" | "external"
        self.icon = icon

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


def draw_background(img: Image.Image) -> None:
    top, bottom = BG_TOP, BG_BOTTOM
    for y in range(CANVAS_H):
        t = y / max(CANVAS_H - 1, 1)
        row = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(0, CANVAS_W, 4):
            img.putpixel((x, y), row)
            if x + 1 < CANVAS_W:
                img.putpixel((x + 1, y), row)
            if x + 2 < CANVAS_W:
                img.putpixel((x + 2, y), row)
            if x + 3 < CANVAS_W:
                img.putpixel((x + 3, y), row)


def draw_shadow(draw: ImageDraw.ImageDraw, box: Box, offset: int = 6) -> None:
    draw.rounded_rectangle(
        [box.left + offset, box.top + offset, box.right + offset, box.bottom + offset],
        radius=14,
        fill=(200, 205, 220),
    )


def _icon_bounds(box: Box) -> tuple[int, int, int, int]:
    size = 26
    x0 = box.left + 14
    y0 = box.cy - size // 2
    return x0, y0, x0 + size, y0 + size


def draw_icon(draw: ImageDraw.ImageDraw, box: Box, color) -> None:
    if not box.icon:
        return
    x0, y0, x1, y1 = _icon_bounds(box)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2

    if box.icon == "chat":
        draw.rounded_rectangle([x0, y0, x1, y1 - 6], radius=6, outline=color, width=2)
        draw.polygon([(x0 + 6, y1 - 6), (x0 + 14, y1 - 6), (x0 + 6, y1)], fill=color)
    elif box.icon == "search":
        r = 8
        draw.ellipse([cx - r - 3, y0, cx + r - 3, y0 + 2 * r], outline=color, width=2)
        draw.line([cx + r - 5, y0 + 2 * r - 2, x1, y1], fill=color, width=3)
    elif box.icon == "gear":
        r = 10
        for i in range(8):
            angle = math.pi / 4 * i
            tx = cx + r * math.cos(angle)
            ty = cy + r * math.sin(angle)
            draw.ellipse([tx - 2, ty - 2, tx + 2, ty + 2], fill=color)
        draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=color, width=2)
    elif box.icon == "database":
        draw.ellipse([x0, y0, x1, y0 + 8], outline=color, width=2)
        draw.line([x0, y0 + 4, x0, y1 - 4], fill=color, width=2)
        draw.line([x1, y0 + 4, x1, y1 - 4], fill=color, width=2)
        draw.arc([x0, y1 - 8, x1, y1], start=0, end=180, fill=color, width=2)
    elif box.icon == "chart":
        base = y1
        draw.line([x0, base, x1, base], fill=color, width=2)
        draw.rectangle([x0 + 2, cy - 2, x0 + 7, base], fill=color)
        draw.rectangle([x0 + 10, cy - 8, x0 + 15, base], fill=color)
        draw.rectangle([x0 + 18, cy + 3, x0 + 23, base], fill=color)
    elif box.icon == "shield":
        draw.polygon(
            [(cx, y0), (x1, y0 + 6), (x1, cy + 4), (cx, y1), (x0, cy + 4), (x0, y0 + 6)],
            outline=color,
            width=2,
        )
        draw.line([cx - 5, cy, cx - 1, cy + 5], fill=color, width=2)
        draw.line([cx - 1, cy + 5, cx + 6, cy - 4], fill=color, width=2)
    elif box.icon == "doc":
        draw.rounded_rectangle([x0, y0, x1, y1], radius=3, outline=color, width=2)
        for ly in (y0 + 7, y0 + 13, y0 + 19):
            draw.line([x0 + 4, ly, x1 - 4, ly], fill=color, width=1)


def draw_box(draw: ImageDraw.ImageDraw, box: Box) -> None:
    fill, border = {
        "deterministic": (DETERMINISTIC_FILL, DETERMINISTIC_BORDER),
        "llm": (LLM_FILL, LLM_BORDER),
        "external": (EXTERNAL_FILL, EXTERNAL_BORDER),
    }[box.kind]
    draw.rounded_rectangle(
        [box.left, box.top, box.right, box.bottom], radius=14, fill=fill, outline=border, width=3
    )
    draw_icon(draw, box, border)

    if "\n" in box.name:
        return  # multi-line labels (source cluster) are drawn separately with MINI_FONT

    text_left_pad = ICON_MARGIN if box.icon else 0
    available_w = box.w - text_left_pad - 12
    bbox = draw.textbbox((0, 0), box.name, font=BOX_FONT)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    text_x = box.left + text_left_pad + max(0, (available_w - text_w) / 2)
    draw.text(
        (text_x, box.cy - text_h / 2 - bbox[1]),
        box.name,
        fill=TEXT_COLOR,
        font=BOX_FONT,
    )

    if box.kind == "llm":
        badge_w, badge_h = 34, 18
        bx0 = box.right - badge_w - 6
        by0 = box.top - badge_h // 2
        draw.rounded_rectangle(
            [bx0, by0, bx0 + badge_w, by0 + badge_h], radius=9, fill=LLM_BORDER
        )
        draw.text((bx0 + 6, by0 + 2), "LLM", fill=BADGE_TEXT, font=BADGE_FONT)


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


def draw_routed_arrow(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[int, int]],
    color=ARROW_COLOR,
    width: int = 3,
) -> None:
    for a, b in zip(points[:-1], points[1:], strict=True):
        draw.line([a, b], fill=color, width=width)
    start, end = points[-2], points[-1]
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 12
    p1 = (end[0] - size * math.cos(angle - math.pi / 6), end[1] - size * math.sin(angle - math.pi / 6))
    p2 = (end[0] - size * math.cos(angle + math.pi / 6), end[1] - size * math.sin(angle + math.pi / 6))
    draw.polygon([end, p1, p2], fill=color)


def _draw_dashed_line(draw, start, end, color, width, dash_len=14, gap_len=10):
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
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_TOP)
    draw_background(img)
    draw = ImageDraw.Draw(img)

    draw.text((44, 22), "PlaceMatch - Architecture", fill=TEXT_COLOR, font=TITLE_FONT)
    draw.text(
        (44, 58),
        "Autonomous Evidence-Based Place Recommendation Agent",
        fill=(90, 90, 105),
        font=SUBTITLE_FONT,
    )

    def kind_of(name: str) -> str:
        return "llm" if name in LLM_CALLING_MODULES else "deterministic"

    request_box = Box("Natural-Language Request", CENTER_X, 140, kind="deterministic", icon="chat")
    interpreter_box = Box(REQUEST_INTERPRETER, CENTER_X, 262, kind=kind_of(REQUEST_INTERPRETER), icon="chat")
    research_box = Box(AGENTIC_RESEARCH, CENTER_X, 400, h=104, kind=kind_of(AGENTIC_RESEARCH), icon="search")
    evidence_box = Box(EVIDENCE_MEMORY, CENTER_X, 588, kind=kind_of(EVIDENCE_MEMORY), icon="database")
    evaluation_box = Box(DYNAMIC_EVALUATION, CENTER_X, 710, kind=kind_of(DYNAMIC_EVALUATION), icon="chart")
    validator_box = Box(
        RECOMMENDATION_VALIDATOR, CENTER_X, 832, kind=kind_of(RECOMMENDATION_VALIDATOR), icon="shield"
    )
    generator_box = Box(
        RECOMMENDATION_GENERATOR, CENTER_X, 954, kind=kind_of(RECOMMENDATION_GENERATOR), icon="doc"
    )
    output_box = Box("Ranked Recommendations", CENTER_X, 1076, kind="deterministic", icon="doc")

    tool_registry_box = Box(TOOL_REGISTRY, SIDE_X, 400, w=SIDE_W, kind=kind_of(TOOL_REGISTRY), icon="gear")

    source_names = [
        "Nominatim\n(geocoding)",
        "Open-Meteo\n(weather)",
        "Overpass\n(amenities)",
        "Wikivoyage /\nofficial sources",
    ]
    source_boxes = []
    cluster_top = 530
    cluster_gap = 16
    mini_w = (SIDE_W - cluster_gap) // 2
    mini_h = 66
    for i, sname in enumerate(source_names):
        row, col = divmod(i, 2)
        sx = SIDE_X - SIDE_W // 2 + mini_w // 2 + col * (mini_w + cluster_gap)
        sy = cluster_top + row * (mini_h + cluster_gap) + mini_h // 2
        source_boxes.append(Box(sname, sx, sy, w=mini_w, h=mini_h, kind="external"))

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
        *source_boxes,
    ]

    for box in all_boxes:
        draw_shadow(draw, box)

    main_chain = [
        (request_box, interpreter_box),
        (interpreter_box, research_box),
        (research_box, evidence_box),
        (evidence_box, evaluation_box),
        (evaluation_box, validator_box),
        (validator_box, generator_box),
        (generator_box, output_box),
    ]
    for a, b in main_chain:
        draw_arrow(draw, (CENTER_X, a.bottom), (CENTER_X, b.top), width=4)

    draw_arrow(draw, (research_box.right, research_box.cy), (tool_registry_box.left, tool_registry_box.cy))

    for sbox in source_boxes:
        draw_routed_arrow(
            draw,
            [(tool_registry_box.cx, tool_registry_box.bottom + 8), (sbox.cx, sbox.top)],
            color=THIN_ARROW_COLOR,
            width=2,
        )

    draw_routed_arrow(
        draw,
        [
            (tool_registry_box.left, tool_registry_box.bottom + 40),
            (evidence_box.right + 60, evidence_box.cy - 20),
            (evidence_box.right, evidence_box.cy),
        ],
        color=ARROW_COLOR,
        width=2,
    )

    loop_x = SIDE_X + SIDE_W // 2 + 70
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

    for box in source_boxes:
        lines = box.name.split("\n")
        total_h = len(lines) * 18
        y = box.cy - total_h / 2
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=MINI_FONT)
            w = bbox[2] - bbox[0]
            draw.text((box.cx - w / 2, y), line, fill=TEXT_COLOR, font=MINI_FONT)
            y += 18

    legend_y = CANVAS_H - 70
    draw.rounded_rectangle(
        [40, legend_y, 65, legend_y + 20], radius=4, fill=DETERMINISTIC_FILL, outline=DETERMINISTIC_BORDER, width=2
    )
    draw.text((75, legend_y), "Deterministic module", fill=TEXT_COLOR, font=LABEL_FONT)
    draw.rounded_rectangle(
        [330, legend_y, 355, legend_y + 20], radius=4, fill=LLM_FILL, outline=LLM_BORDER, width=2
    )
    draw.text((365, legend_y), "LLM-calling module", fill=TEXT_COLOR, font=LABEL_FONT)
    draw.rounded_rectangle(
        [590, legend_y, 615, legend_y + 20], radius=4, fill=EXTERNAL_FILL, outline=EXTERNAL_BORDER, width=2
    )
    draw.text((625, legend_y), "External data source", fill=TEXT_COLOR, font=LABEL_FONT)
    draw.line([900, legend_y + 10, 950, legend_y + 10], fill=FEEDBACK_COLOR, width=3)
    draw.text((960, legend_y), "Validator feedback loop (max 1x)", fill=TEXT_COLOR, font=LABEL_FONT)

    return img


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    img = build_diagram()
    img.save(OUTPUT_PATH, format="PNG")
    print(f"Wrote {OUTPUT_PATH} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()
