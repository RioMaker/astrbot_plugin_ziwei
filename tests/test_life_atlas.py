"""Fictional fixtures for P1 structure, never evidence of real-life prediction."""

import json
from copy import deepcopy
from datetime import datetime

import pytest

from astrbot_plugin_ziwei import life_atlas
from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.engine import apply_decade, apply_flow, build_chart
from astrbot_plugin_ziwei.life_atlas import build_life_atlas
from astrbot_plugin_ziwei.rules import RuleProfile


def natal(command="1990-06-15 08:30 男", profile=None):
    request = parse_request(command)
    return build_chart(request.birth, profile or request.profile)


@pytest.fixture(scope="module")
def example():
    return build_life_atlas(natal())


def hit_map(atlas):
    return {hit["age"]: hit for window in atlas["windows"] for hit in window["hits"]}


def test_fictional_preview_windows_have_fixed_ages_topics_and_boundaries(example):
    assert [
        (window["start_age"], window["end_age"], window["topic"])
        for window in example["windows"]
    ] == [
        (5, 5, "study"),
        (7, 7, "study"),
        (12, 12, "study"),
        (14, 14, "study"),
        (16, 16, "relationship"),
        (20, 20, "relationship"),
        (37, 37, "home"),
        (38, 38, "mobility"),
        (40, 40, "home"),
        (42, 42, "mobility"),
        (44, 44, "home"),
        (46, 47, "relationship"),
        (51, 51, "relationship"),
        (55, 58, "home"),
        (60, 60, "career"),
    ]
    assert (example["start_age"], example["end_age"]) == (1, 64)
    assert (example["start_year"], example["end_year"]) == (1990, 2053)
    assert example["endpoint_decade"]["age_start"] == 55
    assert example["endpoint_decade"]["age_end"] == 64
    assert example["scan_summary"]["scanned_years"] == 64
    assert example["scan_summary"]["annual_hits"] == 19
    assert example["scan_summary"]["window_count"] == 15
    assert "annual_facts" not in example
    assert len(json.dumps(example, ensure_ascii=False).encode("utf-8")) < 150_000
    assert [window["id"] for window in example["windows"]] == [
        f"{number:02}" for number in range(1, 16)
    ]


@pytest.mark.parametrize(
    "age,target,decade_path,annual_paths",
    [
        (5, 7, "右弼科", {"文昌科"}),
        (7, 7, "右弼科", {"右弼科"}),
        (12, 7, "右弼科", {"文昌忌"}),
        (14, 7, "右弼科", {"破军禄"}),
        (16, 2, "天梁科", {"天梁权"}),
        (20, 2, "天梁科", {"天梁科"}),
        (37, 9, "文曲科", {"紫微权", "左辅科"}),
        (38, 12, "巨门禄", {"巨门权"}),
        (40, 9, "文曲科", {"紫微科"}),
        (42, 12, "巨门禄", {"巨门忌"}),
        (44, 9, "文曲科", {"文曲忌"}),
        (46, 5, "武曲忌", {"武曲禄"}),
        (47, 5, "武曲忌", {"武曲权"}),
        (51, 5, "武曲忌", {"武曲科"}),
        (55, 11, "贪狼忌", {"贪狼禄"}),
        (56, 11, "贪狼忌", {"贪狼权"}),
        (57, 11, "贪狼忌", {"贪狼禄"}),
        (58, 11, "贪狼忌", {"贪狼权"}),
        (60, 12, "巨门权", {"巨门禄"}),
    ],
)
def test_fictional_preview_actual_flight_paths(
    example, age, target, decade_path, annual_paths
):
    hit = hit_map(example)[age]
    dp, ap = hit["decade_paths"], hit["annual_paths"]
    assert hit["target_branch"] == target
    assert {path["star"] + path["hua"] for path in dp} == {decade_path}
    assert {path["star"] + path["hua"] for path in ap} == annual_paths
    assert dp[0]["source_branch"] != ap[0]["source_branch"]
    assert all(path["target_branch"] == target for path in dp + ap)
    assert all(path["source_names"]["大限"] == "命宫" for path in dp)
    assert all(path["source_names"]["流年"] == "命宫" for path in ap)
    assert all(1 <= path["source_branch"] <= 12 for path in dp + ap)
    assert hit["year"] == 1989 + age


def test_merge_keeps_contiguous_years_within_same_decade_and_target(example):
    seen = set()
    for window in example["windows"]:
        hits = window["hits"]
        assert [hit["age"] for hit in hits] == list(
            range(window["start_age"], window["end_age"] + 1)
        )
        assert (
            len({(h["topic"], h["decade_index"], h["target_branch"]) for h in hits})
            == 1
        )
        for hit in hits:
            key = (hit["age"], hit["target_branch"])
            assert key not in seen
            seen.add(key)
            assert hit["decade_index"] > 0
            assert hit["interpretation_status"] == "structural_candidate_only"
            assert hit["conditions_status"] == "literature_context_not_fully_evaluated"
    assert [w["id"] for w in example["windows"] if w["mixed"]] == [
        "03",
        "10",
        "11",
        "12",
        "13",
        "14",
    ]


