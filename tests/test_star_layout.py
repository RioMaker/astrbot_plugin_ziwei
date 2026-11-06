"""Check rendered glyph geometry and content, including crowded palaces."""

import io
from copy import deepcopy

import pytest
from PIL import Image

from astrbot_plugin_ziwei import modern_renderer as drawing
from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request
from astrbot_plugin_ziwei.engine import apply_flow, build_chart
from astrbot_plugin_ziwei.renderer import Renderer, find_font


@pytest.mark.parametrize("theme", ["day", "night"])
@pytest.mark.parametrize("school", ["wm259", "xunlongzhe"])
@pytest.mark.parametrize(
    "birth,year,legacy_side,course_side",
    [
        (DEMO_BIRTH, 2020, 400, 400),  # Three Hua source badges at the same star.
        ("农历 1993-10-25 20:00 男 真太阳时=关", 2026, 480, 500),
        ("1988-08-08 08:08 女 经度=-74 时区=-5", 2026, 520, 480),
    ],
)
def test_large_star_columns_keep_every_glyph_aligned_and_clear(
    monkeypatch, theme, school, birth, year, legacy_side, course_side
):
    try:
        font_path = find_font()
    except ValueError:
        pytest.skip("需要中文字体进行图片验收")
    request = parse_request(birth, {"rule_school": school})
    expected_side = legacy_side if school == "wm259" else course_side
    chart = apply_flow(build_chart(request.birth, request.profile), year=year)
    before = deepcopy(chart)
    records, palaces = [], {}
    active_star, active_palace = None, None
    real_text = drawing.Painter.text
    real_star, real_palace = drawing._vertical_star, drawing._draw_palace

    def text(painter, value, x, y, size=20, color=None, width=None):
        real_text(painter, value, x, y, size, color, width)
        if active_star is not None:
            left, top, right, bottom = painter.font(size).getbbox(value, anchor="lt")
            active_star["glyphs"].append(
                (value, size, color, (x + left, y + top, x + right, y + bottom))
            )

    def star(painter, value, x, y, color, step):
        nonlocal active_star
        active_star = {
            "star": value,
            "palace": active_palace,
            "top": y,
            "glyphs": [],
        }
        real_star(painter, value, x, y, color, step)
        records.append(active_star)
        active_star = None

    def palace(painter, item, chart, x, y, height, *, width):
        nonlocal active_palace
        active_palace = item["palace"]["branch"]
        palaces[active_palace] = (x, y, x + width, y + height)
        real_palace(painter, item, chart, x, y, height, width=width)
        active_palace = None

    monkeypatch.setattr(drawing.Painter, "text", text)
    monkeypatch.setattr(drawing, "_vertical_star", star)
    monkeypatch.setattr(drawing, "_draw_palace", palace)
    png = Renderer(font_path, theme).render(chart)
    assert chart == before
    assert sorted(record["star"]["instance_id"] for record in records) == sorted(
        value["instance_id"] for value in chart["stars"]
    )
    for record in records:
        value, glyphs = record["star"], record["glyphs"]
        names = glyphs[: len(value["name"])]
        assert "".join(glyph[0] for glyph in names) == value["name"]
        assert all(glyph[1] == 36 for glyph in names)
        centers = [(box[0] + box[2]) / 2 for *_, box in glyphs]
        assert max(centers) - min(centers) <= 1
        assert names[1][3][1] - names[0][3][1] == 40
        assert sum(glyph[0] == "副" for glyph in glyphs) == bool(value["secondary"])
        annotations = glyphs[len(names) + int(value["secondary"]) :]
        assert [glyph[0] for glyph in annotations] == (
            ([value["brightness"]] if value["brightness"] else [])
            + [mark["label"] for mark in value["marks"]]
        )
        x, y, right, bottom = palaces[record["palace"]]
        for *_, box in glyphs:
            assert x < box[0] < box[2] < right
            # Reserve the top strip for self-Hua labels, and clear the footer.
            assert y + 18 <= box[1] < box[3] < bottom - 156
        neighbors = [r for r in records if r["palace"] == record["palace"]]
        for neighbor in neighbors:
            for glyph in glyphs:
                for other in neighbor["glyphs"]:
                    if glyph is other:
                        continue
                    a, b = glyph[3], other[3]
                    assert (
                        a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
                    ), (value["instance_id"], glyph, other)
    # Equal squares stay compact even when the densest palace wraps.
    assert len(palaces) == 12
    assert {(right - x, bottom - y) for x, y, right, bottom in palaces.values()} == {
        (expected_side, expected_side)
    }
    for branch in palaces:
        stars = [record for record in records if record["palace"] == branch]
        tops = sorted({record["top"] for record in stars})
        for top, following in zip(tops, tops[1:], strict=False):
            bottom = max(
                glyph[3][3]
                for record in stars
                if record["top"] == top
                for glyph in record["glyphs"]
            )
            assert 8 <= following - bottom <= 24
    with Image.open(io.BytesIO(png)) as image:
        assert image.height < 2700
