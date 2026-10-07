"""Independent decade selection preserves natal stars and clears old flow layers."""

from copy import deepcopy
from datetime import datetime

import pytest

from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request
from astrbot_plugin_ziwei.engine import (
    apply_decade,
    apply_flow,
    build_chart,
    build_natal,
)
from astrbot_plugin_ziwei.rules import PALACES, RuleProfile, wrap


def natal(sex="男", profile=None):
    request = parse_request(f"1990-06-15 08:30 {sex}")
    return build_chart(request.birth, profile or request.profile)


@pytest.mark.parametrize("index", range(13))
@pytest.mark.parametrize("sex,direction", [("男", 1), ("女", -1)])
def test_decade_selection_follows_natal_direction_and_age_boundaries(
    index, sex, direction
):
    base = natal(sex)
    snapshot = deepcopy(base)
    selected = apply_decade(base, index)
    flow = selected["flow"]
    assert base == snapshot and selected is not base
    assert flow["kind"] == "decade"
    assert flow["year"] is None and flow["target"] is None
    assert set(flow["layers"]) == {"大限"}
    assert base["life"] == 3 and base["bureau"] == 5
    branch = 1 if index == 0 else wrap(3 + direction * (index - 1))
    age_start = 1 if index == 0 else 5 + (index - 1) * 10
    age_end = 4 if index == 0 else age_start + 9
    assert flow["age"] == age_start
    assert flow["decade"] == base["decades"][index]
    assert flow["layers"]["大限"]["life"] == branch
    assert flow["start_year"] == 1990 + age_start - 1
    assert flow["end_year"] == 1990 + age_end - 1
    for palace in selected["palaces"]:
        assert palace["flow_names"] == {
            "大限": PALACES[wrap(branch - palace["branch"] + 1) - 1]
        }
    assert {p["flow_names"]["大限"] for p in selected["palaces"]} == set(PALACES)


def test_first_decade_transforms_the_correct_stars_with_single_layer():
    base = natal()
    first = apply_decade(base, 1)
    # First decade at 戊寅: 贪狼禄、太阴权、右弼科、天机忌.
    expected = {9: "禄", 8: "权", 18: "科", 2: "忌"}
    assert first["flow"]["layers"]["大限"] == {
        "life": 3,
        "stem": 5,
        "four_hua": ["贪狼禄", "太阴权", "右弼科", "天机忌"],
    }
    actual = {s["id"]: s["hua"]["大限"] for s in first["stars"] if "大限" in s["hua"]}
    assert actual == expected
    for original, selected in zip(base["stars"], first["stars"], strict=True):
        assert selected is not original
        assert selected["branch"] == original["branch"]
        assert selected["self_hua"] == original["self_hua"]
        assert {
            key: value for key, value in selected["hua"].items() if key != "大限"
        } == original["hua"]
    for palace in first["palaces"]:
        for star in palace["stars"]:
            assert any(star is item for item in first["stars"])


def test_rule_variant_applies_to_selected_decade_without_mutating_default():
    # The third forward decade starts at 庚辰, exposing the configurable 庚四化.
    default = apply_decade(natal(), 3)
    changed = apply_decade(natal(profile=RuleProfile(geng=3)), 3)
    assert default["flow"]["layers"]["大限"]["stem"] == 7
    assert default["flow"]["layers"]["大限"]["four_hua"] == [
        "太阳禄",
        "武曲权",
        "太阴科",
        "天同忌",
    ]
    assert changed["flow"]["layers"]["大限"]["four_hua"] == [
        "太阳禄",
        "武曲权",
        "天府科",
        "天同忌",
    ]
    assert apply_decade(natal(), 3) == default


