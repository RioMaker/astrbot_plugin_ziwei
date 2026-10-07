"""Modern Eastern chart layout with independent star and transformation cues."""

import io
import math

from PIL import Image, ImageDraw, ImageFont

from .rules import BRANCHES, HUA, STAR_NAMES, STEMS, RuleProfile
from .themes import get_theme

STAR_CATEGORIES = (
    ("major", "主星"),
    ("assistant", "辅星"),
    ("malefic", "煞星"),
    ("minor", "杂曜"),
)
HUA_LAYERS = ("生年", "大限", "流年")
LAYER_NAMES = {"生年": "生年", "大限": "大运", "流年": "流年"}
MALEFIC_IDS = frozenset(
    {23, 24, 25, 26, 27, 28, 31, 37, 39, 41, 48, 53, 54, 57, 59, 62, 77, 78, 80}
)
GRID = ((6, 7, 8, 9), (5, None, None, 10), (4, None, None, 11), (3, 2, 1, 12))
CELL_WIDTH = 400
MARGIN = 32
GAP = 0
PALACE_POSITIONS = {
    branch: (row, col)
    for row, cells in enumerate(GRID)
    for col, branch in enumerate(cells)
    if branch is not None
}


def self_hua_vector(branch, source, *, cell_height=CELL_WIDTH):
    """Outward points away from the chart center; inward points toward it."""
    if source not in {"outward", "inward"}:
        raise ValueError("未知自化方向")
    row, col = PALACE_POSITIONS[branch]
    dx, dy = (col - 1.5) * CELL_WIDTH, (row - 1.5) * cell_height
    length = math.hypot(dx, dy)
    sign = 1 if source == "outward" else -1
    return sign * dx / length, sign * dy / length


def _horizontal_arrow_slot(index, count):
    """Leave the central palace title and its body badge clear."""
    left_count = (count + 1) // 2
    if index < left_count:
        return 66 + 80 * (index + 1) / (left_count + 1)
    return 280 + 64 * (index - left_count + 1) / (count - left_count + 1)


def self_hua_arrow_layout(stars, branch, x, y, height):
    """Outer frame for outward Hua; inner frame for inward Hua."""
    row, col = PALACE_POSITIONS[branch]
    outer_side = (
        "top" if row == 0 else "bottom" if row == 3 else "left" if col == 0 else "right"
    )
    opposite = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
    arrows = []
    for source in ("outward", "inward"):
        marks = [
            (s, s["self_hua"].get(source)) for s in stars if s["self_hua"].get(source)
        ]
        side = outer_side if source == "outward" else opposite[outer_side]
        for index, (star, value) in enumerate(marks):
            lane = (index + 1) / (len(marks) + 1)
            if side in {"top", "bottom"}:
                anchor = (
                    x + _horizontal_arrow_slot(index, len(marks)),
                    y if side == "top" else y + height,
                )
                label = (anchor[0] + 18, y + 2 if side == "top" else y + height - 15)
            else:
                anchor = (
                    x if side == "left" else x + CELL_WIDTH,
                    y + 24 + (height - 128) * lane,
                )
                if source == "outward":
                    label = (
                        x - 23 if side == "left" else x + CELL_WIDTH + 9,
                        anchor[1] + 14,
                    )
                else:
                    label = (
                        x + 7 if side == "left" else x + CELL_WIDTH - 21,
                        anchor[1] + 14,
                    )
            dx, dy = self_hua_vector(branch, source, cell_height=height)
            arrows.append(
                {
                    "star_id": star["id"],
                    "instance_id": star["instance_id"],
                    "source": source,
                    "hua": value,
                    "side": side,
                    "start": (anchor[0] - dx * 10, anchor[1] - dy * 10),
                    "end": (anchor[0] + dx * 15, anchor[1] + dy * 15),
                    "label_position": label,
                }
            )
    return arrows


def display_category(star):
    if star["id"] in MALEFIC_IDS:
        return "malefic"
    return star["group"]


def star_marks(star):
    """Keep transformation source explicit without repainting the star name."""
    marks = []
    for row, layer in enumerate(HUA_LAYERS):
        if star["hua"].get(layer):
            marks.append(
                {
                    "label": star["hua"][layer],
                    "hua": star["hua"][layer],
                    "source": layer,
                    "row": row,
                    "self": False,
                }
            )
    return marks


