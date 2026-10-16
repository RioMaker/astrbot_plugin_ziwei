"""Portable, request-local rendering of the life-atlas structural candidates."""

from __future__ import annotations

import io
import random
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont

from .renderer import find_font
from .rules import BRANCHES, STEMS
from .themes import normalize_theme

WIDTH = 2400
LEFT, RIGHT = 270, 2260
LANES = (
    ("study", "学习评估"),
    ("career", "工作职责"),
    ("relationship", "关系相处"),
    ("mobility", "外出迁移"),
    ("home", "居所家庭"),
)


def _palette(theme):
    # Every call owns its colors. No renderer-level/global theme state is mutated.
    if normalize_theme(theme) == "night":
        return {
            "top": "#0d1c32",
            "bottom": "#080f1d",
            "panel": "#0e1c2e",
            "ink": "#e4edf6",
            "muted": "#a0b2c8",
            "faint": "#7389a3",
            "accent": "#8dcee2",
            "line": "#263a53",
            "grid": "#1b2d43",
            "stage": "#15273d",
            "stage_alt": "#122237",
            "last": "#18354a",
            "decade": "#9fc5ad",
            "annual": "#93bade",
            "mixed": "#c4a88d",
            "node_ink": "#0d1c2e",
            "lanes": ("#dec18a", "#76cbd9", "#cda3ce", "#8eb8ef", "#a0c9ae"),
        }
    return {
        "top": "#f6f3ec",
        "bottom": "#eee9df",
        "panel": "#fcfaf5",
        "ink": "#2b3745",
        "muted": "#5e6974",
        "faint": "#747c82",
        "accent": "#296b83",
        "line": "#d1d4d1",
        "grid": "#e5e5de",
        "stage": "#e8edf0",
        "stage_alt": "#f0eee7",
        "last": "#dce9e9",
        "decade": "#477956",
        "annual": "#3e6f98",
        "mixed": "#8f6846",
        "node_ink": "#ffffff",
        "lanes": ("#967139", "#287e8d", "#97638e", "#4677ab", "#538365"),
    }


def _rgb(color):
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def _mix(first, second, amount):
    return tuple(
        round(a * (1 - amount) + b * amount)
        for a, b in zip(_rgb(first), _rgb(second), strict=True)
    )


def _range(first, last):
    return str(first) if first == last else f"{first}—{last}"


class _Plan:
    """Measure once, then draw the same operations and expose their audit boxes."""

    def __init__(self, font_path, theme):
        self.font_path = font_path
        self.colors = _palette(theme)
        self.fonts = {}
        self.ops = []
        self.boxes = []
        self.nodes = []
        self.window_ids = []
        self.height = 0

    def font(self, size):
        if size not in self.fonts:
            self.fonts[size] = ImageFont.truetype(self.font_path, size)
        return self.fonts[size]

    def wrap(self, value, width, size):
        lines = []
        for paragraph in str(value).splitlines():
            current = ""
            for char in paragraph:
                if current and self.font(size).getlength(current + char) > width:
                    lines.append(current)
                    current = ""
                current += char
            lines.append(current)
        return lines or [""]

    def text(self, x, y, value, size=26, color=None, anchor="lt"):
        value = str(value)
        if not value:
            return
        font = self.font(size)
        box = font.getbbox(value, anchor=anchor)
        self.boxes.append(
            {"text": value, "box": [x + box[0], y + box[1], x + box[2], y + box[3]]}
        )
        self.ops.append(
            (
                "text",
                ((x, y), value),
                {"font": font, "fill": color or self.colors["ink"], "anchor": anchor},
            )
        )

    def shape(self, kind, box, **kwargs):
        self.ops.append((kind, (box,), kwargs))

    def line(self, x1, y1, x2, y2, color=None, width=1):
        self.shape(
            "line", (x1, y1, x2, y2), fill=color or self.colors["line"], width=width
        )

    def audit(self):
        collisions = []
        ordered = sorted(self.boxes, key=lambda item: item["box"][1])
        for index, first in enumerate(ordered):
            x1, y1, x2, y2 = first["box"]
            for second in ordered[index + 1 :]:
                a, b, c, d = second["box"]
                if b >= y2:
                    break
                if min(x2, c) > max(x1, a) and min(y2, d) > max(y1, b):
                    collisions.append([first["text"], second["text"]])
        outside = [
            item
            for item in self.boxes
            if not (
                0 <= item["box"][0] < item["box"][2] <= WIDTH
                and 0 <= item["box"][1] < item["box"][3] <= self.height
            )
        ]
        return {
            "size": [WIDTH, self.height],
            "text_boxes": self.boxes,
            "text_collisions": collisions,
            "out_of_bounds": outside,
            "node_boxes": self.nodes,
            "rendered_window_ids": self.window_ids,
        }

    def png(self):
        image = Image.new("RGB", (WIDTH, self.height))
        draw = ImageDraw.Draw(image)
        for y in range(self.height):
            draw.line(
                (0, y, WIDTH, y),
                fill=_mix(self.colors["top"], self.colors["bottom"], y / self.height),
            )
        for kind, args, kwargs in self.ops:
            getattr(draw, kind)(*args, **kwargs)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True, dpi=(144, 144))
        return output.getvalue()


