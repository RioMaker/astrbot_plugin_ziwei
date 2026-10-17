"""One vertical candidate-sheet PNG, composed from the complete chart renderer."""

import io
import math
from copy import deepcopy
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from .rules import BRANCHES
from .themes import get_theme, normalize_theme

MAX_CANDIDATES = 32
MAX_PIXELS = 80_000_000
MAX_CELL_WIDTH = 1664
MIN_CELL_WIDTH = 1200
_GAP = 24
_MARGIN = 8


def _validate_candidates(candidates):
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError("定盘总图须包含1—32个完整候选")
    ids = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("定盘总图候选格式无效")
        cid = candidate.get("id")
        if not isinstance(cid, str) or not cid or len(cid) > 64 or cid in ids:
            raise ValueError("定盘总图候选ID须完整且唯一")
        ids.add(cid)
        if not isinstance(candidate.get("chart"), dict):
            raise ValueError("定盘总图缺少候选完整命盘")
        intervals = candidate.get("intervals")
        if not isinstance(intervals, list) or not 1 <= len(intervals) <= 32:
            raise ValueError("定盘总图缺少完整候选时间范围")
        for interval in intervals:
            try:
                start = datetime.fromisoformat(interval["start"])
                end = datetime.fromisoformat(interval["end"])
                if start.tzinfo or end.tzinfo or not start < end:
                    raise ValueError("invalid_interval")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("定盘总图候选钟表范围无效") from exc
        try:
            normalized = candidate["chart"]["normalized"]
            representative = normalized["civil"]
            datetime.fromisoformat(representative)
            branch = normalized["hour_branch"]
            if type(branch) is not int or not 1 <= branch <= 12:
                raise ValueError("invalid_branch")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("定盘总图缺少候选代表时刻或计算时辰") from exc


def _range_text(interval):
    start = datetime.fromisoformat(interval["start"])
    end = datetime.fromisoformat(interval["end"])
    ending = end.strftime("%H:%M:%S")
    if end.date() != start.date():
        ending = end.strftime("%Y-%m-%d %H:%M:%S")
    return f"钟表 {start:%Y-%m-%d %H:%M:%S} 至 {ending}（不含终点）"


def _wrap(text, font, width):
    lines, line = [], ""
    for character in text:
        trial = line + character
        if line and font.getlength(trial) > width:
            lines.append(line)
            line = character
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def _layout(candidates, image_sizes, font_path, cell_width):
    columns, rows = 1, len(candidates)
    ratio = cell_width / MAX_CELL_WIDTH
    sizes = {"title": round(42 * ratio), "body": round(28 * ratio)}
    fonts = {key: ImageFont.truetype(font_path, size) for key, size in sizes.items()}
    inset = max(14, round(20 * ratio))
    line_gap = max(6, round(9 * ratio))
    line_heights = {
        key: font.getbbox("出生时辰 0123456789", anchor="lt")[3] + line_gap
        for key, font in fonts.items()
    }
    width = 2 * _MARGIN + columns * cell_width + (columns - 1) * _GAP
    title_height = line_heights["title"] + line_heights["body"] + 3 * inset
    cells = []
    for index, (candidate, source) in enumerate(
        zip(candidates, image_sizes, strict=True)
    ):
        branch = BRANCHES[candidate["chart"]["normalized"]["hour_branch"] - 1]
        text_rows = [("title", f"候选 {candidate['id']} · 计算{branch}时")]
        text_rows += [("body", _range_text(item)) for item in candidate["intervals"]]
        representative = candidate["chart"]["normalized"]["civil"]
        text_rows.append(("body", f"代表时刻 {representative} · 非确定出生时间"))
        texts = []
        y = inset
        for role, text in text_rows:
            for line in _wrap(text, fonts[role], cell_width - 2 * inset):
                texts.append({"text": line, "x": inset, "y": y, "role": role})
                y += line_heights[role]
        scale = min(1.0, cell_width / source[0])
        size = (round(source[0] * scale), round(source[1] * scale))
        cells.append(
            {
                "candidate_id": candidate["id"],
                "intervals": deepcopy(candidate["intervals"]),
                "source_size": list(source),
                "image_size": list(size),
                "row": index // columns,
                "column": index % columns,
                "header_height": y + inset,
                "texts": texts,
            }
        )
    y = title_height
    for cell in cells:
        header_height = cell["header_height"]
        image_height = cell["image_size"][1]
        row_height = header_height + image_height
        x = _MARGIN
        image_x = x + (cell_width - cell["image_size"][0]) // 2
        cell["box"] = [x, y, x + cell_width, y + row_height]
        cell["header_box"] = [x, y, x + cell_width, y + header_height]
        cell["image_box"] = [
            image_x,
            y + header_height,
            image_x + cell["image_size"][0],
            y + header_height + cell["image_size"][1],
        ]
        for text in cell["texts"]:
            text["x"] += x
            text["y"] += y
            bounds = fonts[text["role"]].getbbox(text["text"], anchor="lt")
            text["box"] = [
                text["x"] + bounds[0],
                text["y"] + bounds[1],
                text["x"] + bounds[2],
                text["y"] + bounds[3],
            ]
        y += row_height + _GAP
    return {
        "size": [width, y - _GAP + _MARGIN],
        "columns": columns,
        "rows": rows,
        "cell_width": cell_width,
        "font_sizes": sizes,
        "title_height": title_height,
        "inset": inset,
        "line_heights": line_heights,
        "cells": cells,
        "empty_cells": [],
    }