def test_switching_between_annual_and_decade_clears_all_old_selections():
    base = natal()
    annual = apply_flow(base, target=datetime(2026, 10, 6, 10))
    snapshot = deepcopy(annual)
    decade = apply_decade(annual, 1)
    assert annual == snapshot
    assert decade == apply_decade(base, 1)
    assert all(set(p["flow_names"]) == {"大限"} for p in decade["palaces"])
    assert all(
        set(s["hua"]) <= {"生年", "命宫", "日干", "大限"} for s in decade["stars"]
    )
    changed = apply_decade(decade, 0)
    assert changed == apply_decade(base, 0)
    assert apply_flow(changed, year=2027) == apply_flow(base, year=2027)
    assert apply_flow(changed, year=2027)["flow"]["kind"] == "annual"


def test_decade_calendar_uses_astrology_lunar_year_and_supports_final_decade():
    request = parse_request("1990-01-15 08:30 男")
    base = build_chart(request.birth, request.profile)
    assert base["normalized"]["astrology_lunar"]["year"] == 1989
    toddler = apply_decade(base, 0)["flow"]
    assert toddler["start_year"] == 1989
    final = apply_decade(natal(), 12)["flow"]
    assert (final["start_year"], final["end_year"]) == (2104, 2113)


@pytest.mark.parametrize("stem,bureau", [(1, 2), (2, 6), (3, 5), (4, 3), (5, 4)])
def test_toddler_to_first_decade_boundary_in_all_five_bureaus(stem, bureau):
    normalized = natal()["normalized"]
    normalized.update(year_stem=stem, year_branch=stem, month=4)
    base = build_natal(normalized, "男", RuleProfile())
    assert base["life"] == 2 and base["bureau"] == bureau
    toddler = apply_decade(base, 0)["flow"]
    first = apply_decade(base, 1)["flow"]
    assert (toddler["decade"]["age_start"], toddler["decade"]["age_end"]) == (
        1,
        bureau - 1,
    )
    assert first["decade"]["age_start"] == bureau
    assert toddler["end_year"] + 1 == first["start_year"]
    assert apply_flow(base, year=toddler["end_year"])["flow"]["decade"]["index"] == 0
    assert apply_flow(base, year=first["start_year"])["flow"]["decade"]["index"] == 1


@pytest.mark.parametrize("index", [-1, 13, 1.0, True, False, "1", None])
def test_decade_index_rejects_wrong_types_and_range(index):
    with pytest.raises(ValueError, match="运限须为"):
        apply_decade(natal(), index)


@pytest.mark.parametrize("key", ["运限", "大限", "大运"])
@pytest.mark.parametrize("index", [0, 1, 12])
def test_decade_options_and_aliases_share_one_request(key, index):
    request = parse_request(f"{DEMO_BIRTH} {key}={index} 主题=夜间 文字")
    assert request.decade_index == index
    assert request.flow_year is None and request.flow_target is None
    assert request.image_theme == "night" and request.output == "text"
    assert request == parse_request(f"{DEMO_BIRTH} 运限={index} 主题=夜间 文字")
    assert parse_request("199006150830 男 运限=0").decade_index == 0


@pytest.mark.parametrize(
    "options",
    [
        "运限=-1",
        "运限=13",
        "运限=1.0",
        "运限=01",
        "运限=+1",
        "运限=一",
        "运限=",
        "运限=１２",
        "运限=1 运限=2",
        "运限=1 大限=2",
        "大运=1 大限=1",
        "运限=1 流年=2026",
        "流年=2026 大运=1",
        "运限=1 流盘=2026-10-06",
        "运限=1 流时=10:00",
        "运限=1 流盘=2026-10-06 流时=10:00",
    ],
)
def test_bad_or_conflicting_decade_options_rejected(options):
    with pytest.raises(ValueError):
        parse_request(f"{DEMO_BIRTH} {options}")


def test_existing_natal_annual_and_daily_requests_do_not_select_decade():
    for suffix in ("", " 流年=2026", " 流盘=2026-10-06 流时=10:00"):
        assert parse_request(DEMO_BIRTH + suffix).decade_index is None
