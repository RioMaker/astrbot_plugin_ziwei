import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from astrbot_plugin_ziwei.calendar_core import BirthInput, normalize
from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.rules import RuleProfile

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/calendar-fixtures.json").read_text("utf-8")
)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["id"])
def test_recovered_calendar_samples(fixture):
    y, m, d, clock, tz, lon, _ = fixture["input"]
    h, minute, second = map(int, clock.split(":"))
    n = normalize(
        BirthInput(y, m, d, h, minute, "男", second=second, timezone=tz, longitude=lon),
        RuleProfile(),
    )
    expected = fixture["expected"]
    delta = abs(
        (
            datetime.fromisoformat(n["computed"])
            - datetime.fromisoformat(expected["apparentSolar"])
        ).total_seconds()
    )
    # This independently implemented EOT intentionally does not promise
    # bit-for-bit reproduction of the recovered ephemeris.
    assert delta <= 45
    assert "".join(n["pillars"]) == expected["pillars"]
    for field in ("year", "month", "day", "leap"):
        assert n["lunar"][field] == expected["lunarDate"][field]


@pytest.mark.parametrize(
    "rule,day,expected",
    [("split", 15, 2), ("split", 16, 3), ("current", 16, 2), ("next", 15, 3)],
)
def test_leap_month_boundary(rule, day, expected):
    birth = BirthInput(
        2023, 2, day, 12, 0, "女", calendar="lunar", leap=True, true_solar=False
    )
    n = normalize(birth, RuleProfile(leap_month_rule=rule))
    assert n["month"] == expected
    assert n["lunar"]["month"] == 2 and n["lunar"]["leap"]
    assert n["original"]["month"] == 2


@pytest.mark.parametrize("day,expected_month,expected_leap", [(29, 3, False)])
def test_late_zi_exits_leap_month(day, expected_month, expected_leap):
    birth = BirthInput(
        2023, 2, day, 23, 30, "男", calendar="lunar", leap=True, true_solar=False
    )
    n = normalize(birth, RuleProfile())
    assert n["lunar"] == {
        "year": 2023,
        "month": 2,
        "day": 29,
        "leap": True,
        "text": "二〇二三年闰二月廿九",
    }
    assert n["astrology_lunar"]["month"] == expected_month
    assert n["astrology_lunar"]["leap"] == expected_leap
    assert n["day"] == 1


def test_late_zi_lunar_new_year_and_pillars():
    birth = parse_request("2024-02-09 23:30 女 真太阳时=关").birth
    next_day = normalize(birth, RuleProfile())
    midnight = normalize(birth, RuleProfile(late_zi_rule="midnight"))
    assert next_day["astrology_lunar"]["year"] == 2024
    assert next_day["day"] == 1 and next_day["month"] == 1
    assert midnight["astrology_lunar"]["year"] == 2023
    assert midnight["day"] == 30 and midnight["month"] == 12
    assert next_day["year_stem"] == 1 and midnight["year_stem"] == 10
    assert next_day["pillars"][2:] != midnight["pillars"][2:]
    assert next_day["original"] == midnight["original"]


def test_ziwei_year_distinct_from_term_year():
    n = normalize(parse_request("2024-02-06 12:00 男 真太阳时=关").birth, RuleProfile())
    assert n["year_stem"] == 10 and n["year_branch"] == 4  # still lunar 2023
    assert n["pillars"][0] == "甲辰"  # already after Lichun


def test_same_ut_has_same_term_pillars_across_timezones():
    a = parse_request("2024-02-04 16:00 男 真太阳时=关 时区=8").birth
    b = replace(a, hour=8, timezone=0)
    assert (
        normalize(a, RuleProfile())["pillars"][:2]
        == normalize(b, RuleProfile())["pillars"][:2]
    )


def test_solar_lunar_input_equivalence():
    a = normalize(parse_request("1990-06-15 08:30 男").birth, RuleProfile())
    b = normalize(parse_request("农历 1990-05-23 08:30 男").birth, RuleProfile())
    assert a["computed"] == b["computed"] and a["pillars"] == b["pillars"]


@pytest.mark.parametrize(
    "text",
    [
        "1991-02-29 10:00 男",
        "1990-13-01 10:00 男",
        "1899-01-01 10:00 女",
        "2101-01-01 10:00 女",
        "农历 2024-闰02-01 12:00 女",
        "农历 2023-闰02-30 12:00 男",
        "1990-06-15 24:00 男",
        "1990-06-15 08:30 未知",
        "1990-06-15 08:30 男 经度=nan",
        "1990-06-15 08:30 男 时区=15",
        "1990-06-15 08:30 男 经度=181",
    ],
)
def test_invalid_birth_rejected(text):
    with pytest.raises(ValueError):
        parse_request(text)
