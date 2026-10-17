"""Complete vertical candidate geometry and pixels, including stress data."""

import io
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from PIL import Image, ImageColor, ImageDraw

from astrbot_plugin_ziwei.rectification import build_candidates
from astrbot_plugin_ziwei.rectification_commands import parse_rectification_request
from astrbot_plugin_ziwei.rectification_renderer import (
    MAX_PIXELS,
    plan_candidate_collage,
    render_candidate_collage,
)
from astrbot_plugin_ziwei.renderer import Renderer, find_font
from astrbot_plugin_ziwei.rules import BRANCHES
from astrbot_plugin_ziwei.themes import get_theme


@pytest.fixture
def font_path():
    try:
        return find_font()
    except ValueError:
        pytest.skip("需要中文字体进行总图验收")


def stress_candidates(count):
    """Synthetic IDs and date ranges for layout stress, never actual birth cases."""
    start = datetime(1990, 6, 15)
    candidates = []
    for number in range(count):
        instant = start + timedelta(minutes=number * 2)
        intervals = [
            {
                "start": instant.isoformat(sep=" "),
                "end": (instant + timedelta(minutes=1)).isoformat(sep=" "),
            }
        ]
        candidates.append(
            {
                "id": f"c{number + 1:02}",
                "intervals": intervals,
                "chart": {
                    "number": number,
                    "normalized": {
                        "civil": intervals[0]["start"],
                        "hour_branch": number % 12 + 1,
                    },
                },
            }
        )
    return candidates


