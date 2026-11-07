"""Calendar aliases, explicit Chinese dates and the solar-correction order."""

import pytest

from astrbot_plugin_ziwei.calendar_core import normalize
from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.compact_commands import normalize_compact_arguments
from astrbot_plugin_ziwei.engine import build_chart
from astrbot_plugin_ziwei.life_atlas_commands import parse_life_atlas_request
from astrbot_plugin_ziwei.personal_commands import natal_request


@pytest.mark.parametrize(
    "name,date,canonical",
    [(name, "199006150830", "solar") for name in ("公历", "阳历", "solar")]
    + [(name, "199005230830", "lunar") for name in ("农历", "阴历", "lunar")],
)
def test_calendar_prefix_and_option_are_equivalent(name, date, canonical):
    expected = parse_request(f"{canonical} {date} 男")
    for text in (
        f"{name} {date} 男",
        f"{name}{date} 男",
        f"{date} 男 历法={name}",
        f"{name}{date} 男 历法={canonical}",
    ):
        request = parse_request(text)
        assert request == expected
        assert request.birth.civil_datetime().isoformat() == "1990-06-15T08:30:00"


@pytest.mark.parametrize(
    "text,canonical",
    [
        ("阳历1990年6月15日08:30 男", "199006150830 男"),
        ("1990年06月15号 083045 女", "19900615083045 女"),
        ("阴历1990年五月廿三日0830 男", "农历 199005230830 男"),
        ("农历 1990年5月23日 08:30 男", "农历 199005230830 男"),
        ("1990年五月二十三日 08:30 男 历法=阴历", "农历 199005230830 男"),
        ("农历2023年闰二月十六日120045 女", "农历 2023闰0216120045 女"),
        ("阴历 2024年正月初一日 子时 女", "农历 202401010000 女"),
        ("农历2023年腊月三十日亥时 男", "农历 202312302200 男"),
        ("农历2023年冬月廿日 12:00 男", "农历 202311201200 男"),
        ("农历2023年腊月卅日 12:00 男", "农历 202312301200 男"),
    ],
)
def test_named_dates_share_all_calculation_entry_points(text, canonical):
    request = parse_request(text)
    expected = parse_request(canonical)
    assert request == expected
    assert natal_request(text, {}) == expected
    assert parse_life_atlas_request(text).chart_request == expected
    assert build_chart(request.birth, request.profile) == build_chart(
        expected.birth, expected.profile
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("阴历1990年五月廿三日0830男", "阴历 1990年五月廿三日 0830 男"),
        ("阳历1990年6月15日08:30男文字", "阳历 1990年6月15日 08:30 男 文字"),
        ("2023年闰二月十六日1200女历法=农历", "2023年闰二月十六日 1200 女 历法=农历"),
        ("农历2024年正月初一日子时女", "农历 2024年正月初一日 子时 女"),
    ],
)
def test_named_compact_dates_only_split_explicit_boundaries(text, expected):
    normalized = normalize_compact_arguments(text)
    assert normalized.args == expected
    assert parse_request(normalized.args) == parse_request(expected)


@pytest.mark.parametrize(
    "lunar,solar,civil",
    [
        ("2023年闰二月十六日", "2023年4月6日", "2023-04-06"),
        ("2024年正月初一日", "2024年2月10日", "2024-02-10"),
        ("2023年腊月三十日", "2024年2月9日", "2024-02-09"),
    ],
)
@pytest.mark.parametrize("options", ["真太阳时=关", "经度=75", "经度=140 子时=零点"])
def test_lunar_conversion_precedes_true_solar_time_and_rollover(
    lunar, solar, civil, options
):
    a = parse_request(f"阴历{lunar} 0030 女 {options}")
    b = parse_request(f"阳历{solar} 0030 女 {options}")
    left, right = build_chart(a.birth, a.profile), build_chart(b.birth, b.profile)
    normalized = left["normalized"]
    assert normalized["civil"] == civil + " 00:30:00"
    assert normalized["original"]["calendar"] == "lunar"
    if options == "真太阳时=关":
        assert normalized["computed"] == normalized["civil"]
        assert normalized["correction_minutes"] == 0
    if options == "经度=75":
        assert normalized["computed"][:10] < civil
    normalized.pop("original")
    right["normalized"].pop("original")
    assert left == right


def test_calendar_selection_and_solar_switch_do_not_leak_between_requests():
    config = {"longitude": 75, "true_solar": True}
    first = parse_request("202401010030 女 历法=阴历 真太阳时=关", config)
    second = parse_request("202401010030 女", config)
    assert first.birth.calendar == "lunar" and not first.birth.true_solar
    assert second.birth.calendar == "solar" and second.birth.true_solar
    assert normalize(first.birth, first.profile)["civil"] == "2024-02-10 00:30:00"
    assert normalize(second.birth, second.profile)["civil"] == "2024-01-01 00:30:00"
    assert config == {"longitude": 75, "true_solar": True}


@pytest.mark.parametrize(
    "text",
    [
        "199006150830 男 历法=未知",
        "阳历199006150830 男 历法=农历",
        "农历199005230830 男 历法=阳历",
        "199006150830 男 历法=公历 历法=阳历",
        "农历2024年闰二月初一日1200 女",
        "农历2023年闰二月三十日1200 女",
        "阳历2023年闰二月十六日1200 女",
        "1990年五月廿三日0830 男",
        "农历2023年十三月初一日1200 男",
        "农历2023年正月初十一日1200 男",
        "农历2023年正月廿十日1200 男",
        "农历2023年正月三十一日1200 男",
        "农历2023年正月初零日1200 男",
        "农历闰202302161200 女",
        "农历2023闰闰02161200 女",
        "阳历1990年6月15日2400 男",
        "阴历1990年五月廿三日 男",
        "阴历1990年五月廿三日0830 男 后缀",
    ],
)
def test_invalid_ambiguous_or_conflicting_calendar_inputs_are_rejected(text):
    with pytest.raises(ValueError):
        parse_request(text)