def _path_rows(window):
    """Keep every distinct hit/path, including its source palace and age."""
    grouped = {}
    backgrounds = {}
    for hit in window["hits"]:
        target = (
            "大"
            + hit["target_decade_palace"]
            + "·"
            + BRANCHES[hit["target_branch"] - 1]
        )
        for key, label, role in (
            ("decade_paths", "大命", "decade"),
            ("annual_paths", "流命", "annual"),
        ):
            paths = defaultdict(list)
            for path in hit[key]:
                source = label + "·" + STEMS[path["source_stem"] - 1]
                source += BRANCHES[path["source_branch"] - 1]
                paths[source].append(path["star"] + path["hua"])
            for source, stars in paths.items():
                row_key = (role, source, "、".join(stars) + " → " + target)
                grouped.setdefault(row_key, []).append(hit["age"])
        stars = (
            "、".join(
                s["name"] + ("(副)" if s.get("secondary") else "")
                for s in hit["natal_major_stars"]
            )
            or "无十四主星"
        )
        background = f"本命 {hit['target_natal_palace']} · {stars}"
        backgrounds.setdefault(background, []).append(hit["age"])
    rows = []
    for (role, source, value), ages in grouped.items():
        suffix = ""
        if len(window["hits"]) > 1:
            suffix = " [" + "/".join(map(str, dict.fromkeys(ages))) + "岁]"
        rows.append((role, source, value + suffix))
    for value, ages in backgrounds.items():
        if len(backgrounds) > 1:
            value += " [" + "/".join(map(str, dict.fromkeys(ages))) + "岁]"
        rows.append(("faint", "", value))
    return rows


def _header(plan, atlas, demo):
    c = plan.colors
    rng = random.Random(20261016)
    for _ in range(95):
        x, y = rng.randrange(1100, WIDTH), rng.randrange(0, 220)
        radius = rng.choice((1, 1, 1, 2))
        plan.shape(
            "ellipse",
            (x - radius, y - radius, x + radius, y + radius),
            fill=_mix(c["top"], c["accent"], 0.22),
        )
    for radius in (210, 292, 390):
        plan.shape(
            "arc",
            (2130 - radius * 1.7, 55 - radius, 2130 + radius * 1.7, 55 + radius),
            start=6,
            end=173,
            fill=c["line"],
            width=2,
        )
    plan.line(74, 51, 122, 51, c["accent"], 3)
    plan.text(139, 37, "紫微斗数  /  人生阶段总览", 23, c["muted"])
    plan.text(74, 94, "人生变动图谱", 62)
    subtitle = "重大变动候选 · 虚构资料预览" if demo else "重大变动候选 · 结构筛选"
    plan.text(77, 183, subtitle, 27, c["muted"])
    plan.text(
        2324,
        66,
        f"{atlas['start_age']:02d} — {atlas['end_age']:02d}",
        62,
        c["accent"],
        "rt",
    )
    plan.text(
        2324,
        151,
        f"虚岁  ·  农历 {atlas['start_year']}—{atlas['end_year']} 年",
        26,
        c["muted"],
        "rt",
    )
    plan.line(74, 236, 2326, 236)
    natal = atlas["natal"]
    normalized = natal["normalized"]
    birth = normalized["original"]
    timing = "真太阳时" if normalized["true_solar"] else "钟表时间"
    metadata = (
        f"{natal['sex']}  /  {normalized['civil']}  /  {natal['bureau_name']}"
        f"  /  UTC{birth['timezone']:+g} · 经度{birth['longitude']:g}° · {timing}"
    )
    lines = plan.wrap(metadata, 1570, 25)
    for index, value in enumerate(lines):
        plan.text(77, 260 + index * 34, value, 25, c["muted"])
    endpoint = atlas["endpoint_decade"]
    plan.text(
        2324,
        260,
        "60岁所在大运：" + _range(endpoint["age_start"], endpoint["age_end"]) + "岁",
        25,
        c["accent"],
        "rt",
    )
    return 316 + (len(lines) - 1) * 34


