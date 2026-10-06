"""Portable Pillow rendering; images stay in memory and need no browser service."""

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .rules import BRANCHES, HUA, STAR_NAMES, STEMS, RuleProfile

BG = "#f2eee5"
PAPER = "#fffdf7"
INK = "#263d3a"
MUTED = "#6c7871"
RED = "#b84f3e"
GOLD = "#a88445"
GRID = ((6, 7, 8, 9), (5, None, None, 10), (4, None, None, 11), (3, 2, 1, 12))


def birth_hua(chart):
    p = RuleProfile(**chart["profile"])
    return "  ".join(
        STAR_NAMES[sid] + HUA[i]
        for i, sid in enumerate(p.four_hua(chart["normalized"]["year_stem"]), 1)
    )


def star_text(star, full=False):
    text = star["name"] + ("(副)" if star["secondary"] else "")
    if star["brightness"]:
        text += f"[{star['brightness']}]"
    for layer, hua in star["hua"].items():
        if full or layer in {"生年", "流年"}:
            text += f" {layer}{hua}"
    for label, key in (("离心", "outward"), ("向心", "inward")):
        if star["self_hua"][key]:
            text += f" {label}{star['self_hua'][key]}"
    return text


def text_chart(chart):
    n, p = chart["normalized"], chart["profile"]
    yg = STEMS[n["year_stem"] - 1] + BRANCHES[n["year_branch"] - 1]
    lines = [
        "紫微斗数排盘",
        f"{chart['sex']} · {yg}年 · {chart['bureau_name']}",
        f"公历钟表：{n['civil']} UTC{n['original']['timezone']:+g}",
        f"{'真太阳时（近似）' if n['true_solar'] else '钟表时间'}：{n['computed']}",
        f"实际农历：{n['lunar']['text']}",
        f"安星农历：{n['astrology_lunar']['year']}年 "
        f"{'闰' if n['astrology_lunar']['leap'] else ''}"
        f"{n['astrology_lunar']['month']}月{n['day']}日；安星月={n['month']}",
        f"四柱：{' '.join(n['pillars'])}",
        f"命宫{BRANCHES[chart['life'] - 1]} · 身宫{BRANCHES[chart['body'] - 1]}"
        f" · 命主{chart['life_master']} · 身主{chart['body_master']}",
        f"生年四化：{birth_hua(chart)}",
        f"规则：闰月={p['leap_month_rule']}，子时={p['late_zi_rule']}，庙旺={p['brightness']}",
    ]
    if chart["flow"]:
        flow = chart["flow"]
        lines += [
            f"目标：{flow['target'] or str(flow['year']) + '农历年'}"
            f" · 虚岁{flow['age']}"
        ]
        for label, layer in flow["layers"].items():
            lines.append(
                f"{label}命宫：{BRANCHES[layer['life'] - 1]}；"
                f"四化：{' '.join(layer['four_hua'])}"
            )
    for palace in sorted(
        chart["palaces"], key=lambda item: (chart["life"] - item["branch"]) % 12
    ):
        lines += [
            "",
            f"{palace['name']} · {palace['stem_name']}{palace['branch_name']}"
            f"{' · 身宫' if palace['body'] else ''}",
        ]
        lines += [
            star_text(star, True)
            for star in sorted(palace["stars"], key=lambda item: item["id"])
        ]
        lines.append(
            "十二神："
            + " / ".join(
                f"{label}:{value}" for label, value in palace["sequences"].items()
            )
        )
        if palace["flow_names"]:
            lines.append(
                " / ".join(
                    f"{layer}:{name}" for layer, name in palace["flow_names"].items()
                )
            )
    lines += [
        "",
        "大限（虚岁，" + ("顺行" if chart["direction"] == 1 else "逆行") + "）：",
    ]
    for decade in chart["decades"]:
        label = "童限" if decade["index"] == 0 else f"第{decade['index']}限"
        lines.append(
            f"{label} {decade['age_start']}—{decade['age_end']}岁 "
            f"{STEMS[decade['stem'] - 1]}{BRANCHES[decade['branch'] - 1]}："
            f"{' '.join(decade['four_hua'])}"
        )
    lines += [
        "",
        "真太阳时与历法为独立实现；未完成原软件全盘 GUI 比对。",
        "规则版本：" + chart["rules_version"],
    ]
    return "\n".join(lines)


