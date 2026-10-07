import io
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from PIL import Image

from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request
from astrbot_plugin_ziwei.engine import apply_flow, build_chart
from astrbot_plugin_ziwei.modern_renderer import Painter
from astrbot_plugin_ziwei.renderer import Renderer, find_font
from astrbot_plugin_ziwei.themes import DAY, ELEMENTS, NIGHT, get_theme


@pytest.fixture
def font_path():
    try:
        return find_font()
    except ValueError:
        pytest.skip("需要中文字体进行图片验收")


@pytest.mark.parametrize(
    "value,expected",
    [("白天", "day"), ("day", "day"), ("夜间", "night"), ("night", "night")],
)
def test_theme_option_overrides_default_without_affecting_birth_or_rules(
    value, expected
):
    config = {"image_theme": "night"}
    before = deepcopy(config)
    r = parse_request(DEMO_BIRTH + " 主题=" + value, config)
    baseline = parse_request(DEMO_BIRTH, config)
    assert r.image_theme == expected
    assert r.birth == baseline.birth and r.profile == baseline.profile
    assert config == before and baseline.image_theme == "night"


@pytest.mark.parametrize("value", ["auto", "", "Day", 1, None, []])
def test_invalid_default_theme_is_rejected(value):
    with pytest.raises(ValueError, match="主题"):
        parse_request(DEMO_BIRTH, {"image_theme": value})


def test_stems_and_branches_are_colored_by_their_own_element():
    assert set(ELEMENTS) == set("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥")
    for p in (DAY, NIGHT):
        assert p.element_color("甲") == p.element_color("卯")
        assert p.element_color("丙") == p.element_color("午")
        assert p.element_color("戊") == p.element_color("丑") == p.element_color("未")
        assert p.element_color("辛") == p.element_color("申")
        assert p.element_color("癸") == p.element_color("子")
        assert len({p.element_color(c) for c in "甲丙戊庚壬"}) == 5


def luminance(color):
    values = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    values = [
        v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values
    ]
    return sum(
        v * weight for v, weight in zip(values, (0.2126, 0.7152, 0.0722), strict=True)
    )


def contrast(a, b):
    low, high = sorted((luminance(a), luminance(b)))
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("theme", ["day", "night"])
def test_theme_text_contrast_on_the_actual_surfaces(theme):
    p = get_theme(theme)
    for category in ("major", "assistant", "malefic", "minor"):
        assert contrast(p.star_color(category), p.paper) >= 4.5
    for surface in (p.paper, p.center, p.bg):
        assert contrast(p.ink, surface) >= 4.5
        assert contrast(p.muted, surface) >= 4.5
    assert contrast(p.on_accent, p.accent) >= 4.5
    for hua in "禄权科忌":
        fg, bg = p.hua_color(hua)
        assert contrast(fg, bg) >= 4.5
    for character in ELEMENTS:
        assert contrast(p.element_color(character), p.paper) >= 4.5


def test_parallel_day_night_rendering_cannot_mix_colors_or_mutate_chart(font_path):
    chart = build_chart(parse_request(DEMO_BIRTH).birth)
    before = deepcopy(chart)
    renderer = Renderer(font_path)
    baseline = renderer.render(chart, "day")
    with ThreadPoolExecutor(max_workers=2) as pool:
        day_future = pool.submit(renderer.render, chart, "day")
        night_future = pool.submit(renderer.render, chart, "night")
        day, night = day_future.result(), night_future.result()
    assert day == baseline and day != night
    for png, palette in ((day, DAY), (night, NIGHT)):
        with Image.open(io.BytesIO(png)) as im:
            assert im.getpixel((0, 0)) == tuple(
                int(palette.bg[i : i + 2], 16) for i in (1, 3, 5)
            )
    assert chart == before and renderer.theme == "day"


@pytest.mark.parametrize("theme", ["day", "night"])
@pytest.mark.parametrize("case", ["ordinary", "dense", "crowded"])
def test_palace_arrows_do_not_cover_any_text_or_neighbor(
    monkeypatch, theme, case, font_path
):
    r = parse_request(
        "农历 1993-10-25 20:00 男 真太阳时=关" if case == "dense" else DEMO_BIRTH
    )
    chart = apply_flow(build_chart(r.birth, r.profile), year=2026)
    if case == "crowded":
        # Stress both side strips with four arrows each, independent of rules.
        for star, hua in zip(chart["palaces"][4]["stars"], "禄权科忌", strict=False):
            star["self_hua"] = {"outward": hua, "inward": hua}
    texts, arrows = [], []
    real_text, real_arrow = Painter.text, Painter.arrow

    def text(painter, value, x, y, size=20, color=None, width=None):
        real_text(painter, value, x, y, size, color, width)
        while (
            width is not None
            and painter.font(size).getlength(value) > width
            and size > 14
        ):
            size -= 1
        box = painter.font(size).getbbox(value, anchor="lt")
        texts.append((x, y, x + box[2], y + box[3], value))

    def arrow(painter, start, end, color, **kwargs):
        real_arrow(painter, start, end, color, **kwargs)
        arrows.append(
            (
                min(start[0], end[0]) - 3,
                min(start[1], end[1]) - 3,
                max(start[0], end[0]) + 3,
                max(start[1], end[1]) + 3,
            )
        )

    monkeypatch.setattr(Painter, "text", text)
    monkeypatch.setattr(Painter, "arrow", arrow)
    Renderer(font_path, theme).render(chart)
    assert len(arrows) == sum(
        bool(hua)
        for palace in chart["palaces"]
        for star in palace["stars"]
        for hua in star["self_hua"].values()
    )
    assert not any(
        label in {"主星", "辅星", "煞星", "杂曜", "生禄", "年禄"} for *_, label in texts
    )
    for a in arrows:
        for b in texts:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)
