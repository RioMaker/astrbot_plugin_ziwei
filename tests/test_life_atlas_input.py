"""Atlas input shares calendar rules without inheriting chart display defaults."""

from copy import deepcopy
from dataclasses import replace

import pytest

from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.life_atlas_commands import (
    LifeAtlasRequest,
    parse_life_atlas_request,
)

DEMO = "199006150830 男"
CHART_ID = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    "text",
    [
        DEMO,
        "19900615083045 女",
        "1990-06-15 08:30 男",
        "19900615 083045 female",
        "19900615 辰时 male",
        "公历 199006150830 男 真太阳时=关",
        "农历 199005230830 男",
        "农历 2023-闰02-16 12:30 女 闰月=下月 子时=零点",
        "199006150830 男 经度=116.4 时区=8",
    ],
)
def test_birth_input_reuses_chart_parser_with_dark_image_default(text):
    request = parse_life_atlas_request(text)
    assert request == LifeAtlasRequest(
        replace(parse_request(text), output="image", image_theme="night")
    )


def test_calendar_configuration_is_preserved_without_display_cross_contamination():
    config = {
        "output_mode": "text",
        "image_theme": "day",
        "true_solar": False,
        "timezone": 9,
        "longitude": 135,
        "leap_month_rule": "next",
        "late_zi_rule": "midnight",
        "geng": 3,
        # Only explicitly requested range options are rejected.
        "流年": 2026,
        "运限": 2,
    }
    snapshot = deepcopy(config)
    request = parse_life_atlas_request(DEMO, config)
    assert request.chart_request == replace(
        parse_request(DEMO, config), output="image", image_theme="night"
    )
    assert request.image_theme == "night"
    assert config == snapshot
    assert parse_life_atlas_request(DEMO).chart_request != request.chart_request


@pytest.mark.parametrize(
    "theme,expected",
    [("白天", "day"), ("day", "day"), ("夜间", "night"), ("night", "night")],
)
def test_explicit_theme_overrides_only_the_current_request(theme, expected):
    config = {"image_theme": "invalid", "output_mode": "invalid"}
    request = parse_life_atlas_request(f"{DEMO} 主题={theme}", config)
    assert request.image_theme == expected
    assert request.chart_request.image_theme == expected
    assert request.chart_request.output == "image"
    assert parse_life_atlas_request(DEMO, config).image_theme == "night"


@pytest.mark.parametrize(
    "text,theme",
    [("", "night"), ("  ", "night"), ("主题=白天", "day"), ("主题=night", "night")],
)
def test_recent_chart_selection_never_invents_birth_data(text, theme):
    assert parse_life_atlas_request(text, {"image_theme": "day"}) == LifeAtlasRequest(
        None, image_theme=theme
    )


def test_archive_selection_accepts_only_id_and_theme():
    assert parse_life_atlas_request(f"命盘={CHART_ID}") == LifeAtlasRequest(
        None, CHART_ID
    )
    assert parse_life_atlas_request(f"主题=白天 命盘={CHART_ID}") == LifeAtlasRequest(
        None, CHART_ID, "day"
    )


@pytest.mark.parametrize(
    "extra",
    [
        DEMO,
        "农历",
        "经度=116.4",
        "时区=8",
        "真太阳时=关",
        "闰月=next",
        "子时=midnight",
        "图片",
        "输出=图片",
        "任意选项=值",
        "命盘",
        "主题",
    ],
)
def test_archive_id_cannot_be_mixed_with_birth_or_calculation_options(extra):
    with pytest.raises(ValueError, match="不能混用"):
        parse_life_atlas_request(f"命盘={CHART_ID} {extra}")


@pytest.mark.parametrize(
    "chart_id", ["", "0" * 31, "0" * 33, "g" * 32, CHART_ID.upper(), "../" + "0" * 32]
)
def test_archive_id_requires_exact_lowercase_hex(chart_id):
    with pytest.raises(ValueError, match="命盘编号"):
        parse_life_atlas_request(f"命盘={chart_id}")


@pytest.mark.parametrize(
    "option",
    ["流年=2026", "流盘=2026-10-06", "流时=10:00", "运限=0", "大限=1", "大运=2"],
)
@pytest.mark.parametrize("base", [DEMO, "", f"命盘={CHART_ID}"])
def test_explicit_range_is_rejected_in_every_selection_mode(option, base):
    with pytest.raises(ValueError, match="固定从出生"):
        parse_life_atlas_request(f"{base} {option}")


@pytest.mark.parametrize("option", ["文字", "text", "输出=文字", "输出=text"])
def test_text_output_is_not_an_atlas(option):
    with pytest.raises(ValueError, match="仅支持图片"):
        parse_life_atlas_request(f"{DEMO} {option}")


@pytest.mark.parametrize("option", ["图片", "image", "输出=图片", "输出=image"])
def test_explicit_image_output_is_supported_with_birth_data(option):
    assert parse_life_atlas_request(f"{DEMO} {option}").chart_request.output == "image"


@pytest.mark.parametrize(
    "text",
    [
        "199006150830",
        "19900615 男",
        "19900615083 男",
        "1990061508304 男",
        "199006152400 男",
        "199006150860 男",
        "19900615083060 男",
        "199102290830 男",
        "199013150830 男",
        "199006150830 性别未知",
        "主题=白天 主题=夜间",
        f"命盘={CHART_ID} 命盘={CHART_ID}",
        f"{DEMO} 主题=星空",
    ],
)
def test_invalid_or_missing_data_is_not_autofilled(text):
    with pytest.raises(ValueError):
        parse_life_atlas_request(text)


@pytest.mark.parametrize(
    "text,message",
    [(DEMO + "x" * 1025, "指令过长"), (DEMO + ' "', "指令中存在未闭合的引号")],
)
def test_syntax_errors_do_not_echo_birth_data(text, message):
    with pytest.raises(ValueError) as exc:
        parse_life_atlas_request(text)
    assert str(exc.value) == message
    assert "19900615" not in str(exc.value)
