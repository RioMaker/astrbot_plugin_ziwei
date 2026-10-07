"""Portable Pillow rendering; images stay in memory and need no browser service."""

from pathlib import Path

from PIL import ImageFont

from .modern_renderer import render_chart
from .rules import BRANCHES, HUA, STAR_NAMES, STEMS, RuleProfile
from .themes import normalize_theme


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
        if flow.get("kind") == "decade":
            decade = flow["decade"]
            label = "童限" if decade["index"] == 0 else f"第{decade['index']}大运"
            lines.append(
                f"运限：{label} · 虚岁{decade['age_start']}—{decade['age_end']}"
                f" · 农历{flow['start_year']}—{flow['end_year']}年"
            )
        else:
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
    def __init__(self, font_path="", theme="day"):
        self.font_path = find_font(font_path)
        self.theme = normalize_theme(theme)
        # Check readability at construction, before accepting image requests.
        ImageFont.truetype(self.font_path, 20)

    def render(self, chart, theme=None) -> bytes:
        return render_chart(
            chart, self.font_path, self.theme if theme is None else theme
        )
