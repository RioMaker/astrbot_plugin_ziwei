"""Modern Eastern chart layout with independent star and transformation cues."""

import io
import math

from PIL import Image, ImageDraw, ImageFont

from .rules import BRANCHES, HUA, STAR_NAMES, STEMS, RuleProfile

BG = "#f4f2eb"
PAPER = "#fffefb"
INK = "#263c36"
MUTED = "#65756d"
BORDER = "#d9dfd6"
JADE = "#296f60"
NAVY = "#365879"
CINNABAR = "#b55349"
BRONZE = "#88704c"
STAR_CATEGORIES = (
    ("major", "主星", NAVY),
    ("assistant", "辅星", JADE),
    ("malefic", "煞星", CINNABAR),
    ("minor", "杂曜", BRONZE),
)
MALEFIC_IDS = frozenset(
    {23, 24, 25, 26, 27, 28, 31, 37, 39, 41, 48, 53, 54, 57, 59, 62, 77, 78, 80}
)
HUA_COLORS = {
    "禄": ("#23765d", "#e4f0e8"),
    "权": ("#956614", "#f7edd5"),
    "科": ("#356d9b", "#e5eef6"),
    "忌": ("#aa4354", "#f7e5e8"),
}
GRID = ((6, 7, 8, 9), (5, None, None, 10), (4, None, None, 11), (3, 2, 1, 12))
CELL_WIDTH = 400
MARGIN = 32
GAP = 0


def display_category(star):
    if star["id"] in MALEFIC_IDS:
        return "malefic"
    return star["group"]


def star_marks(star):
    """Keep transformation source explicit without repainting the star name."""
    marks = []
    for layer, prefix in (("生年", "生"), ("流年", "年")):
        if star["hua"].get(layer):
            marks.append(
                {
                    "label": prefix + star["hua"][layer],
                    "hua": star["hua"][layer],
                    "source": layer,
                    "self": False,
                }
            )
    for source, prefix in (("outward", "离"), ("inward", "向")):
        value = star["self_hua"].get(source)
        if value:
            marks.append(
                {"label": prefix + value, "hua": value, "source": source, "self": True}
            )
    return marks


def make_chart_view(chart):
    """Reference-style display model, preserving all natal calculation fields."""
    birth_year = chart["normalized"]["astrology_lunar"]["year"]
    decades = {d["branch"]: d for d in chart["decades"] if d["index"] > 0}
    flow = chart["flow"]
    limit = min(2100, birth_year + chart["decades"][-1]["age_end"] - 1)
    palaces = []
    for palace in chart["palaces"]:
        groups = []
        for key, label, color in STAR_CATEGORIES:
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
        if flow and years:
            nearest = min(range(len(years)), key=lambda i: abs(years[i] - flow["year"]))
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
                    flow and palace["branch"] == flow["layers"]["流年"]["life"]
                ),
                "annual_label": f"{flow['year']} · 流年{palace['flow_names']['流年']}"
                if flow
                else "",
            }
        )
    return {"chart": chart, "palaces": palaces, "flow": flow}