def make_chart_view(chart, theme="day"):
    """Reference-style display model, preserving all natal calculation fields."""
    palette = get_theme(theme)
    birth_year = chart["normalized"]["astrology_lunar"]["year"]
    decades = {d["branch"]: d for d in chart["decades"] if d["index"] > 0}
    flow = chart["flow"]
    layers = flow["layers"] if flow else {}
    selected_year = flow.get("year") if flow else None
    focus_year = selected_year or (flow.get("start_year") if flow else None)
    limit = min(2100, birth_year + chart["decades"][-1]["age_end"] - 1)
    palaces = []
    for palace in chart["palaces"]:
        groups = []
        for key, label in STAR_CATEGORIES:
            color = palette.star_color(key)
            stars = [
                dict(s, marks=star_marks(s))
                for s in sorted(palace["stars"], key=lambda s: s["id"])
                if display_category(s) == key
            ]
            if stars:
                groups.append(
                    {"key": key, "label": label, "color": color, "stars": stars}
                )
        first_year = birth_year + (palace["branch"] - ((birth_year - 4) % 12 + 1)) % 12
        years = [year for year in range(first_year, limit + 1, 12) if year >= 1900]
        start = 0
        if focus_year is not None and years:
            nearest = min(range(len(years)), key=lambda i: abs(years[i] - focus_year))
            start = min(max(0, nearest - 2), max(0, len(years) - 5))
        years = years[start : start + 5]
        decade = decades[palace["branch"]]
        palaces.append(
            {
                "palace": palace,
                "groups": groups,
                "decade": decade,
                "decade_year_start": birth_year + decade["age_start"] - 1,
                "decade_year_end": birth_year + decade["age_end"] - 1,
                "annual_years": years,
                "annual_ages": [year - birth_year + 1 for year in years],
                "annual_selected": bool(
                    "流年" in layers and palace["branch"] == layers["流年"]["life"]
                ),
                "decade_selected": bool(
                    "大限" in layers and palace["branch"] == layers["大限"]["life"]
                ),
                "decade_label": f"大运{palace['flow_names']['大限']}"
                if "大限" in layers
                else "",
                "annual_label": f"{flow['year']} · 流年{palace['flow_names']['流年']}"
                if selected_year is not None
                else "",
            }
        )
    return {"chart": chart, "palaces": palaces, "flow": flow}


class Painter:
    def __init__(self, width, height, font_path, theme="day"):
        self.palette = get_theme(theme)
        self.image = Image.new("RGB", (width, height), self.palette.bg)
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path
        self.fonts = {}

    def font(self, size):
        if size not in self.fonts:
            self.fonts[size] = ImageFont.truetype(self.font_path, size)
        return self.fonts[size]

    def text(self, value, x, y, size=20, color=None, width=None):
        while (
            width is not None and self.font(size).getlength(value) > width and size > 14
        ):
            size -= 1
        box = self.font(size).getbbox(value, anchor="lt")
        if width is not None and box[2] > width:
            raise ValueError("盘面文字超出布局宽度")
        if (
            x < 0
            or y < 0
            or x + box[2] > self.image.width
            or y + box[3] > self.image.height
        ):
            raise ValueError("盘面文字超出画布")
        self.draw.text(
            (x, y),
            value,
            fill=color or self.palette.ink,
            font=self.font(size),
            anchor="lt",
        )

    def card(self, x, y, w, h, fill=None, outline=None, thickness=1, radius=14):
        self.draw.rounded_rectangle(
            (x, y, x + w, y + h),
            radius=radius,
            fill=fill or self.palette.paper,
            outline=outline or self.palette.border,
            width=thickness,
        )

    def pill(
        self,
        label,
        x,
        y,
        color=None,
        fill=None,
        size=15,
        height=25,
        outline=None,
        padding=16,
    ):
        width = math.ceil(self.font(size).getlength(label)) + padding
        fill = fill or self.palette.center
        self.card(x, y, width, height, fill=fill, outline=outline or fill, radius=6)
        text_height = self.font(size).getbbox(label, anchor="lt")[3]
        self.text(
            label,
            x + padding / 2,
            y + (height - text_height) // 2,
            size,
            color or self.palette.muted,
        )
        return width

    def rule(self, x, y, width):
        self.draw.line((x, y, x + width, y), fill=self.palette.border)

    def arrow(self, start, end, color, *, head=6, stroke=2):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if not length:
            return
        ux, uy = dx / length, dy / length
        base = (end[0] - ux * head, end[1] - uy * head)
        self.draw.line((start, base), fill=color, width=stroke)
        self.draw.polygon(
            (
                end,
                (base[0] - uy * head / 2, base[1] + ux * head / 2),
                (base[0] + uy * head / 2, base[1] - ux * head / 2),
            ),
            fill=color,
        )


