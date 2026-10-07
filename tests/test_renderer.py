import io
from copy import deepcopy
from datetime import datetime

import pytest
from PIL import Image

from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.engine import apply_flow, build_chart
from astrbot_plugin_ziwei.modern_renderer import make_chart_view, star_marks
from astrbot_plugin_ziwei.renderer import Renderer, find_font, text_chart
from astrbot_plugin_ziwei.rules import RuleProfile


@pytest.mark.parametrize(
    "args",
    [
        "1990-06-15 08:30 男",
        "农历 2023-闰02-16 12:00 女",
        "2024-02-09 23:30 男 真太阳时=关",
        "1988-08-08 08:08 女 经度=-74 时区=-5",
        "农历 1993-10-25 20:00 男 真太阳时=关",
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
        assert 1600 <= image.width <= 1750 and image.height < 3100
        assert len(image.getcolors(maxcolors=1000000)) > 100
    assert len(png) < 2_000_000


def test_text_preserves_all_flow_layers_and_secondary_stars():
    r = parse_request("1990-06-15 08:30 男")
    text = text_chart(
        apply_flow(build_chart(r.birth), target=datetime(2026, 10, 6, 10))
    )
    for word in (
        "流年命宫",
        "流月命宫",
        "流日命宫",
        "流时命宫",
        "旬空(副)",
        "离心忌",
        "童限",
        "虚岁37",
    ):
        assert word in text


def test_no_minor_stars_image():
    try:
        font = find_font()
    except ValueError:
        pytest.skip("需要中文字体进行图片验收")
    r = parse_request("1990-06-15 08:30 男")
    c = build_chart(r.birth, RuleProfile(minor_stars=False))
    assert Renderer(font).render(c).startswith(b"\x89PNG")


def test_star_groups_and_transformation_marks_do_not_mutate_calculation():
    r = parse_request("1990-06-15 08:30 男")
    chart = apply_flow(build_chart(r.birth), year=2026)
    before = deepcopy(chart)
    view = make_chart_view(chart)
    assert chart == before
    displayed = {
        star["instance_id"]: (star, group["key"])
        for item in view["palaces"]
        for group in item["groups"]
        for star in group["stars"]
    }
    assert len(displayed) == len(chart["stars"]) == 70
    for star, category in displayed.values():
        if star["id"] in {23, 24, 25, 26, 27, 28, 54, 80}:
            assert category == "malefic"
        elif star["id"] <= 14:
            assert category == "major"
        elif star["id"] in {15, 16, 17, 18, 19, 20, 21, 22}:
            assert category == "assistant"
    assert displayed["natal-33-primary"][1] == "minor"
    assert displayed["natal-16-primary"][1] == "assistant"
    # 丙年文昌化科；its assistant color remains independent of the Hua mark.
    assert displayed["natal-15-primary"][1] == "assistant"
    assert displayed["natal-15-primary"][0]["marks"]
    assert "natal-78-secondary" in displayed


def test_four_hua_sources_and_self_transformations_remain_separate():
    star = {
        "hua": {"生年": "禄", "流年": "忌", "大限": "科"},
        "self_hua": {"outward": "权", "inward": "科"},
    }
    marks = star_marks(star)
    assert [mark["label"] for mark in marks] == ["生禄", "年忌", "离权", "向科"]
    assert [mark["source"] for mark in marks] == ["生年", "流年", "outward", "inward"]
    assert [mark["self"] for mark in marks] == [False, False, True, True]


def test_annual_year_series_maps_to_the_correct_branch_and_age():
    r = parse_request("1990-06-15 08:30 男")
    view = make_chart_view(build_chart(r.birth))
    wu = view["palaces"][6]
    assert wu["annual_years"] == [1990, 2002, 2014, 2026, 2038]
    assert wu["annual_ages"] == [1, 13, 25, 37, 49]
    for item in view["palaces"]:
        assert all(
            (year - 4) % 12 + 1 == item["palace"]["branch"]
            for year in item["annual_years"]
        )
        assert item["annual_selected"] is False


# 2099 己未与 2027 丁未相差 72 年，二者流年均落未宫。
@pytest.mark.parametrize("year,branch", [(2026, 7), (2027, 8), (2099, 8)])
def test_selected_annual_year_has_one_highlight_and_stays_in_the_series(year, branch):
    r = parse_request("1990-06-15 08:30 男")
    view = make_chart_view(apply_flow(build_chart(r.birth), year=year))
    selected = [item for item in view["palaces"] if item["annual_selected"]]
    assert len(selected) == 1
    assert selected[0]["palace"]["branch"] == branch
    assert year in selected[0]["annual_years"]
    assert str(year) in selected[0]["annual_label"]


def test_spring_boundary_labels_lunar_flow_year_instead_of_civil_year():
    r = parse_request("1990-06-15 08:30 男")
    c = apply_flow(build_chart(r.birth), target=datetime(2024, 2, 9, 12))
    view = make_chart_view(c)
    selected = next(item for item in view["palaces"] if item["annual_selected"])
    assert selected["palace"]["branch"] == 4
    assert selected["annual_label"].startswith("2023")
    assert 2023 in selected["annual_years"] and 2024 not in selected["annual_years"]
