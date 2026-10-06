import io
from datetime import datetime

import pytest
from PIL import Image

from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.engine import apply_flow, build_chart
from astrbot_plugin_ziwei.renderer import Renderer, find_font, text_chart
from astrbot_plugin_ziwei.rules import RuleProfile


@pytest.mark.parametrize(
    "args",
    [
        "2001-03-19 10:00 男",
        "农历 2023-闰02-16 12:00 女",
        "2024-02-09 23:30 男 真太阳时=关",
        "1988-08-08 08:08 女 经度=-74 时区=-5",
    ],
)
def test_render_decodable_bounded_image(args):
    try:
        font = find_font()
    except ValueError:
        pytest.skip("需要中文字体进行图片验收")
    r = parse_request(args)
    c = apply_flow(build_chart(r.birth, r.profile), target=datetime(2026, 10, 6, 10))
    png = Renderer(font).render(c)
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        assert image.format == "PNG" and image.mode == "RGB"
        assert 1400 <= image.width <= 1500 and image.height < 2400
        assert len(image.getcolors(maxcolors=1000000)) > 100
    assert len(png) < 2_000_000


def test_text_preserves_all_flow_layers_and_secondary_stars():
    r = parse_request("2001-03-19 10:00 男")
    text = text_chart(
        apply_flow(build_chart(r.birth), target=datetime(2026, 10, 6, 10))
    )
    for word in (
        "流年命宫",
        "流月命宫",
        "流日命宫",
        "流时命宫",
        "旬空(副)",
        "离心科",
        "童限",
        "虚岁26",
    ):
        assert word in text


def test_no_minor_stars_image():
    try:
        font = find_font()
    except ValueError:
        pytest.skip("需要中文字体进行图片验收")
    r = parse_request("2001-03-19 10:00 男")
    c = build_chart(r.birth, RuleProfile(minor_stars=False))
    assert Renderer(font).render(c).startswith(b"\x89PNG")
