"""Explicit course alternatives, immutable archives and per-request isolation."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from astrbot_plugin_ziwei.calendar_core import BirthInput, normalize
from astrbot_plugin_ziwei.engine import (
    apply_decade,
    apply_flow,
    build_chart,
    build_natal,
)
from astrbot_plugin_ziwei.rules import TABLES, RuleProfile

from .test_course_rules import positions

# Fixed pairs for 子/丑/寅/卯 groups, repeated for 辰..亥. Hour rows have
# been advanced independently around the twelve branches, including wrap.
FIRE_BELL = {
    ("verse", 1): ("寅戌", "卯戌", "丑卯", "酉戌"),
    ("verse", 5): ("午寅", "未寅", "巳未", "丑寅"),
    ("verse", 12): ("丑酉", "寅酉", "子寅", "申酉"),
    ("copy", 1): ("寅戌", "戌卯", "丑卯", "酉戌"),
    ("copy", 5): ("午寅", "寅未", "巳未", "丑寅"),
    ("copy", 12): ("丑酉", "酉寅", "子寅", "申酉"),
}


@pytest.fixture(scope="module")
def normalized():
    return normalize(BirthInput(1990, 6, 15, 8, 30, "男"), RuleProfile())


@pytest.mark.parametrize("year_branch", range(1, 13))
@pytest.mark.parametrize("hour", [1, 5, 12])
def test_fire_bell_alternatives_do_not_mix_school_hour_or_year_groups(
    normalized, year_branch, hour
):
    for school in ("xunlongzhe", "wm259"):
        for source in ("verse", "copy"):
            for method in ("school", "year", "hour"):
                profile = RuleProfile(
                    rule_school=school, fire_bell_chou=source, fire_bell_hour=method
                )
                chart = build_natal(
                    dict(normalized, year_branch=year_branch, hour_branch=hour),
                    "男",
                    profile,
                )
                expected_hour = (
                    hour
                    if method == "hour" or (method == "school" and school == "wm259")
                    else 1
                )
                expected = FIRE_BELL[source, expected_hour][(year_branch - 1) % 4]
                assert positions(chart, 23) + positions(chart, 24) == expected


@pytest.mark.parametrize("year_branch", range(1, 13))
def test_list_variant_moves_only_the_three_disputed_jiangqian_stars(
    normalized, year_branch
):
    args = dict(normalized, year_branch=year_branch)
    verse = build_natal(args, "女", RuleProfile())
    variant = build_natal(args, "女", RuleProfile(jiangqian_order="list"))
    start = (0, 9, 6, 3)[(year_branch - 1) % 4]
    assert [
        variant["palaces"][(start + i) % 12]["sequences"]["将前"] for i in range(12)
    ] == "将星 攀鞍 岁驿 息神 华盖 劫煞 灾煞 天煞 月煞 指背 咸池 亡神".split()
    assert variant["stars"] == verse["stars"]
    for a, b in zip(variant["palaces"], verse["palaces"], strict=True):
        assert {k: v for k, v in a["sequences"].items() if k != "将前"} == {
            k: v for k, v in b["sequences"].items() if k != "将前"
        }


@pytest.mark.parametrize(
    "school,options,custom",
    [
        ("xunlongzhe", {}, False),
        ("xunlongzhe", {"fire_bell_hour": "year"}, False),
        ("xunlongzhe", {"fire_bell_hour": "hour"}, True),
        ("xunlongzhe", {"fire_bell_chou": "copy"}, True),
        ("xunlongzhe", {"jiangqian_order": "list"}, True),
        ("xunlongzhe", {"geng": 4}, True),
        ("wm259", {}, False),
        ("wm259", {"fire_bell_hour": "hour"}, False),
        ("wm259", {"fire_bell_hour": "year"}, True),
        ("wm259", {"fire_bell_chou": "copy"}, True),
        ("wm259", {"jiangqian_order": "list"}, True),
    ],
)
def test_source_label_uses_effective_choices(school, options, custom):
    assert (
        "自定义" in RuleProfile(rule_school=school, **options).source_label
    ) == custom


@pytest.mark.parametrize("school", ["xunlongzhe", "wm259"])
def test_snapshots_without_new_options_keep_the_previous_results(school):
    birth = BirthInput(1993, 6, 15, 8, 30, "男")
    chart = build_chart(birth, RuleProfile(rule_school=school))
    for key in ("fire_bell_hour", "fire_bell_chou", "jiangqian_order"):
        del chart["profile"][key]
    if school == "wm259":
        del chart["profile"]["rule_school"]
    before = deepcopy(chart)
    restored = RuleProfile.from_snapshot(chart["profile"])
    rebuilt = build_chart(birth, restored)
    assert rebuilt["stars"] == chart["stars"]
    assert rebuilt["palaces"] == chart["palaces"]
    for overlaid in (apply_decade(chart, 2), apply_flow(chart, year=2026)):
        assert [(s["id"], s["branch"]) for s in overlaid["stars"]] == [
            (s["id"], s["branch"]) for s in chart["stars"]
        ]
        assert overlaid["profile"] == before["profile"]
    assert chart == before


def test_alternating_explicit_variants_do_not_mutate_shared_tables():
    birth = BirthInput(1993, 6, 15, 8, 30, "男")
    profiles = [
        RuleProfile(),
        RuleProfile(fire_bell_chou="copy", jiangqian_order="list", geng=4),
        RuleProfile(fire_bell_hour="hour"),
        RuleProfile(rule_school="wm259"),
    ]
    tables = deepcopy(TABLES)
    expected = [build_chart(birth, p) for p in profiles]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda p: build_chart(birth, p), profiles * 4))
    assert results == expected * 4
    assert TABLES == tables


@pytest.mark.parametrize("key", ["fire_bell_hour", "fire_bell_chou", "jiangqian_order"])
def test_unknown_choices_are_rejected(key):
    with pytest.raises(ValueError, match=key):
        RuleProfile(**{key: "unknown"})
