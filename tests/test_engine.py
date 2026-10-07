import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.engine import (
    _brightness,
    apply_flow,
    build_chart,
    build_natal,
)
from astrbot_plugin_ziwei.rules import RuleProfile


def natal(profile=None):
    request = parse_request("1990-06-15 08:30 男")
    return build_chart(request.birth, profile or request.profile)


def test_reference_core_fixed_expectations():
    c = natal()
    expected = json.loads(
        (Path(__file__).parent / "fixtures/core-formula-fixture.json").read_text(
            "utf-8"
        )
    )["result"]
    for key in ("life", "body", "bureau"):
        assert c[key] == expected[key]
    assert [p["stem"] for p in c["palaces"]] == expected["palaceStems"]
    positions = {str(s["id"]): s["branch"] for s in c["stars"]}
    for sid, branch in expected["starPositions"].items():
        assert positions[sid] == branch
    assert list(RuleProfile().four_hua(7)) == expected["birthFourHua"]
    assert (
        list(RuleProfile().four_hua(c["palaces"][c["life"] - 1]["stem"]))
        == expected["lifePalaceFourHua"]
    )
    for actual, wanted in zip(c["decades"][1:], expected["decades"], strict=True):
        assert actual["branch"] == wanted["branch"]
        assert actual["age_start"] == wanted["ageStart"]
        assert actual["age_end"] == wanted["ageEnd"]


@pytest.mark.parametrize("stem", range(1, 11))
@pytest.mark.parametrize("sex", ["男", "女"])
def test_direction_and_complete_palace_partitions(stem, sex):
    n = natal()["normalized"]
    n.update(year_stem=stem, year_branch=stem)
    c = build_natal(n, sex, RuleProfile())
    expected = 1 if (stem % 2 == 1) == (sex == "男") else -1
    assert c["direction"] == expected
    assert len({p["name"] for p in c["palaces"]}) == 12
    assert len({d["branch"] for d in c["decades"][1:]}) == 12
    for label in ("长生", "博士", "岁前", "将前"):
        assert len({p["sequences"][label] for p in c["palaces"]}) == 12
    assert {s["id"] for s in c["stars"] if s["group"] == "major"} == set(range(1, 15))
    assert all(1 <= s["branch"] <= 12 for s in c["stars"])
    assert len({s["instance_id"] for s in c["stars"]}) == len(c["stars"])


@pytest.mark.parametrize(
    "bureau_stem,life_month", [(1, 2), (2, 2), (3, 2), (4, 2), (5, 2)]
)
@pytest.mark.parametrize("day", [1, 2, 5, 6, 15, 29, 30])
def test_major_stars_all_bureaus_and_day_padding(bureau_stem, life_month, day):
    n = natal()["normalized"]
    n.update(year_stem=bureau_stem, year_branch=bureau_stem, month=life_month, day=day)
    c = build_natal(n, "男", RuleProfile())
    positions = {s["id"]: s["branch"] for s in c["stars"]}
    assert len([s for s in c["stars"] if s["id"] <= 14]) == 14
    assert (positions[1] + positions[7] - 6) % 12 == 0
    assert (positions[3] - positions[1]) % 12 == 9
    assert (positions[14] - positions[7]) % 12 == 10


@pytest.mark.parametrize(
    "name,selection,stem,wanted",
    [
        ("jia", 2, 1, (6, 14, 16, 3)),
        ("wu", 2, 5, (9, 8, 3, 2)),
        ("geng", 2, 7, (3, 4, 5, 8)),
        ("geng", 3, 7, (3, 4, 7, 5)),
        ("geng", 4, 7, (3, 4, 7, 11)),
        ("geng", 5, 7, (3, 4, 5, 11)),
        ("xin", 2, 8, (10, 3, 4, 15)),
        ("ren", 2, 9, (12, 1, 7, 4)),
        ("ren", 3, 9, (12, 1, 11, 4)),
        ("gui", 2, 10, (14, 10, 3, 9)),
    ],
)
def test_all_nondefault_hua_variants_isolated(name, selection, stem, wanted):
    original = RuleProfile().four_hua(stem)
    assert RuleProfile(**{name: selection}).four_hua(stem) == wanted
    assert RuleProfile().four_hua(stem) == original


def test_self_hua_and_secondary_voids_are_distinct():
    c = natal()
    moon = next(s for s in c["stars"] if s["id"] == 8)
    assert moon["self_hua"]["outward"] == "忌"
    for sid in (77, 78):
        pair = [s for s in c["stars"] if s["id"] == sid]
        assert (
            len(pair) == 2
            and pair[0]["secondary"] is False
            and pair[1]["secondary"] is True
        )
        assert pair[0]["branch"] != pair[1]["branch"]


def test_flow_is_pure_and_higher_layer_clears_lower_layers():
    base = natal()
    before = deepcopy(base)
    all_layers = apply_flow(base, target=datetime(2026, 10, 6, 10))
    assert base == before
    assert set(all_layers["flow"]["layers"]) == {
        "大限",
        "小限",
        "流年",
        "流月",
        "流日",
        "流时",
    }
    assert all_layers["flow"]["age"] == 37
    assert all_layers["flow"]["layers"]["流年"]["life"] == 7
    changed = apply_flow(all_layers, year=2027)
    assert set(changed["flow"]["layers"]) == {"大限", "小限", "流年"}
    assert all(not ({"流月", "流日", "流时"} & set(s["hua"])) for s in changed["stars"])
    assert all(
        not ({"流月", "流日", "流时"} & set(p["flow_names"]))
        for p in changed["palaces"]
    )
    assert [(s["id"], s["branch"]) for s in changed["stars"]] == [
        (s["id"], s["branch"]) for s in base["stars"]
    ]


def test_flow_new_year_and_toddler_boundary():
    c = natal()
    before_spring = apply_flow(c, target=datetime(2024, 2, 9, 12))
    after_spring = apply_flow(c, target=datetime(2024, 2, 10, 12))
    assert before_spring["flow"]["year"] == 2023
    assert after_spring["flow"]["year"] == 2024
    assert apply_flow(c, year=1993)["flow"]["decade"]["index"] == 0
    assert apply_flow(c, year=1994)["flow"]["decade"]["index"] == 1


@pytest.mark.parametrize("year", [1899, 1989, 2123])
def test_flow_invalid_range(year):
    with pytest.raises(ValueError):
        apply_flow(natal(), year=year)


@pytest.mark.parametrize("brightness", ["qs", "zz", "xd1", "xd2"])
def test_brightness_tables_and_small_star_switch(brightness):
    c = natal(RuleProfile(brightness=brightness, minor_stars=False))
    assert len(c["stars"]) == 28
    # The independent source tables give grade 2 at 丑 for all four profiles.
    expected = {"qs": "旺", "zz": "旺", "xd1": "旺", "xd2": "旺"}
    assert (
        next(s for s in c["stars"] if s["id"] == 19)["brightness"]
        == expected[brightness]
    )
    if brightness != "xd2":
        assert _brightness(19, 3, RuleProfile(brightness=brightness)) == ""
