import math
from copy import deepcopy

import pytest

from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request
from astrbot_plugin_ziwei.engine import build_chart
from astrbot_plugin_ziwei.modern_renderer import (
    CELL_WIDTH,
    PALACE_POSITIONS,
    self_hua_arrow_layout,
    self_hua_vector,
)


def inside(point, x, y, height):
    return x <= point[0] <= x + CELL_WIDTH and y <= point[1] <= y + height


@pytest.mark.parametrize("branch", range(1, 13))
@pytest.mark.parametrize("source", ["outward", "inward"])
def test_arrows_follow_the_outer_or_inner_frame_and_point_correctly(branch, source):
    height = 416
    row, col = PALACE_POSITIONS[branch]
    x, y = 32 + col * CELL_WIDTH, 160 + row * height
    star = {"id": 1, "instance_id": "test", "self_hua": {source: "禄"}}
    arrow = self_hua_arrow_layout([star], branch, x, y, height)[0]
    assert inside(arrow["start"], x, y, height)
    assert not inside(arrow["end"], x, y, height)
    assert arrow["source"] == source and arrow["hua"] == "禄"
    dx, dy = self_hua_vector(branch, source, cell_height=height)
    assert math.isclose(math.hypot(dx, dy), 1)
    displacement = (
        arrow["end"][0] - arrow["start"][0],
        arrow["end"][1] - arrow["start"][1],
    )
    assert displacement[0] * dx + displacement[1] * dy > 0
    outer = (
        "top" if row == 0 else "bottom" if row == 3 else "left" if col == 0 else "right"
    )
    expected = (
        outer
        if source == "outward"
        else {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}[outer]
    )
    assert arrow["side"] == expected
    assert 0 <= arrow["end"][0] <= 1664
    assert 128 <= arrow["end"][1] <= 160 + 4 * height + 24


def test_multiple_arrows_keep_star_associations_and_do_not_mutate_data():
    stars = [
        {
            "id": i,
            "instance_id": f"test-{i}",
            "self_hua": {"outward": hua, "inward": hua},
        }
        for i, hua in enumerate(("禄", "权", "科", "忌"), 1)
    ]
    original = deepcopy(stars)
    arrows = self_hua_arrow_layout(stars, 6, 32, 160, 416)
    assert len(arrows) == 8
    assert len({arrow["end"] for arrow in arrows}) == 8
    assert {(a["star_id"], a["source"]) for a in arrows} == {
        (i, source) for i in range(1, 5) for source in ("outward", "inward")
    }
    assert stars == original


def test_real_chart_arrows_match_calculated_self_hua_count():
    c = build_chart(parse_request(DEMO_BIRTH).birth)
    before = deepcopy(c)
    for p in c["palaces"]:
        arrows = self_hua_arrow_layout(p["stars"], p["branch"], 32, 160, 416)
        assert len(arrows) == sum(
            bool(value) for star in p["stars"] for value in star["self_hua"].values()
        )
    assert c == before


def test_bottom_arrows_leave_palace_title_and_body_badge_clear():
    stars = [
        {
            "id": i,
            "instance_id": f"test-{i}",
            "self_hua": {"outward": hua, "inward": hua},
        }
        for i, hua in enumerate(("禄", "权", "科", "忌"), 1)
    ]
    for branch in (1, 2, 3, 12):
        for star_list in (stars[:1], stars):
            arrows = self_hua_arrow_layout(star_list, branch, 32, 160, 416)
            for arrow in arrows:
                # The centered palace title and adjacent body badge occupy this band.
                for point in (arrow["start"], arrow["end"], arrow["label_position"]):
                    assert point[0] < 32 + 164 or point[0] > 32 + 270