def _timeline(plan, atlas, top):
    c = plan.colors
    first_age, last_age = atlas["start_age"], atlas["end_age"]
    cell = (RIGHT - LEFT) / (last_age - first_age + 1)

    def start(age):
        return LEFT + (age - first_age) * cell

    def end(age):
        return start(age) + cell

    windows = atlas["windows"]
    labels = dict(LANES)
    unknown = {window["topic"] for window in windows} - labels.keys()
    if unknown:
        raise ValueError("图谱含未知主题")
    placements = []
    lane_rows = []
    y = top + 264
    for topic, label in LANES:
        row_ends = []
        lane_items = sorted(
            (w for w in windows if w["topic"] == topic),
            key=lambda w: (w["start_age"], w["end_age"], w["id"]),
        )
        for window in lane_items:
            if not first_age <= window["start_age"] <= window["end_age"] <= last_age:
                raise ValueError("图谱窗口超出年龄范围")
            x1, x2 = start(window["start_age"]), end(window["end_age"])
            x = (x1 + x2) / 2
            age_label = _range(window["start_age"], window["end_age"]) + "岁"
            note = ""
            if window["mixed"]:
                hua = {
                    p["hua"]
                    for h in window["hits"]
                    for p in h["decade_paths"] + h["annual_paths"]
                }
                note = "".join(h for h in "禄权科忌" if h in hua) + "并见"
            half = max(
                30,
                plan.font(25).getlength(age_label) / 2,
                plan.font(20).getlength(note) / 2,
                plan.font(23).getlength(str(window["id"])) / 2 + 9,
            )
            bound_left, bound_right = min(x1 - 3, x - half), max(x2 + 3, x + half)
            row = next(
                (
                    i
                    for i, previous in enumerate(row_ends)
                    if previous + 20 < bound_left
                ),
                len(row_ends),
            )
            if row == len(row_ends):
                row_ends.append(bound_right)
            else:
                row_ends[row] = bound_right
            placements.append((window, x1, x2, y + row * 112, age_label, note, half))
        rows = max(1, len(row_ends))
        lane_rows.append((topic, label, y, rows))
        y += rows * 112
    bottom = y - 40
    plan.shape(
        "rounded_rectangle",
        (64, top, 2336, bottom),
        radius=20,
        fill=c["panel"],
        outline=c["line"],
        width=2,
    )
    plan.text(93, top + 34, "大运阶段", 26, c["muted"])
    for decade in atlas["decades"]:
        age1 = max(first_age, decade["age_start"])
        age2 = min(last_age, decade["age_end"])
        if age1 > age2:
            continue
        x1, x2 = start(age1), end(age2)
        last = decade["index"] == atlas["endpoint_decade"]["index"]
        fill = c["last"] if last else c["stage" if decade["index"] % 2 else "stage_alt"]
        plan.shape("rectangle", (x1 + 2, top + 22, x2 - 2, top + 93), fill=fill)
        title = "童限" if decade["index"] == 0 else f"第{decade['index']}大运"
        plan.text(
            (x1 + x2) / 2, top + 31, title, 24, c["accent"] if last else c["ink"], "mt"
        )
        plan.text(
            (x1 + x2) / 2, top + 63, _range(age1, age2) + "岁", 21, c["muted"], "mt"
        )
        if last:
            plan.line(x1 + 2, top + 92, x2 - 2, top + 92, c["accent"], 2)
    plan.text(93, top + 120, "虚岁", 24, c["muted"])
    plan.text(93, top + 156, "农历年", 23, c["faint"])
    ticks = sorted({first_age, last_age, *range(10, last_age + 1, 10)})
    positions = {}
    next_left = RIGHT + 40
    for age in reversed(ticks):
        age_text = "出生·1" if age == 1 else str(age)
        year = atlas["start_year"] + age - first_age
        half = (
            max(plan.font(25).getlength(age_text), plan.font(22).getlength(str(year)))
            / 2
        )
        natural = start(age) + cell / 2
        x = min(natural, next_left - half - 16)
        positions[age] = x
        next_left = x - half
    for age in range(first_age, last_age + 1):
        x = start(age) + cell / 2
        plan.line(x, top + 194, x, top + 208 if age % 5 == 0 else top + 202, c["faint"])
    for age in ticks:
        x, label_x = start(age) + cell / 2, positions[age]
        plan.text(
            label_x,
            top + 117,
            "出生·1" if age == 1 else age,
            25,
            c["accent"] if age == 60 else c["ink"],
            "mt",
        )
        plan.text(
            label_x,
            top + 154,
            atlas["start_year"] + age - first_age,
            22,
            c["faint"],
            "mt",
        )
        if abs(label_x - x) > 1:
            plan.line(label_x, top + 183, x, top + 193, c["faint"])
        if age == 60:
            for dy in range(top + 214, bottom - 12, 14):
                plan.line(x, dy, x, min(dy + 5, bottom - 12), c["accent"])
        else:
            plan.line(x, top + 214, x, bottom - 12, c["grid"])
    colors = dict(zip((key for key, _ in LANES), c["lanes"], strict=True))
    for topic, label, lane_y, rows in lane_rows:
        color = colors[topic]
        plan.line(93, lane_y - 13, 93, lane_y + 14, color, 4)
        plan.text(113, lane_y - 15, label, 29, color)
        for row in range(rows):
            line_y = lane_y + row * 112
            plan.line(LEFT, line_y, RIGHT, line_y, c["line"], 2)
    for window, x1, x2, y, age_label, note, half in placements:
        color = colors[window["topic"]]
        x = (x1 + x2) / 2
        node_half = max(22, plan.font(23).getlength(str(window["id"])) / 2 + 9)
        if window["start_age"] == window["end_age"]:
            plan.shape(
                "ellipse",
                (x - node_half - 7, y - 29, x + node_half + 7, y + 29),
                fill=_mix(c["panel"], color, 0.13),
            )
            plan.shape(
                "ellipse", (x - node_half, y - 22, x + node_half, y + 22), fill=color
            )
        else:
            a, b = min(x1 + 3, x - node_half), max(x2 - 3, x + node_half)
            plan.shape(
                "rounded_rectangle",
                (a - 6, y - 28, b + 6, y + 28),
                radius=24,
                fill=_mix(c["panel"], color, 0.13),
            )
            plan.shape(
                "rounded_rectangle", (a, y - 21, b, y + 21), radius=20, fill=color
            )
        plan.text(x, y, window["id"], 23, c["node_ink"], "mm")
        plan.text(x, y - 53, age_label, 25, anchor="mt")
        plan.text(x, y + 31, note, 20, c["mixed"], "mt")
        plan.window_ids.append(window["id"])
        plan.nodes.append(
            {
                "id": window["id"],
                "topic": window["topic"],
                "box": [
                    min(x1 - 6, x - half - 7),
                    y - 53,
                    max(x2 + 6, x + half + 7),
                    y + 58,
                ],
            }
        )
    if not windows:
        plan.text(
            2324, bottom + 25, "本次结构筛选未形成候选窗口", 24, c["accent"], "rt"
        )
    return bottom, colors