def _vertical_star(painter, star, x, y, color, step):
    major = star["group"] == "major"
    name_size = min(26 if major else 23, int(step) - 5)
    for index, character in enumerate(star["name"]):
        painter.text(
            character,
            x + (step - name_size) / 2,
            y + index * 28,
            name_size,
            color,
        )
    if star["secondary"]:
        painter.text("副", x + step - 13, y - 15, 12, painter.palette.muted)
    if star["brightness"]:
        width = painter.font(14).getlength(star["brightness"]) + 8
        painter.pill(
            star["brightness"],
            x + (step - width) / 2,
            y + 65,
            size=14,
            height=22,
            padding=8,
        )
    size = 14
    for mark in star["marks"]:
        color, fill = painter.palette.layer_style(mark["source"])
        width = painter.font(size).getlength(mark["label"]) + 8
        painter.pill(
            mark["label"],
            x + (step - width) / 2,
            y + 91 + mark["row"] * 24,
            color,
            fill,
            size=size,
            height=24,
            outline=fill,
            padding=8,
        )


def _draw_palace(painter, item, chart, x, y, height):
    palace = item["palace"]
    selected = item["annual_selected"]
    natal_life = palace["branch"] == chart["life"]
    painter.card(x, y, CELL_WIDTH, height, radius=0)
    stars = [(s, g["color"]) for g in item["groups"] for s in g["stars"]]
    banks = math.ceil(len(stars) / 12)
    for bank in range(banks):
        row = stars[bank * 12 : (bank + 1) * 12]
        step = min(36, (CELL_WIDTH - 40) / len(row))
        for col, (star, color) in enumerate(row):
            _vertical_star(
                painter,
                star,
                x + 16 + col * step,
                y + 30 + bank * 190,
                color,
                step,
            )
    badge_x = x + 70
    for source, label in (
        ("大限", item["decade_label"]),
        ("流年", item["annual_label"]),
    ):
        if label:
            foreground, fill = painter.palette.layer_style(source)
            badge_x += (
                painter.pill(
                    label,
                    badge_x,
                    y + height - 156,
                    foreground,
                    fill,
                    size=14,
                    height=25,
                    padding=12,
                )
                + 8
            )
    painter.text("流年", x + 70, y + height - 120, 15, painter.palette.muted)
    year_x = x + 110
    year_foreground, year_fill = painter.palette.layer_style("流年")
    for year in item["annual_years"]:
        active = bool(chart["flow"] and chart["flow"]["year"] == year)
        year_x += (
            painter.pill(
                str(year),
                year_x,
                y + height - 124,
                year_foreground if active else painter.palette.ink,
                year_fill if active else painter.palette.paper,
                size=15,
                height=25,
                padding=4,
            )
            + 1
        )
    painter.text(
        "虚岁 " + "、".join(map(str, item["annual_ages"])),
        x + 70,
        y + height - 92,
        14,
        painter.palette.muted,
        CELL_WIDTH - 123,
    )
    d = item["decade"]
    age = f"{d['age_start']}—{d['age_end']}岁"
    painter.text(
        age,
        x + (CELL_WIDTH - painter.font(21).getlength(age)) / 2,
        y + height - 64,
        21,
        painter.palette.ink,
    )
    name_color = (
        painter.palette.major
        if natal_life
        else painter.palette.hua_color("科")[0]
        if selected
        else painter.palette.ink
    )
    px = x + (CELL_WIDTH - painter.font(26).getlength(palace["name"])) / 2
    painter.text(palace["name"], px, y + height - 33, 26, name_color)
    if palace["body"]:
        painter.pill(
            "身",
            px + painter.font(26).getlength(palace["name"]) + 9,
            y + height - 34,
            painter.palette.accent,
            painter.palette.center,
            size=15,
            height=24,
            padding=8,
        )
    sequences = palace["sequences"]
    for index, key in enumerate(("博士", "岁前", "将前")):
        painter.text(
            sequences[key],
            x + 12,
            y + height - 81 + index * 24,
            15,
            painter.palette.accent if index == 0 else painter.palette.muted,
        )
    for index, character in enumerate(sequences["长生"]):
        painter.text(
            character,
            x + CELL_WIDTH - 30,
            y + height - 121 + index * 18,
            15,
            painter.palette.muted,
        )
    painter.text(
        palace["stem_name"],
        x + CELL_WIDTH - 34,
        y + height - 65,
        24,
        painter.palette.element_color(palace["stem_name"]),
    )
    painter.text(
        palace["branch_name"],
        x + CELL_WIDTH - 34,
        y + height - 36,
        24,
        painter.palette.element_color(palace["branch_name"]),
    )
    if selected or item["decade_selected"] or natal_life:
        outline = (
            painter.palette.hua_color("科")[0]
            if selected
            else painter.palette.hua_color("禄")[0]
            if item["decade_selected"]
            else painter.palette.major
        )
        painter.draw.rectangle(
            (x + 3, y + 3, x + CELL_WIDTH - 3, y + height - 3),
            outline=outline,
            width=2,
        )


