"""Fictional charts and explicitly synthetic windows exercise real layout paths."""

import io
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from PIL import Image

from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.engine import build_chart
from astrbot_plugin_ziwei.life_atlas import build_life_atlas
from astrbot_plugin_ziwei.life_atlas_renderer import LifeAtlasRenderer


@pytest.fixture(scope="module")
def renderer():
    try:
        return LifeAtlasRenderer()
    except ValueError:
        pytest.skip("需要中文字体进行图片验收")


def synthetic_atlas(end_age=64, windows=()):
    """These fabricated hits test typography, not Ziwei interpretation."""
    first_decade = end_age - 59
    decades = [{"index": 0, "age_start": 1, "age_end": first_decade - 1}]
    decades.extend(
        {"index": index + 1, "age_start": age, "age_end": age + 9}
        for index, age in enumerate(range(first_decade, end_age, 10))
    )
    return {
        "start_age": 1,
        "end_age": end_age,
        "start_year": 1988,
        "end_year": 1988 + end_age - 1,
        "endpoint_decade": decades[-1],
        "decades": decades,
        "natal": {
            "sex": "女",
            "bureau_name": "布局测试局",
            "normalized": {
                "civil": "1988-08-08 08:08:00",
                "true_solar": False,
                "original": {"timezone": -5, "longitude": -74},
            },
        },
        "windows": list(windows),
    }


def synthetic_window(index, age, *, topic="career", mixed=False):
    def path(star, hua, source):
        return {
            "source_branch": source,
            "source_stem": 1,
            "star": star,
            "hua": hua,
        }

    return {
        "id": f"{index:02d}",
        "start_age": age,
        "end_age": age,
        "start_year": 1988 + age - 1,
        "end_year": 1988 + age - 1,
        "topic": topic,
        "mixed": mixed,
        "hits": [
            {
                "age": age,
                "year": 1988 + age - 1,
                "target_branch": 7,
                "target_decade_palace": "官禄",
                "target_natal_palace": "田宅",
                "natal_major_stars": [{"name": "紫微", "secondary": False}],
                "decade_paths": [path("天机", "禄", 3)],
                "annual_paths": [path("太阴", "忌" if mixed else "科", 5)],
            }
        ],
    }


def all_text(audit):
    return "\n".join(item["text"] for item in audit["text_boxes"])


def assert_clean_layout(audit):
    assert not audit["text_collisions"]
    assert not audit["out_of_bounds"]
    width, height = audit["size"]
    for node in audit["node_boxes"]:
        x1, y1, x2, y2 = node["box"]
        assert 0 <= x1 < x2 <= width
        assert 0 <= y1 < y2 <= height


@pytest.mark.parametrize("end_age", [61, 62, 63, 64, 65])
def test_final_year_and_age_are_visible_without_tick_collisions(renderer, end_age):
    atlas = synthetic_atlas(end_age, [synthetic_window(1, end_age)])
    audit = renderer.layout_audit(atlas)
    assert_clean_layout(audit)
    texts = {item["text"] for item in audit["text_boxes"]}
    assert str(end_age) in texts
    assert "60" in texts
    assert str(atlas["end_year"]) in texts
    assert f"{end_age}岁" in texts
    assert f"60岁所在大运：{end_age - 9}—{end_age}岁" in texts
    assert "1988-08-08 08:08:00" in all_text(audit)
    assert "布局测试局" in all_text(audit)
    assert "虚构" not in all_text(audit)
    assert audit["rendered_window_ids"] == ["01"]


def test_dense_same_age_windows_get_more_space_and_retain_every_path(renderer):
    windows = [synthetic_window(i, 20 + i % 2, mixed=True) for i in range(1, 37)]
    atlas = synthetic_atlas(windows=windows)
    audit = renderer.layout_audit(atlas)
    assert_clean_layout(audit)
    assert len(audit["node_boxes"]) == 36
    assert set(audit["rendered_window_ids"]) == {w["id"] for w in windows}
    assert audit["size"][1] > renderer.layout_audit(synthetic_atlas())["size"][1]
    text = all_text(audit)
    assert text.count("天机禄 → 大官禄·午") == 36
    assert text.count("太阴忌 → 大官禄·午") == 36
    assert text.count("大命·甲寅") == 36
    assert text.count("流命·甲辰") == 36
    nodes = audit["node_boxes"]
    for index, first in enumerate(nodes):
        x1, y1, x2, y2 = first["box"]
        for second in nodes[index + 1 :]:
            a, b, c, d = second["box"]
            assert min(x2, c) <= max(x1, a) or min(y2, d) <= max(y1, b)


def test_merged_window_retains_different_sources_and_long_wrapped_explanations(
    renderer,
):
    window = synthetic_window(1, 20)
    window.update(end_age=23, end_year=2010)
    for age in range(21, 24):
        hit = deepcopy(window["hits"][0])
        hit["age"] = age
        hit["annual_paths"][0]["source_branch"] = (age - 11) % 12 + 1
        hit["annual_paths"].extend(
            [
                {**hit["annual_paths"][0], "star": "天机", "hua": "禄"},
                {**hit["annual_paths"][0], "star": "武曲", "hua": "权"},
                {**hit["annual_paths"][0], "star": "贪狼", "hua": "忌"},
            ]
        )
        window["hits"].append(hit)
    audit = renderer.layout_audit(synthetic_atlas(windows=[window]))
    assert_clean_layout(audit)
    text = all_text(audit)
    assert "流命·甲戌" in text
    assert "流命·甲亥" in text
    assert "流命·甲子" in text
    for age in range(20, 24):
        assert f"[{age}岁]" in text.replace("\n", "")


def test_no_window_still_renders_complete_timeline(renderer):
    atlas = synthetic_atlas(61)
    audit = renderer.layout_audit(atlas, demo=True)
    assert_clean_layout(audit)
    assert not audit["rendered_window_ids"]
    assert "本次结构筛选未形成候选窗口" in all_text(audit)
    assert "童限" in all_text(audit)
    assert "虚构资料预览" in all_text(audit)
    with Image.open(io.BytesIO(renderer.render(atlas))) as image:
        image.load()
        assert image.mode == "RGB"
        assert list(image.size) == renderer.layout_audit(atlas)["size"]


def test_themes_are_request_local_even_with_shared_renderer(renderer):
    atlas = synthetic_atlas(windows=[synthetic_window(1, 20)])
    original = deepcopy(atlas)
    night_before = renderer.render(atlas)
    with ThreadPoolExecutor(max_workers=2) as pool:
        day, night = list(
            pool.map(lambda theme: renderer.render(atlas, theme), ("day", "night"))
        )
    assert night_before == night == renderer.render(atlas)
    assert day != night
    assert atlas == original
    assert_clean_layout(renderer.layout_audit(atlas, "day"))
    with Image.open(io.BytesIO(day)) as light, Image.open(io.BytesIO(night)) as dark:
        assert sum(light.getpixel((0, 0))) > sum(dark.getpixel((0, 0)))


def test_actual_fictional_chart_preserves_all_windows_in_both_themes(renderer):
    request = parse_request("1990-06-15 08:30 男")
    atlas = build_life_atlas(build_chart(request.birth, request.profile))
    for theme in ("day", "night"):
        audit = renderer.layout_audit(atlas, theme)
        assert_clean_layout(audit)
        assert set(audit["rendered_window_ids"]) == {w["id"] for w in atlas["windows"]}
        assert len(audit["rendered_window_ids"]) == len(atlas["windows"])
        assert "01 — 64" in all_text(audit)
        assert "《河洛紫微斗数》" in all_text(audit)
        assert "虚构" not in all_text(audit)