class Painter:
    def __init__(self, width, height, font_path):
        self.image = Image.new("RGB", (width, height), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path
        self.fonts = {}

    def font(self, size):
        if size not in self.fonts:
            self.fonts[size] = ImageFont.truetype(self.font_path, size)
        return self.fonts[size]

    def text(self, value, x, y, size=20, color=INK, width=None):
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
        self.draw.text((x, y), value, fill=color, font=self.font(size), anchor="lt")

    def card(self, x, y, w, h, fill=PAPER, outline=BORDER, thickness=1, radius=14):
        self.draw.rounded_rectangle(
            (x, y, x + w, y + h),
            radius=radius,
            fill=fill,
            outline=outline,
            width=thickness,
        )

    def pill(
        self,
        label,
        x,
        y,
        color=MUTED,
        fill="#eef0e9",
        size=15,
        height=25,
        outline=None,
        padding=16,
    ):
        width = math.ceil(self.font(size).getlength(label)) + padding
        self.card(x, y, width, height, fill=fill, outline=outline or fill, radius=6)
        text_height = self.font(size).getbbox(label, anchor="lt")[3]
        self.text(label, x + padding / 2, y + (height - text_height) // 2, size, color)
        return width

    def rule(self, x, y, width):
        self.draw.line((x, y, x + width, y), fill=BORDER)


def _vertical_star(painter, star, x, y, color, step):
    name_size = min(25 if star["group"] == "major" else 22, int(step) - 5)
    for index, character in enumerate(star["name"]):
        painter.text(
            character, x + (step - name_size) / 2, y + index * 28, name_size, color
        )
    if star["secondary"]:
        painter.text("副", x + step - 12, y - 12, 10, MUTED)
    if star["brightness"]:
        width = painter.font(13).getlength(star["brightness"]) + 8
        painter.pill(
            star["brightness"],
            x + (step - width) / 2,
            y + 65,
            size=13,
            height=21,
            padding=8,
        )
    size = min(13, int((step - 8) / 2))
    for index, mark in enumerate(star["marks"]):
        color, fill = HUA_COLORS[mark["hua"]]
        width = painter.font(size).getlength(mark["label"]) + 8
        painter.pill(
            mark["label"],
            x + (step - width) / 2,
            y + 91 + index * 23,
            color,
            PAPER if mark["self"] else fill,
            size=size,
            height=21,
            outline=fill if mark["self"] else None,
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
                painter, star, x + 16 + col * step, y + 20 + bank * 190, color, step
            )
    if chart["flow"]:
        painter.pill(
            item["annual_label"],
            x + 70,
            y + height - 156,
            PAPER if selected else MUTED,
            JADE if selected else "#eef1e8",
            size=15,
            height=25,
        )
    painter.text("流年", x + 70, y + height - 120, 14, MUTED)
    year_x = x + 108
    for year in item["annual_years"]:
        active = bool(chart["flow"] and chart["flow"]["year"] == year)
        year_x += (
            painter.pill(
                str(year),
                year_x,
                y + height - 124,
                PAPER if active else INK,
                JADE if active else PAPER,
                size=14,
                height=23,
                padding=6,
            )
            + 1
        )
    painter.text(
        "虚岁 " + "、".join(map(str, item["annual_ages"])),
        x + 70,
        y + height - 92,
        13,
        MUTED,
        CELL_WIDTH - 123,
    )
    d = item["decade"]
    age = f"{d['age_start']}—{d['age_end']}岁"
    painter.text(
        age,
        x + (CELL_WIDTH - painter.font(21).getlength(age)) / 2,
        y + height - 64,
        21,
        INK,
    )
    name_color = NAVY if natal_life else JADE if selected else INK
    px = x + (CELL_WIDTH - painter.font(26).getlength(palace["name"])) / 2
    painter.text(palace["name"], px, y + height - 33, 26, name_color)
    if palace["body"]:
        painter.pill(
            "身",
            px + painter.font(26).getlength(palace["name"]) + 9,
            y + height - 34,
            JADE,
            "#e2ecdf",
            size=14,
            height=24,
            padding=8,
        )
    sequences = palace["sequences"]
    for index, key in enumerate(("博士", "岁前", "将前")):
        painter.text(
            sequences[key],
            x + 16,
            y + height - 81 + index * 24,
            14,
            JADE if index == 0 else MUTED,
        )
    for index, character in enumerate(sequences["长生"]):
        painter.text(
            character, x + CELL_WIDTH - 31, y + height - 121 + index * 17, 14, MUTED
        )
    painter.text(palace["stem_name"], x + CELL_WIDTH - 34, y + height - 65, 23, BRONZE)
    painter.text(
        palace["branch_name"], x + CELL_WIDTH - 34, y + height - 36, 23, BRONZE
    )
    if selected or natal_life:
        painter.draw.rectangle(
            (x + 3, y + 3, x + CELL_WIDTH - 3, y + height - 3),
            outline=JADE if selected else NAVY,
            width=2,
        )


def _draw_hua_row(painter, row, x, y, cell=152, size=19):
    for index, (name, value) in enumerate(row):
        sx = x + index * cell
        color, fill = HUA_COLORS[value]
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


def _draw_center(painter, chart, x, y, width, height):
    painter.card(x, y, width, height, fill="#eaf0e7", outline=BORDER, radius=0)
    n, profile = chart["normalized"], chart["profile"]
    painter.text("本命概览", x + 28, y + 23, 18, JADE)
    yg = STEMS[n["year_stem"] - 1] + BRANCHES[n["year_branch"] - 1]
    painter.text(f"{yg}年 · {chart['bureau_name']}", x + 28, y + 56, 34)
    painter.pill(
        chart["sex"], x + width - 67, y + 61, JADE, "#d7e6d7", size=18, height=30
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
        painter.text(label, x + 28, y + 165 + index * 32, 16, MUTED)
        painter.text(value, x + 135, y + 162 + index * 32, 20, width=width - 163)
    painter.text("四柱", x + 28, y + 317, 16, MUTED)
    for index, pillar in enumerate(n["pillars"]):
        sx = x + 135 + index * 142
        painter.text(
            ("年柱", "月柱", "日柱", "时柱")[index], sx + 8, y + 292, 13, MUTED
        )
        painter.pill(pillar, sx, y + 311, INK, PAPER, size=24, height=40)
    painter.text("生年四化", x + 28, y + 373, 18, JADE)
    p = RuleProfile(**profile)
    row = [
        (STAR_NAMES[sid], HUA[i]) for i, sid in enumerate(p.four_hua(n["year_stem"]), 1)
    ]
    _draw_hua_row(painter, row, x + 28, y + 407, cell=(width - 56) // 4)
    painter.rule(x + 28, y + 449, width - 56)
    leap = {"current": "本月", "next": "下月", "split": "十五／十六分界"}[
        profile["leap_month_rule"]
    ]
    boundary = "23时换日" if profile["late_zi_rule"] == "next_day" else "零点换日"
    painter.text(f"闰月 {leap}  ·  {boundary}", x + 28, y + 469, 16, MUTED)
    painter.text(
        f"大限：虚岁起限 · {'顺行' if chart['direction'] == 1 else '逆行'}",
        x + 28,
        y + 498,
        16,
        MUTED,
    )
    if chart["flow"]:
        flow = chart["flow"]
        by = y + 540
        painter.card(x + 24, by, width - 48, 151, fill=PAPER, outline="#d6e3d6")
        painter.text("当前流年", x + 44, by + 16, 16, JADE)
        annual_gz = STEMS[(flow["year"] - 4) % 10] + BRANCHES[(flow["year"] - 4) % 12]
        painter.text(f"{flow['year']} · {annual_gz}", x + 44, by + 49, 28, JADE)
        label = "童限" if flow["decade"]["index"] == 0 else "大限"
        painter.text(
            f"虚岁 {flow['age']}   {label} "
            f"{flow['decade']['age_start']}—{flow['decade']['age_end']}岁",
            x + 365,
            by + 55,
            18,
            width=width - 407,
        )
        painter.text(
            "目标 " + (flow["target"] or f"{flow['year']}农历年"),
            x + 44,
            by + 101,
            18,
            MUTED,
            width - 88,
        )
        painter.text(
            "年份按农历标注，同宫流年每12年重复。", x + 44, by + 130, 14, MUTED
        )
    else:
        painter.text("大限一览 · 虚岁", x + 28, y + 538, 16, JADE)
        cell = (width - 56) / 4
        for i, d in enumerate(chart["decades"][1:]):
            sx = x + 28 + i % 4 * cell
            sy = y + 572 + i // 4 * 40
            painter.text(
                f"{d['age_start']}—{d['age_end']}岁 · {BRANCHES[d['branch'] - 1]}",
                sx,
                sy,
                16,
                INK,
            )
    if height < 710:
        raise ValueError("盘心资料区域高度不足")


def render_chart(chart, font_path):
    view = make_chart_view(chart)
    max_banks = max(math.ceil(len(p["palace"]["stars"]) / 12) for p in view["palaces"])
    cell_height = max(360, max_banks * 190 + 176)
    width = 2 * MARGIN + 4 * CELL_WIDTH
    grid_y = 160
    footer_y = grid_y + 4 * cell_height + 24
    layers = list(chart["flow"]["layers"].items()) if chart["flow"] else []
    summary_height = math.ceil(len(layers) / 2) * 88 + 38 if layers else 0
    painter = Painter(width, footer_y + summary_height + 84, font_path)
    painter.draw.line((MARGIN, 30, MARGIN + 4, 93), fill=JADE, width=4)
    painter.text("紫微斗数", MARGIN + 21, 30, 41)
    painter.text("十二宫命盘", MARGIN + 213, 54, 20, MUTED)
    label = (
        f"{chart['flow']['year']} 农历流年 · 虚岁{chart['flow']['age']}"
        if chart["flow"]
        else "本命盘"
    )
    painter.pill(
        label,
        width - MARGIN - (289 if chart["flow"] else 110),
        40,
        JADE,
        "#e2ecdf",
        size=20,
        height=41,
    )
    lx = MARGIN
    for _, label, color in STAR_CATEGORIES:
        painter.draw.ellipse((lx, 109, lx + 8, 117), fill=color)
        painter.text(label, lx + 18, 104, 18, color)
        lx += 102
    painter.text("四化", lx + 10, 105, 17, MUTED)
    for i, value in enumerate(HUA[1:]):
        color, fill = HUA_COLORS[value]
        painter.pill(value, lx + 65 + i * 44, 98, color, fill, size=17, height=30)
    painter.text("生＝生年  年＝流年  离／向＝自化", lx + 270, 105, 17, MUTED)
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
    if layers:
        painter.text("运限四化", MARGIN, footer_y, 21, JADE)
        for i, (label, layer) in enumerate(layers):
            sx = MARGIN + i % 2 * (2 * CELL_WIDTH + 8)
            sy = footer_y + 38 + i // 2 * 88
            painter.card(sx, sy, 2 * CELL_WIDTH - 8, 76)
            painter.text(
                f"{label} · 命宫{BRANCHES[layer['life'] - 1]}",
                sx + 18,
                sy + 12,
                16,
                MUTED,
            )
            pairs = [(text[:-1], text[-1]) for text in layer["four_hua"]]
            _draw_hua_row(painter, pairs, sx + 18, sy + 39, cell=184, size=18)
    by = footer_y + summary_height + 10
    painter.text(
        "宫位大限为虚岁；同宫流年年份每12年重复，选中流年单独高亮。",
        MARGIN,
        by,
        16,
        MUTED,
    )
    painter.text(
        "真太阳时为近似校正。出生资料仅用于本次排盘，不保存命例。",
        MARGIN,
        by + 29,
        15,
        MUTED,
    )
    stream = io.BytesIO()
    painter.image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()