def _details(plan, atlas, top, colors):
    c = plan.colors
    plan.text(
        77,
        top + 25,
        "圆点为单年，横条为连续年份；编号对应下方依据。空白不表示无事发生。",
        24,
        c["muted"],
    )
    plan.line(74, top + 80, 2326, top + 80)
    plan.text(76, top + 110, "窗口依据", 37)
    plan.text(
        2324,
        top + 116,
        f"{len(atlas['windows'])} 个候选窗口 · 按时间排序",
        25,
        c["muted"],
        "rt",
    )
    plan.text(
        78,
        top + 161,
        "大命＝大运命宫  /  流命＝流年命宫  /  均取本命固定宫干飞化",
        23,
        c["muted"],
    )
    width, gap, y = 728, 34, top + 220
    windows = sorted(
        atlas["windows"], key=lambda w: (w["start_age"], w["end_age"], w["id"])
    )
    for index in range(0, len(windows), 3):
        row_bottom = y
        for column, window in enumerate(windows[index : index + 3]):
            x = 74 + column * (width + gap)
            color = colors[window["topic"]]
            badge = max(43, plan.font(22).getlength(str(window["id"])) + 18)
            plan.shape(
                "rounded_rectangle",
                (x, y, x + badge, y + 34),
                radius=7,
                fill=_mix(c["panel"], color, 0.17),
            )
            plan.text(x + badge / 2, y + 17, window["id"], 22, color, "mm")
            plan.text(
                x + badge + 14,
                y,
                _range(window["start_age"], window["end_age"])
                + "岁 · "
                + dict(LANES)[window["topic"]],
                28,
            )
            plan.text(
                x + width,
                y + 5,
                _range(window["start_year"], window["end_year"]),
                22,
                c["faint"],
                "rt",
            )
            content_y = y + 47
            for role, source, value in _path_rows(window):
                inset = 145 if source else 0
                plan.text(x, content_y, source, 23, c[role])
                for line in plan.wrap(value, width - inset, 23):
                    plan.text(
                        x + inset,
                        content_y,
                        line,
                        23,
                        c["faint"] if role == "faint" else c["ink"],
                    )
                    content_y += 33
            plan.line(x, content_y + 6, x + width, content_y + 6)
            row_bottom = max(row_bottom, content_y + 25)
        y = row_bottom
    if not windows:
        plan.text(78, y, "未形成候选窗口；仍完整展示童限与各段大运。", 26, c["muted"])
        y += 65
    return y + 10