def find_font(configured=""):
    if configured:
        font = Path(configured).expanduser()
        if not font.is_file():
            raise ValueError("配置的字体不存在")
        return str(font)
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise ValueError("未找到中文字体")


class Renderer:
    def __init__(self, font_path=""):
        self.font_path = find_font(font_path)
        # Check readability at construction, before accepting image requests.
        ImageFont.truetype(self.font_path, 20)

    def render(self, chart) -> bytes:
        fonts = {}

        def font(size):
            if size not in fonts:
                fonts[size] = ImageFont.truetype(self.font_path, size)
            return fonts[size]

        cell_w, margin, gap = 340, 32, 8
        max_rows = max(math.ceil(len(p["stars"]) / 2) for p in chart["palaces"])
        cell_h = max(330, 118 + max_rows * 34)
        width = margin * 2 + cell_w * 4 + gap * 3
        grid_y = 140
        footer_y = grid_y + 4 * cell_h + gap * 3 + 20
        flow_count = len(chart["flow"]["layers"]) if chart["flow"] else 0
        height = footer_y + 140 + flow_count * 34
        image = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(image)

        def line(text, x, y, size=20, color=INK, max_width=None):
            if max_width is not None:
                while size > 14 and font(size).getlength(text) > max_width:
                    size -= 1
                if font(size).getlength(text) > max_width:
                    raise ValueError("盘面文字超出布局宽度")
            draw.text((x, y), text, fill=color, font=font(size))

        line("紫微斗数", margin, 24, 44)
        line("十二宫命盘", margin + 218, 45, 22, MUTED)
        line(
            "生年四化以红色标记 · ↑离心自化  ↓向心自化 · 年为流年四化",
            margin,
            91,
            19,
            MUTED,
        )
        for row, cells in enumerate(GRID):
            for col, branch in enumerate(cells):
                if branch is None:
                    continue
                p = chart["palaces"][branch - 1]
                x = margin + col * (cell_w + gap)
                y = grid_y + row * (cell_h + gap)
                active = branch == chart["life"]
                draw.rounded_rectangle(
                    (x, y, x + cell_w, y + cell_h),
                    radius=12,
                    fill=PAPER,
                    outline=RED if active else "#d7d9ce",
                    width=3 if active else 1,
                )
                line(
                    p["name"] + (" · 身宫" if p["body"] else ""),
                    x + 16,
                    y + 12,
                    25,
                    RED if active else INK,
                )
                line(
                    p["stem_name"] + p["branch_name"], x + cell_w - 62, y + 15, 22, GOLD
                )
                draw.line((x + 16, y + 53, x + cell_w - 16, y + 53), fill="#e4e4d9")
                if "流年" in p["flow_names"]:
                    line("年" + p["flow_names"]["流年"], x + 183, y + 20, 15, MUTED)
                for index, star in enumerate(sorted(p["stars"], key=lambda s: s["id"])):
                    sx, sy = x + 16 + (index % 2) * 155, y + 65 + (index // 2) * 34
                    label = (
                        star["name"]
                        + ("副" if star["secondary"] else "")
                        + star["brightness"]
                    )
                    label += star["hua"].get("生年", "")
                    if "流年" in star["hua"]:
                        label += "年" + star["hua"]["流年"]
                    label += (
                        ("↑" + star["self_hua"]["outward"])
                        if star["self_hua"]["outward"]
                        else ""
                    )
                    label += (
                        ("↓" + star["self_hua"]["inward"])
                        if star["self_hua"]["inward"]
                        else ""
                    )
                    color = (
                        RED
                        if "生年" in star["hua"]
                        else INK
                        if star["group"] != "minor"
                        else MUTED
                    )
                    line(
                        label,
                        sx,
                        sy,
                        21 if star["group"] != "minor" else 18,
                        color,
                        148,
                    )
                decades = [
                    d
                    for d in chart["decades"]
                    if d["branch"] == branch and d["index"] > 0
                ]
                age = decades[0]
                line(
                    f"{age['age_start']}—{age['age_end']}岁 "
                    f" {p['sequences']['长生']} · {p['sequences']['博士']}",
                    x + 16,
                    y + cell_h - 57,
                    17,
                    GOLD,
                    cell_w - 32,
                )
                line(
                    p["sequences"]["岁前"] + " · " + p["sequences"]["将前"],
                    x + 16,
                    y + cell_h - 31,
                    16,
                    MUTED,
                )

        center_x = margin + cell_w + gap
        center_y = grid_y + cell_h + gap
        center_w, center_h = cell_w * 2 + gap, cell_h * 2 + gap
        draw.rounded_rectangle(
            (center_x, center_y, center_x + center_w, center_y + center_h),
            radius=12,
            fill="#e7e9df",
        )
        n = chart["normalized"]
        yg = STEMS[n["year_stem"] - 1] + BRANCHES[n["year_branch"] - 1]
        line(
            f"{chart['sex']} · {yg}年 · {chart['bureau_name']}",
            center_x + 28,
            center_y + 24,
            35,
        )
        center_lines = [
            f"命宫 {BRANCHES[chart['life'] - 1]}   身宫 {BRANCHES[chart['body'] - 1]}",
            f"命主 {chart['life_master']}   身主 {chart['body_master']}",
            f"钟表  {n['civil']}   UTC{n['original']['timezone']:+g}",
            f"东经  {n['original']['longitude']:g}°"
            if n["original"]["longitude"] >= 0
            else f"西经  {-n['original']['longitude']:g}°",
            ("真太阳时（近似）" if n["true_solar"] else "计算钟表时间")
            + f"  {n['computed']}",
            f"农历  {n['lunar']['text']}",
            f"安星  {n['month']}月{n['day']}日 {BRANCHES[n['hour_branch'] - 1]}时",
            "四柱  " + "  ".join(n["pillars"]),
            "生年四化  " + birth_hua(chart),
            "闰月  "
            + {"current": "本月", "next": "下月", "split": "十五／十六分界"}[
                chart["profile"]["leap_month_rule"]
            ],
            "换日  "
            + (
                "晚子时23时"
                if chart["profile"]["late_zi_rule"] == "next_day"
                else "零点"
            ),
            "大限  " + ("顺行" if chart["direction"] == 1 else "逆行") + " · 虚岁起限",
        ]
        if chart["flow"]:
            flow = chart["flow"]
            label = (
                "童限"
                if flow["decade"]["index"] == 0
                else f"第{flow['decade']['index']}大限"
            )
            center_lines += [
                f"目标  {flow['target'] or str(flow['year']) + '农历年'}",
                f"虚岁  {flow['age']} · 当前{label}",
            ]
        for index, value in enumerate(center_lines):
            line(
                value, center_x + 28, center_y + 85 + index * 34, 21, INK, center_w - 56
            )
        line(
            "紫微斗数排盘 · 独立规则实现",
            center_x + 28,
            center_y + center_h - 46,
            18,
            MUTED,
        )
        fy = footer_y
        if chart["flow"]:
            for label, layer in chart["flow"]["layers"].items():
                line(
                    f"{label}命宫 {BRANCHES[layer['life'] - 1]}  ·  "
                    + "  ".join(layer["four_hua"]),
                    margin,
                    fy,
                    21,
                )
                fy += 34
        line(
            "历法：lunar-python 1.4.8 · 真太阳时：独立近似校正"
            " · 尚未完成原软件全盘 GUI 比对",
            margin,
            fy + 12,
            18,
            MUTED,
        )
        line("资料仅用于本次排盘，不保存命例。", margin, fy + 44, 18, MUTED)
        line(chart["rules_version"], margin, fy + 80, 15, MUTED)
        stream = io.BytesIO()
        image.save(stream, format="PNG", optimize=True)
        return stream.getvalue()