def _draw_hua_row(painter, row, x, y, cell=152, size=19, source=None):
    for index, (name, value) in enumerate(row):
        sx = x + index * cell
        color, fill = (
            painter.palette.layer_style(source)
            if source in HUA_LAYERS
            else painter.palette.hua_color(value)
        )
        painter.text(name, sx, y + 5, size)
        painter.pill(
            value,
            sx + painter.font(size).getlength(name) + 9,
            y,
            color,
            fill,
            size=16,
            height=28,
        )


def _draw_compact_pillars(painter, pillars, x, y):
    """Four columns, stems above branches, in a 192 x 72 area."""
    for index, pillar in enumerate(pillars):
        sx = x + index * 48
        painter.text(
            ("年柱", "月柱", "日柱", "时柱")[index],
            sx + 9,
            y,
            13,
            painter.palette.muted,
        )
        for line, character in enumerate(pillar):
            painter.text(
                character,
                sx + 11,
                y + 22 + line * 26,
                24,
                painter.palette.element_color(character),
            )


def _draw_center(painter, chart, x, y, width, height):
    painter.card(
        x,
        y,
        width,
        height,
        fill=painter.palette.center,
        outline=painter.palette.border,
        radius=0,
    )
    n, profile = chart["normalized"], chart["profile"]
    painter.text("本命概览", x + 28, y + 23, 18, painter.palette.accent)
    yg = STEMS[n["year_stem"] - 1] + BRANCHES[n["year_branch"] - 1]
    painter.text(f"{yg}年 · {chart['bureau_name']}", x + 28, y + 56, 34)
    painter.pill(
        chart["sex"],
        x + width - 67,
        y + 61,
        painter.palette.accent,
        painter.palette.center,
        size=18,
        height=30,
    )
    painter.text(
        f"命宫 {BRANCHES[chart['life'] - 1]}   身宫 {BRANCHES[chart['body'] - 1]}"
        f"   命主 {chart['life_master']}   身主 {chart['body_master']}",
        x + 28,
        y + 108,
        20,
        width=width - 56,
    )
    painter.rule(x + 28, y + 145, width - 56)
    longitude = n["original"]["longitude"]
    computed = n["computed"] + (
        f"  {'东经' if longitude >= 0 else '西经'}{abs(longitude):g}°"
        if n["true_solar"]
        else ""
    )
    fields = [
        ("出生钟表", f"{n['civil']}  UTC{n['original']['timezone']:+g}"),
        ("真太阳时" if n["true_solar"] else "计算时刻", computed),
        ("实际农历", n["lunar"]["text"]),
        (
            "安星月日",
            f"{n['month']}月{n['day']}日 · {BRANCHES[n['hour_branch'] - 1]}时",
        ),
    ]
    for index, (label, value) in enumerate(fields):
        painter.text(label, x + 28, y + 165 + index * 32, 17, painter.palette.muted)
        painter.text(value, x + 135, y + 162 + index * 32, 21, width=width - 163)
    _draw_compact_pillars(painter, n["pillars"], x + 28, y + 292)
    profile_rules = RuleProfile(**profile)
    flow = chart["flow"]
    flow_layers = flow["layers"] if flow else {}
    rows = [
        (
            "生年",
            [
                (STAR_NAMES[sid], HUA[i])
                for i, sid in enumerate(profile_rules.four_hua(n["year_stem"]), 1)
            ],
        )
    ]
    for source in ("大限", "流年"):
        if source in flow_layers:
            rows.append(
                (
                    source,
                    [
                        (value[:-1], value[-1])
                        for value in flow_layers[source]["four_hua"]
                    ],
                )
            )
    painter.text("四化来源", x + 264, y + 298, 16, painter.palette.muted)
    legend_x = x + 264
    for source, _ in rows:
        foreground, fill = painter.palette.layer_style(source)
        legend_x += (
            painter.pill(
                LAYER_NAMES[source],
                legend_x,
                y + 325,
                foreground,
                fill,
                size=14,
                height=25,
                padding=12,
            )
            + 10
        )
    for index, (source, pairs) in enumerate(rows):
        sy = y + 386 + index * 36
        foreground, fill = painter.palette.layer_style(source)
        painter.pill(
            LAYER_NAMES[source],
            x + 28,
            sy + 2,
            foreground,
            fill,
            size=14,
            height=25,
            padding=12,
        )
        _draw_hua_row(
            painter, pairs, x + 98, sy, cell=(width - 126) / 4, size=19, source=source
        )
    row_end = y + 386 + len(rows) * 36
    painter.rule(x + 28, row_end + 8, width - 56)
    leap = {"current": "本月", "next": "下月", "split": "十五／十六分界"}[
        profile["leap_month_rule"]
    ]
    boundary = "23时换日" if profile["late_zi_rule"] == "next_day" else "零点换日"
    painter.text(
        f"闰月 {leap}  ·  {boundary}", x + 28, row_end + 26, 16, painter.palette.muted
    )
    painter.text(
        f"大限：虚岁起限 · {'顺行' if chart['direction'] == 1 else '逆行'}",
        x + 28,
        row_end + 53,
        16,
        painter.palette.muted,
    )
    if flow:
        by = row_end + 96
        painter.card(x + 24, by, width - 48, 120)
        decade = flow["decade"]
        decade_label = "童限" if decade["index"] == 0 else f"第{decade['index']}大运"
        if flow.get("kind") == "decade":
            painter.text("当前运限", x + 44, by + 12, 16, painter.palette.accent)
            painter.text(decade_label, x + 44, by + 40, 27, painter.palette.accent)
            painter.text(
                f"虚岁 {decade['age_start']}—{decade['age_end']}岁",
                x + 350,
                by + 44,
                20,
                width=width - 394,
            )
            target = (
                f"农历 {flow['start_year']}—{flow['end_year']}年"
                f" · 命宫{BRANCHES[decade['branch'] - 1]}"
            )
        else:
            painter.text("当前流年", x + 44, by + 12, 16, painter.palette.accent)
            annual_gz = (
                STEMS[(flow["year"] - 4) % 10] + BRANCHES[(flow["year"] - 4) % 12]
            )
            painter.text(
                f"{flow['year']} · {annual_gz}",
                x + 44,
                by + 40,
                27,
                painter.palette.accent,
            )
            painter.text(
                f"虚岁 {flow['age']}   {decade_label} "
                f"{decade['age_start']}—{decade['age_end']}岁",
                x + 350,
                by + 44,
                17,
                width=width - 394,
            )
            target = "目标 " + (flow["target"] or f"{flow['year']}农历年")
        painter.text(target, x + 44, by + 85, 17, painter.palette.muted, width - 88)
    else:
        painter.text(
            "大限一览 · 虚岁", x + 28, row_end + 94, 16, painter.palette.accent
        )
        cell = (width - 56) / 4
        for index, decade in enumerate(chart["decades"][1:]):
            painter.text(
                f"{decade['age_start']}—{decade['age_end']}岁 "
                f"· {BRANCHES[decade['branch'] - 1]}",
                x + 28 + index % 4 * cell,
                row_end + 128 + index // 4 * 40,
                16,
            )
    if height < 710:
        raise ValueError("盘心资料区域高度不足")