@pytest.mark.parametrize(
    "clock,bureau,end_age",
    [
        ("17:30", 2, 61),
        ("00:30", 3, 62),
        ("01:30", 4, 63),
        ("05:30", 5, 64),
        ("09:30", 6, 65),
    ],
)
def test_endpoint_is_end_of_actual_decade_containing_sixty(clock, bureau, end_age):
    chart = natal(f"1990-06-15 {clock} 男")
    assert chart["bureau"] == bureau
    atlas = build_life_atlas(chart)
    assert atlas["end_age"] == end_age
    assert atlas["end_year"] == 1990 + end_age - 1
    assert atlas["endpoint_decade"]["age_start"] <= 60 <= end_age
    assert atlas["decades"][-1] == atlas["endpoint_decade"]


@pytest.mark.parametrize(
    "command,profile,expected_year",
    [
        ("1990-01-26 12:00 男 真太阳时=关", RuleProfile(), 1989),
        ("1990-01-27 12:00 男 真太阳时=关", RuleProfile(), 1990),
        ("1990-01-26 23:30 男 真太阳时=关", RuleProfile(), 1990),
        ("1990-01-26 23:30 男 真太阳时=关", RuleProfile(late_zi_rule="midnight"), 1989),
    ],
)
def test_lunar_new_year_and_late_zi_boundary(command, profile, expected_year):
    atlas = build_life_atlas(natal(command, profile))
    assert atlas["start_year"] == expected_year
    assert atlas["end_year"] == expected_year + atlas["end_age"] - 1
    assert all(
        hit["year"] == expected_year + hit["age"] - 1
        for window in atlas["windows"]
        for hit in window["hits"]
    )


@pytest.mark.parametrize("command", ["1900-01-15 12:00 男", "2038-06-15 01:30 男"])
def test_complete_out_of_range_scan_fails_before_any_annual_calculation(
    command, monkeypatch
):
    chart = natal(command)
    monkeypatch.setattr(
        life_atlas, "apply_flow", lambda *a, **kw: pytest.fail("不能开始截断扫描")
    )
    monkeypatch.setattr(
        life_atlas, "build_facts", lambda *a, **kw: pytest.fail("应先检查完整年份范围")
    )
    with pytest.raises(ValueError, match="超出支持的 1900—2100 年范围"):
        build_life_atlas(chart)


def test_supported_last_year_is_included_without_truncating():
    atlas = build_life_atlas(natal("2037-06-15 17:30 男"))
    assert atlas["end_year"] == 2100
    assert atlas["end_age"] == 64
    assert atlas["scan_summary"]["scanned_years"] == 64


@pytest.mark.parametrize("mode", ["natal", "decade", "date"])
def test_input_and_old_flow_are_detached_and_cleaned(mode, example):
    chart = natal()
    if mode == "decade":
        chart = apply_decade(chart, 3)
    elif mode == "date":
        chart = apply_flow(chart, target=datetime(2026, 10, 6, 10))
    # Persisted charts lose shared references between global and palace stars.
    chart = json.loads(json.dumps(chart))
    before = deepcopy(chart)
    atlas = build_life_atlas(chart)
    assert chart == before
    assert atlas == example
    assert atlas["natal"]["flow"] is None
    assert all(not palace["flow_names"] for palace in atlas["natal"]["palaces"])
    for star in atlas["natal"]["stars"]:
        assert set(star["hua"]) <= {"生年", "命宫", "日干"}
    atlas["natal"]["palaces"][0]["name"] = "修改返回值"
    atlas["decades"][0]["age_start"] = 999
    assert chart == before


def test_hua_variants_do_not_cross_contaminate_or_use_annual_year_stems(example):
    changed = build_life_atlas(natal(profile=RuleProfile(xin=2)))
    changed_hits = hit_map(changed)
    assert {37, 40, 44}.isdisjoint(changed_hits)
    assert 48 in changed_hits
    assert {p["star"] + p["hua"] for p in changed_hits[48]["annual_paths"]} == {
        "武曲科"
    }
    assert build_life_atlas(natal()) == example
    palace_annual = build_life_atlas(natal(profile=RuleProfile(annual_hua="palace")))
    assert palace_annual["windows"] == example["windows"]


@pytest.mark.parametrize(
    "capability", ["natal_stars", "layer_palaces", "palace_stem_flights"]
)
def test_missing_p1_capability_fails_closed(capability, monkeypatch):
    original = life_atlas.build_facts

    def missing(*args, **kwargs):
        facts = original(*args, **kwargs)
        facts["capabilities"].remove(capability)
        return facts

    monkeypatch.setattr(life_atlas, "build_facts", missing)
    with pytest.raises(ValueError, match="缺少必要计算能力"):
        build_life_atlas(natal())


def test_knowledge_is_versioned_conditional_heluo_only(example):
    assert example["rules_version"] == example["natal"]["rules_version"]
    assert example["knowledge_version"]
    assert {card["id"] for card in example["knowledge_snapshot"]} == {
        "hl-method",
        "hl-stems",
        "hl-annual",
        "hl-decade-inside",
        "hl-context",
    }
    assert set(example["knowledge_source_snapshot"]) == {"heluo"}
    assert all(
        card["source"] == "heluo" and card["conditions"] and card["exceptions"]
        for card in example["knowledge_snapshot"]
    )
    assert "独立限流曜" in example["scan_summary"]["unsupported"]
    assert "重大事件阈值" in example["preview_rule"]["not_evaluated"]
    assert "童限事件判断" in example["preview_rule"]["not_evaluated"]
    assert "非古籍重大事件公式" in example["preview_rule"]["status"]
    assert "不表示无事发生" in example["preview_rule"]["no_hit_meaning"]