def plan_candidate_collage(candidates, image_sizes, font_path):
    """Audit geometry from actual rendered sizes, without allocating a canvas."""
    _validate_candidates(candidates)
    if len(image_sizes) != len(candidates) or any(
        len(size) != 2 or any(type(value) is not int or value <= 0 for value in size)
        for size in image_sizes
    ):
        raise ValueError("定盘总图须提供每张完整原图的实际尺寸")
    width = min(MAX_CELL_WIDTH, max(MIN_CELL_WIDTH, max(s[0] for s in image_sizes)))
    while True:
        plan = _layout(candidates, image_sizes, font_path, width)
        pixels = math.prod(plan["size"])
        if pixels <= MAX_PIXELS:
            plan["pixels"] = pixels
            return plan
        if width == MIN_CELL_WIDTH:
            raise ValueError("完整定盘总图超过8000万像素，请减少候选范围后重试")
        width = max(
            MIN_CELL_WIDTH,
            min(width - 1, int(width * math.sqrt(MAX_PIXELS / pixels)) - 4),
        )


def render_candidate_collage(candidates, renderer, theme="day") -> bytes:
    """Stack complete charts vertically with high-contrast title separators.

    Keep native resolution where the total fits 80 megapixels. Larger batches
    scale every complete chart proportionally, never below 1200 pixels wide for
    standard charts. Only compressed source PNGs and one decoded tile accompany
    the canvas; no list of 32 decoded chart images is retained.
    """
    _validate_candidates(candidates)
    theme = normalize_theme(theme)
    palette = get_theme(theme)
    sources, image_sizes = [], []
    for candidate in candidates:
        png = renderer.render(candidate["chart"], theme)
        try:
            with Image.open(io.BytesIO(png)) as tile:
                if tile.format != "PNG":
                    raise ValueError("not_png")
                image_sizes.append(tile.size)
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("候选原盘图片无效，未生成不完整总图") from exc
        sources.append(png)
    plan = plan_candidate_collage(candidates, image_sizes, renderer.font_path)
    fonts = {
        role: ImageFont.truetype(renderer.font_path, size)
        for role, size in plan["font_sizes"].items()
    }
    with Image.new("RGB", tuple(plan["size"]), palette.bg) as canvas:
        draw = ImageDraw.Draw(canvas)
        x, y = _MARGIN + plan["inset"], plan["inset"]
        draw.text(
            (x, y), "定盘候选总图", font=fonts["title"], fill=palette.ink, anchor="lt"
        )
        draw.text(
            (x, y + plan["line_heights"]["title"] + plan["inset"]),
            f"共 {len(candidates)} 个候选 · 钟表范围完整保留 · 所示时刻仅为范围代表",
            font=fonts["body"],
            fill=palette.muted,
            anchor="lt",
        )
        for cell, png in zip(plan["cells"], sources, strict=True):
            draw.rectangle(tuple(cell["header_box"]), fill=palette.accent)
            for text in cell["texts"]:
                draw.text(
                    (text["x"], text["y"]),
                    text["text"],
                    font=fonts[text["role"]],
                    fill=palette.on_accent,
                    anchor="lt",
                )
            with Image.open(io.BytesIO(png)) as source:
                source.load()
                target_size = tuple(cell["image_size"])
                if source.mode == "RGB" and source.size == target_size:
                    canvas.paste(source, tuple(cell["image_box"][:2]))
                else:
                    with source.convert("RGB") as rgb:
                        if rgb.size == target_size:
                            canvas.paste(rgb, tuple(cell["image_box"][:2]))
                        else:
                            with rgb.resize(
                                target_size, Image.Resampling.LANCZOS
                            ) as resized:
                                canvas.paste(resized, tuple(cell["image_box"][:2]))
        output = io.BytesIO()
        canvas.save(output, format="PNG", compress_level=6)
        return output.getvalue()