def render_chart(chart, font_path, theme="day"):
    view = make_chart_view(chart, theme)
    max_banks = max(math.ceil(len(p["palace"]["stars"]) / 12) for p in view["palaces"])
    cell_height = max(376, max_banks * 190 + 186)
    width = 2 * MARGIN + 4 * CELL_WIDTH
    grid_y = 118
    footer_y = grid_y + 4 * cell_height + 24
    layers = list(chart["flow"]["layers"].items()) if chart["flow"] else []
    summary_height = math.ceil(len(layers) / 2) * 88 + 38 if layers else 0
    painter = Painter(width, footer_y + summary_height + 84, font_path, theme)
    painter.draw.line(
        (MARGIN, 30, MARGIN + 4, 93), fill=painter.palette.accent, width=4
    )
    painter.text("紫微斗数", MARGIN + 21, 30, 40)
    painter.text("十二宫命盘", MARGIN + 213, 54, 20, painter.palette.muted)
    if chart["flow"] and chart["flow"].get("kind") == "decade":
        decade = chart["flow"]["decade"]
        label = (
            "童限盘"
            if decade["index"] == 0
            else f"第{decade['index']}大运 "
            f"· {decade['age_start']}—{decade['age_end']}岁"
        )
    else:
        label = (
            f"{chart['flow']['year']} 农历流年 · 虚岁{chart['flow']['age']}"
            if chart["flow"]
            else "本命盘"
        )
    painter.pill(
        label,
        width - MARGIN - (289 if chart["flow"] else 110),
        40,
        painter.palette.accent,
        painter.palette.center,
        size=20,
        height=41,
    )
    for row, cells in enumerate(GRID):
        for col, branch in enumerate(cells):
            if branch is not None:
                _draw_palace(
                    painter,
                    view["palaces"][branch - 1],
                    chart,
                    MARGIN + col * CELL_WIDTH,
                    grid_y + row * cell_height,
                    cell_height,
                )
    _draw_center(
        painter,
        chart,
        MARGIN + CELL_WIDTH,
        grid_y + cell_height,
        2 * CELL_WIDTH,
        2 * cell_height,
    )
    # Paint inner frame arrows after palace and center backgrounds.
    for row, cells in enumerate(GRID):
        for col, branch in enumerate(cells):
            if branch is not None:
                for arrow in self_hua_arrow_layout(
                    chart["palaces"][branch - 1]["stars"],
                    branch,
                    MARGIN + col * CELL_WIDTH,
                    grid_y + row * cell_height,
                    cell_height,
                ):
                    color, _ = painter.palette.hua_color(arrow["hua"])
                    painter.arrow(arrow["start"], arrow["end"], color)
                    painter.text(arrow["hua"], *arrow["label_position"], 13, color)
    if layers:
        painter.text("运限四化", MARGIN, footer_y, 21, painter.palette.accent)
        for i, (label, layer) in enumerate(layers):
            sx = MARGIN + i % 2 * (2 * CELL_WIDTH + 8)
            sy = footer_y + 38 + i // 2 * 88
            painter.card(sx, sy, 2 * CELL_WIDTH - 8, 76)
            painter.text(
                f"{LAYER_NAMES.get(label, label)} · 命宫{BRANCHES[layer['life'] - 1]}",
                sx + 18,
                sy + 12,
                16,
                painter.palette.muted,
            )
            pairs = [(text[:-1], text[-1]) for text in layer["four_hua"]]
            _draw_hua_row(
                painter, pairs, sx + 18, sy + 39, cell=184, size=18, source=label
            )
    by = footer_y + summary_height + 10
    painter.text(
        "宫位大限为虚岁；同宫流年年份每12年重复，选中流年单独高亮。",
        MARGIN,
        by,
        16,
        painter.palette.muted,
    )
    painter.text(
        "真太阳时为近似校正。支持本人临时读盘，不建立永久命例库。",
        MARGIN,
        by + 29,
        15,
        painter.palette.muted,
    )
    stream = io.BytesIO()
    painter.image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()
