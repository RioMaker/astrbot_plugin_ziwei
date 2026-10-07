import io
from copy import deepcopy

import pytest
from PIL import Image

from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request
from astrbot_plugin_ziwei.engine import apply_decade, apply_flow, build_chart
from astrbot_plugin_ziwei.modern_renderer import (
    Painter,
    _draw_compact_pillars,
    make_chart_view,
)
from astrbot_plugin_ziwei.renderer import Renderer, find_font, text_chart
from astrbot_plugin_ziwei.rules import PALACES
from astrbot_plugin_ziwei.themes import get_theme

from .test_themes import contrast


@pytest.fixture
def font_path():
    try:
        return find_font()
    except ValueError:
        pytest.skip("需要中文字体进行图片验收")


@pytest.mark.parametrize("index", [0, 1, 12])
def test_decade_view_has_no_selected_annual_layer(index):
    chart = apply_decade(build_chart(parse_request(DEMO_BIRTH).birth), index)
    before = deepcopy(chart)
    view = make_chart_view(chart)
    assert sum(item["decade_selected"] for item in view["palaces"]) == 1
    assert not any(
        item["annual_selected"] or item["annual_label"] for item in view["palaces"]
    )
    assert all(item["decade_label"] for item in view["palaces"])
    assert all(
        mark["source"] in {"生年", "大限"}
        for item in view["palaces"]
        for group in item["groups"]
        for star in group["stars"]
        for mark in star["marks"]
    )
    assert chart == before
    text = text_chart(chart)
    assert "None" not in text and "流年命宫" not in text
    assert ("童限" if index == 0 else f"第{index}大运") in text


def test_identical_hua_at_same_star_keep_all_three_source_rows():
    # 2020 is 庚子, selected 庚辰大限: all three layers share 庚四化.
    chart = apply_flow(build_chart(parse_request(DEMO_BIRTH).birth), year=2020)
    before = deepcopy(chart)
    view = make_chart_view(chart)
    marks_by_source = {source: [] for source in ("生年", "大限", "流年")}
    for item in view["palaces"]:
        for group in item["groups"]:
            for star in group["stars"]:
                for mark in star["marks"]:
                    marks_by_source[mark["source"]].append(
                        (star["instance_id"], mark["label"])
                    )
                if star["marks"]:
                    assert [mark["row"] for mark in star["marks"]] == [0, 1, 2]
    assert len(marks_by_source["生年"]) == 4
    assert marks_by_source["生年"] == marks_by_source["大限"] == marks_by_source["流年"]
    assert sum(item["annual_selected"] for item in view["palaces"]) == 1
    assert sum(item["decade_selected"] for item in view["palaces"]) == 1
    assert chart == before


@pytest.mark.parametrize("theme", ["day", "night"])
def test_compact_pillars_color_each_character_and_stay_small(
    monkeypatch, theme, font_path
):
    pillars = build_chart(parse_request(DEMO_BIRTH).birth)["normalized"]["pillars"]
    painter = Painter(220, 90, font_path, theme)
    labels = []
    real_text = painter.text

    def record(value, x, y, size=20, color=None, width=None):
        real_text(value, x, y, size, color, width)
        box = painter.font(size).getbbox(value, anchor="lt")
        labels.append((value, x, y, x + box[2], y + box[3], color))

    monkeypatch.setattr(painter, "text", record)
    _draw_compact_pillars(painter, pillars, 5, 5)
    assert len(labels) == 12
    assert [item[0] for item in labels if len(item[0]) == 2] == [
        "年柱",
        "月柱",
        "日柱",
        "时柱",
    ]
    characters = [item for item in labels if len(item[0]) == 1]
    assert [item[0] for item in characters] == list("".join(pillars))
    assert all(item[5] == painter.palette.element_color(item[0]) for item in characters)
    assert max(item[3] for item in labels) <= 5 + 192
    assert max(item[4] for item in labels) <= 5 + 72


@pytest.mark.parametrize("theme", ["day", "night"])
def test_three_source_fills_are_distinct_and_readable(theme):
    p = get_theme(theme)
    styles = [p.layer_style(source) for source in ("生年", "大限", "流年")]
    assert len({fill for _, fill in styles}) == 3
    assert all(contrast(foreground, fill) >= 4.5 for foreground, fill in styles)


@pytest.mark.parametrize("theme", ["day", "night"])
def test_selected_and_natal_palace_titles_stay_readable(monkeypatch, theme, font_path):
    chart = apply_flow(build_chart(parse_request(DEMO_BIRTH).birth), year=2026)
    palette = get_theme(theme)
    colors = []
    real_text = Painter.text

    def record(painter, value, x, y, size=20, color=None, width=None):
        real_text(painter, value, x, y, size, color, width)
        if value in PALACES:
            colors.append(color or painter.palette.ink)

    monkeypatch.setattr(Painter, "text", record)
    Renderer(font_path, theme).render(chart)
    assert len(colors) == 12
    assert all(contrast(color, palette.paper) >= 4.5 for color in colors)


@pytest.mark.parametrize("theme", ["day", "night"])
@pytest.mark.parametrize("mode", ["natal", "toddler", "decade", "annual"])
def test_dense_multilayer_chart_remains_bounded_and_keeps_all_stars(
    mode, theme, font_path
):
    r = parse_request("农历 1993-10-25 20:00 男 真太阳时=关")
    chart = build_chart(r.birth, r.profile)
    if mode == "toddler":
        chart = apply_decade(chart, 0)
    elif mode == "decade":
        chart = apply_decade(chart, 12)
    elif mode == "annual":
        chart = apply_flow(chart, year=2026)
    before = deepcopy(chart)
    view = make_chart_view(chart, theme)
    assert sum(
        len(group["stars"]) for item in view["palaces"] for group in item["groups"]
    ) == len(chart["stars"])
    png = Renderer(font_path, theme).render(chart)
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        assert image.width == 1664 and image.height < 3100
    assert len(png) < 2_000_000 and chart == before