def pattern_png(size, number):
    with Image.new("RGB", size, (70 + number, 110 + number, 150 + number)) as image:
        draw = ImageDraw.Draw(image)
        w, h = size
        draw.rectangle((0, 0, 35, 35), fill="#ff0000")
        draw.rectangle((w - 36, 0, w - 1, 35), fill="#00ff00")
        draw.rectangle((0, h - 36, 35, h - 1), fill="#0000ff")
        draw.rectangle((w - 36, h - 36, w - 1, h - 1), fill="#ffff00")
        draw.line((0, h // 2, w - 1, h // 2), fill="#000000", width=3)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


class PatternRenderer:
    def __init__(self, font_path, sizes):
        self.font_path, self.sizes = font_path, sizes
        self.calls = []
        self.sources = []

    def render(self, chart, theme):
        number = chart["number"]
        self.calls.append((number, theme))
        png = pattern_png(self.sizes[number], number)
        self.sources.append(png)
        return png


def assert_geometry(plan, candidates):
    assert [cell["candidate_id"] for cell in plan["cells"]] == [
        c["id"] for c in candidates
    ]
    assert len(plan["cells"]) == len(candidates)
    width, height = plan["size"]
    assert width <= 1680 and width * height <= MAX_PIXELS
    assert plan["columns"] == 1 and plan["rows"] == len(candidates)
    assert plan["empty_cells"] == []
    for cell, candidate in zip(plan["cells"], candidates, strict=True):
        assert cell["intervals"] == candidate["intervals"]
        x1, y1, x2, y2 = cell["box"]
        a, b, c, d = cell["image_box"]
        assert 0 <= x1 <= a < c <= x2 <= width
        assert 0 <= y1 < b < d <= y2 <= height
        assert [c - a, d - b] == cell["image_size"]
        branch = BRANCHES[candidate["chart"]["normalized"]["hour_branch"] - 1]
        assert cell["texts"][0]["text"] == f"候选 {candidate['id']} · 计算{branch}时"
        for text in cell["texts"]:
            a, b, c, d = text["box"]
            assert x1 <= a < c <= x2
            assert y1 <= b < d <= cell["header_box"][3]
    for i, first in enumerate(plan["cells"]):
        a, b, c, d = first["box"]
        for second in plan["cells"][i + 1 :]:
            x1, y1, x2, y2 = second["box"]
            assert min(c, x2) <= max(a, x1) or min(d, y2) <= max(b, y1)


def test_32_candidate_plan_preserves_ids_intervals_and_variable_heights(font_path):
    candidates = stress_candidates(32)
    candidates[0]["intervals"] = [
        {"start": "1990-06-15 00:00:00", "end": "1990-06-15 01:00:00"},
        {"start": "1990-06-15 23:00:00", "end": "1990-06-16 00:00:00"},
    ]
    sizes = [(1664, 1730 if index % 2 == 0 else 2490) for index in range(32)]
    plan = plan_candidate_collage(candidates, sizes, font_path)
    assert_geometry(plan, candidates)
    assert plan["columns"] == 1 and plan["rows"] == 32
    assert 1200 <= plan["cell_width"] < 1664
    assert plan["empty_cells"] == []
    text = "\n".join(item["text"] for item in plan["cells"][0]["texts"])
    assert "1990-06-16 00:00:00" in text and "01:00:00" in text
    assert "非确定出生时间" in text


def test_native_vertical_layout_keeps_pixels_and_visible_title_separators(font_path):
    candidates = stress_candidates(5)
    sizes = [(1664, 300), (1664, 600), (1664, 400), (1664, 350), (1664, 320)]
    renderer = PatternRenderer(font_path, sizes)
    png = render_candidate_collage(candidates, renderer)
    plan = plan_candidate_collage(candidates, sizes, font_path)
    assert_geometry(plan, candidates)
    assert plan["empty_cells"] == []
    assert renderer.calls == [(index, "day") for index in range(5)]
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        assert image.size == tuple(plan["size"])
        for cell, source in zip(plan["cells"], renderer.sources, strict=True):
            with Image.open(io.BytesIO(source)) as original:
                with image.crop(tuple(cell["image_box"])) as pasted:
                    assert pasted.size == original.size
                    assert pasted.tobytes() == original.tobytes()
        for cell in plan["cells"]:
            box = cell["header_box"]
            assert image.getpixel((box[2] - 5, box[1] + 5)) == ImageColor.getrgb(
                get_theme("day").accent
            )
        for previous, current in zip(plan["cells"], plan["cells"][1:], strict=False):
            assert previous["box"][0] == current["box"][0]
            assert current["box"][1] - previous["box"][3] >= 24
            y = (current["box"][1] + previous["box"][3]) // 2
            assert image.getpixel((20, y)) == ImageColor.getrgb(get_theme("day").bg)
    assert len(png) < 2_000_000


def test_32_dense_tiles_remain_complete_after_bounded_proportional_scaling(font_path):
    candidates = stress_candidates(32)
    sizes = [(1664, 2490)] * 32
    renderer = PatternRenderer(font_path, sizes)
    png = render_candidate_collage(candidates, renderer, "night")
    plan = plan_candidate_collage(candidates, sizes, font_path)
    assert_geometry(plan, candidates)
    assert len(renderer.calls) == 32
    assert renderer.calls == [(index, "night") for index in range(32)]
    assert plan["cell_width"] >= 1200
    assert len(png) < 10_000_000
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        assert image.format == "PNG" and image.mode == "RGB"
        assert image.size == tuple(plan["size"])
        for cell in plan["cells"]:
            x1, y1, x2, y2 = cell["image_box"]
            # All four original corners survive every cell, including row 32.
            assert image.getpixel((x1 + 4, y1 + 4)) == (255, 0, 0)
            assert image.getpixel((x2 - 5, y1 + 4)) == (0, 255, 0)
            assert image.getpixel((x1 + 4, y2 - 5)) == (0, 0, 255)
            assert image.getpixel((x2 - 5, y2 - 5)) == (255, 255, 0)
        for index in (0, 15, 31):
            cell = plan["cells"][index]
            with Image.open(io.BytesIO(renderer.sources[index])) as original:
                with original.resize(
                    tuple(cell["image_size"]), Image.Resampling.LANCZOS
                ) as expected:
                    with image.crop(tuple(cell["image_box"])) as pasted:
                        assert pasted.tobytes() == expected.tobytes()


def test_real_dense_candidate_charts_and_themes_stay_independent(font_path):
    candidates = build_candidates(
        parse_rectification_request("农历 1993-10-25 男 时辰=酉,戌 真太阳时=关")
    )
    before = deepcopy(candidates)
    renderer = Renderer(font_path)
    original = renderer.render
    captured = []

    def capture(chart, theme):
        png = original(chart, theme)
        captured.append(png)
        return png

    renderer.render = capture
    outputs = {}
    for theme in ("day", "night"):
        captured.clear()
        png = render_candidate_collage(candidates, renderer, theme)
        sizes = []
        for source in captured:
            with Image.open(io.BytesIO(source)) as tile:
                sizes.append(tile.size)
        plan = plan_candidate_collage(candidates, sizes, font_path)
        assert_geometry(plan, candidates)
        assert max(h for _, h in sizes) > 2000
        with Image.open(io.BytesIO(png)) as image:
            assert image.getpixel((0, 0)) == ImageColor.getrgb(get_theme(theme).bg)
            for cell, source in zip(plan["cells"], captured, strict=True):
                header = cell["header_box"]
                assert image.getpixel(
                    (header[2] - 5, header[1] + 5)
                ) == ImageColor.getrgb(get_theme(theme).accent)
                with image.crop(tuple(header)) as banner:
                    colors = {color for _, color in banner.getcolors(1000000)}
                    assert ImageColor.getrgb(get_theme(theme).on_accent) in colors
                with Image.open(io.BytesIO(source)) as full_chart:
                    with image.crop(tuple(cell["image_box"])) as pasted:
                        assert pasted.size == full_chart.size
                        assert pasted.tobytes() == full_chart.tobytes()
        outputs[theme] = png
        assert len(png) < 4_000_000
    assert outputs["day"] != outputs["night"]
    assert candidates == before and renderer.theme == "day"


def test_unsupported_huge_layout_rejects_whole_image_instead_of_dropping_tiles(
    font_path,
):
    with pytest.raises(ValueError, match="超过8000万像素"):
        plan_candidate_collage(stress_candidates(32), [(1664, 8000)] * 32, font_path)


@pytest.mark.parametrize(
    "kind", ["empty", "too_many", "duplicate", "no_range", "bad_range"]
)
def test_invalid_candidates_are_rejected_before_rendering(font_path, kind):
    candidates = stress_candidates(2)
    if kind == "empty":
        candidates = []
    elif kind == "too_many":
        candidates = stress_candidates(33)
    elif kind == "duplicate":
        candidates[1]["id"] = candidates[0]["id"]
    elif kind == "no_range":
        candidates[0]["intervals"] = []
    else:
        candidates[0]["intervals"][0]["end"] = "not-a-date"
    renderer = PatternRenderer(font_path, [(1664, 1730)] * len(candidates))
    with pytest.raises(ValueError):
        render_candidate_collage(candidates, renderer)
    assert not renderer.calls


def test_corrupt_chart_does_not_return_partial_collage(font_path):
    renderer = PatternRenderer(font_path, [(1664, 1730)])
    renderer.render = lambda chart, theme: b"invalid PNG"
    with pytest.raises(ValueError, match="未生成不完整总图"):
        render_candidate_collage(stress_candidates(1), renderer)