def _footer(plan, atlas, y, demo):
    c = plan.colors
    plan.line(74, y, 2326, y)
    footnotes = [
        "P1 结构筛选：两路飞化共同落宫，尚未核对完整断事条件；"
        "候选不代表已判定重大事件。",
        "童限仅标范围 · 年龄主题映射及筛选门槛为试行设计 · 吉凶并见保留原始依据",
    ]
    sources = atlas.get("knowledge_source_snapshot", {})
    used = {card.get("source") for card in atlas.get("knowledge_snapshot", [])}
    titles = [
        "《" + source["title"] + "》" for key, source in sources.items() if key in used
    ]
    version = atlas.get("preview_rule", {}).get("id", "P1-common-target-v1")
    footnotes.append(
        "规则 " + version + (" · 框架参考 " + "、".join(titles) if titles else "")
    )
    if demo:
        footnotes.append("虚构演示资料 · 仅用于图面与计算路径检查")
    y += 22
    for footnote in footnotes:
        for line in plan.wrap(footnote, 2246, 23):
            plan.text(77, y, line, 23, c["muted"])
            y += 34
    plan.height = y + 28


class LifeAtlasRenderer:
    """Render a build_life_atlas result without changing it or retaining a theme."""

    def __init__(self, font_path=""):
        self.font_path = find_font(font_path)
        ImageFont.truetype(self.font_path, 20)

    def _plan(self, atlas, theme, demo):
        plan = _Plan(self.font_path, theme)
        top = _header(plan, atlas, demo)
        bottom, colors = _timeline(plan, atlas, top)
        footer = _details(plan, atlas, bottom, colors)
        _footer(plan, atlas, footer, demo)
        return plan

    def render(self, atlas, theme="night", *, demo=False) -> bytes:
        return self._plan(atlas, theme, demo).png()

    def layout_audit(self, atlas, theme="night", *, demo=False):
        """Return geometry without writing a PNG or retaining personal data."""
        return self._plan(atlas, theme, demo).audit()
