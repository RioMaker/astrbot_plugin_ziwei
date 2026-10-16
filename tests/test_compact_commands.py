"""Compact lexical boundaries remain separate from calendars and authorization."""

import pytest

from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.compact_commands import (
    CompactCommand,
    NormalizedArguments,
    normalize_compact_arguments,
    parse_compact_command,
)
from astrbot_plugin_ziwei.life_atlas_commands import parse_life_atlas_request


@pytest.mark.parametrize(
    "compact,expected",
    [
        ("199006150830男", "199006150830 男"),
        ("19900615083045女", "19900615083045 女"),
        ("1990-06-15 08:30男", "1990-06-15 08:30 男"),
        ("1990/06/15 08:30:45女", "1990/06/15 08:30:45 女"),
        ("1990-06-1508:30男", "1990-06-15 08:30 男"),
        ("1990/06/1508:30:45女", "1990/06/15 08:30:45 女"),
        ("1990061508:30男", "19900615 08:30 男"),
        ("1990-06-1508:30 男", "1990-06-15 08:30 男"),
        ("农历2023-闰02-1612:30女", "农历 2023-闰02-16 12:30 女"),
        ("19900615 0830男", "19900615 0830 男"),
        ("19900615 083045女", "19900615 083045 女"),
        ("1990-06-15 辰时男", "1990-06-15 辰时 男"),
        ("农历199005230830男", "农历 199005230830 男"),
        ("阴历2023-闰02-16 12:30女", "阴历 2023-闰02-16 12:30 女"),
        ("solar199006150830男", "solar 199006150830 男"),
        ("lunar199005230830男", "lunar 199005230830 男"),
        (
            "199006150830男经度=116.4时区=8真太阳时=关主题=白天",
            "199006150830 男 经度=116.4 时区=8 真太阳时=关 主题=白天",
        ),
        (
            "199006150830 男经度=-73.5时区=-5子时=midnight闰月=next",
            "199006150830 男 经度=-73.5 时区=-5 子时=midnight 闰月=next",
        ),
        (
            '199006150830男经度="116.4"时区=8',
            '199006150830 男 经度="116.4" 时区=8',
        ),
        ("199006150830男文字", "199006150830 男 文字"),
        ("199006150830女图片主题=夜间", "199006150830 女 图片 主题=夜间"),
        (
            "199006150830男流盘=2026-10-06流时=10:00",
            "199006150830 男 流盘=2026-10-06 流时=10:00",
        ),
    ],
)
def test_normalized_compact_arguments_match_existing_strict_request(compact, expected):
    result = normalize_compact_arguments(compact)
    assert result == NormalizedArguments(expected, True)
    assert parse_request(result.args) == parse_request(expected)
    assert normalize_compact_arguments(result.args) == NormalizedArguments(expected)


def test_archive_id_and_theme_have_explicit_boundaries_without_birth_inference():
    chart_id = "0123456789abcdef0123456789abcdef"
    result = normalize_compact_arguments(f"命盘={chart_id}主题=白天")
    assert result == NormalizedArguments(f"命盘={chart_id} 主题=白天", True)
    request = parse_life_atlas_request(result.args)
    assert request.chart_request is None
    assert request.chart_id == chart_id and request.image_theme == "day"


@pytest.mark.parametrize(
    "text",
    [
        "199006150830 男",
        "1990-06-15   08:30\t女",
        "人生苦短",
        "紫薇花",
        '主题="夜间经度=120"',
        "主题='夜间经度=120'",
        '主题="夜间经度=120',
        "199006150830male",
        "图谱199006150830男",
        "1990年出生男",
        "1990喜欢男",
        "1990-06-15 无关男",
        "子男",
    ],
)
def test_unknown_or_quoted_text_is_preserved_and_not_guessed(text):
    assert normalize_compact_arguments(text) == NormalizedArguments(text)


def test_outer_whitespace_does_not_mark_a_standard_command_as_compact():
    assert normalize_compact_arguments("  199006150830 男\n") == NormalizedArguments(
        "199006150830 男"
    )


@pytest.mark.parametrize(
    "text", ["199002300830男", "199006152400女", "1990061508304男"]
)
def test_lexical_splitting_marks_compact_even_when_calendar_later_rejects(text):
    normalized = normalize_compact_arguments(text)
    assert normalized.changed
    assert normalized.args == text[:-1] + " " + text[-1]
    with pytest.raises(ValueError):
        parse_request(normalized.args)


@pytest.mark.parametrize(
    "text",
    [
        "1990-06-158:30男",
        "1990-06-508:30女",
        "1990-6-1508:30男",
        "1990--06-15 08:30男",
        "1990-06-1508:305男",
        "1990-06-15辰时男",
    ],
)
def test_ambiguous_or_malformed_birth_shapes_only_split_the_sex_boundary(text):
    normalized = normalize_compact_arguments(text)
    assert normalized == NormalizedArguments(text[:-1] + " " + text[-1], True)
    with pytest.raises(ValueError):
        parse_request(normalized.args)
    command = parse_compact_command("/人生 " + text)
    assert command.args == normalized.args and command.compact


def test_fixed_width_boundaries_do_not_hide_calendar_errors():
    normalized = normalize_compact_arguments("1990-02-3008:30男")
    assert normalized == NormalizedArguments("1990-02-30 08:30 男", True)
    with pytest.raises(ValueError):
        parse_request(normalized.args)


@pytest.mark.parametrize("suffix", ["未知", "未知=1", "男女", "主题=未知", "流年=abcd"])
def test_unrecognized_suffix_remains_for_strict_validation(suffix):
    result = normalize_compact_arguments("199006150830男" + suffix)
    assert suffix in result.args
    with pytest.raises(ValueError):
        parse_request(result.args)


@pytest.mark.parametrize("alias", ["紫微", "紫薇", "紫微排盘", "紫薇排盘", "ziwei"])
@pytest.mark.parametrize("prefix", ["/", ""])
def test_each_natal_alias_accepts_joined_digits_or_atlas_subcommand(alias, prefix):
    assert parse_compact_command(f"{prefix}{alias}199006150830男") == CompactCommand(
        alias, "natal", "199006150830 男", True
    )
    assert parse_compact_command(
        f"{prefix}{alias}图谱199006150830男"
    ) == CompactCommand(alias, "atlas", "199006150830 男", True)


@pytest.mark.parametrize("prefix", ["/", ""])
def test_life_command_and_calendar_prefix_are_explicit_boundaries(prefix):
    assert parse_compact_command(f"{prefix}人生农历199005230830女") == CompactCommand(
        "人生", "atlas", "农历 199005230830 女", True
    )


@pytest.mark.parametrize("word", ["开启", "开", "关闭", "关", "状态"])
def test_only_exact_management_words_are_recognized(word):
    assert parse_compact_command("紫薇" + word) == CompactCommand(
        "紫薇", "manage", word, True
    )
    assert parse_compact_command("/紫微 " + word) == CompactCommand(
        "紫微", "manage", word
    )


@pytest.mark.parametrize("word", ["开启", "开", "关闭", "关", "状态"])
@pytest.mark.parametrize("prefix", ["/", ""])
def test_life_command_does_not_claim_management_words(prefix, word):
    assert parse_compact_command(prefix + "人生" + word) is None


@pytest.mark.parametrize("word", ["帮助", "help", "?", "？"])
def test_help_preserves_the_command_family(word):
    assert parse_compact_command("/紫微" + word) == CompactCommand(
        "紫微", "help", word, True
    )
    assert parse_compact_command("/人生" + word) == CompactCommand(
        "人生", "atlas", word, True
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/人生", CompactCommand("人生", "atlas", "")),
        ("/紫微", CompactCommand("紫微", "help", "")),
        ("/人生 199006150830 男", CompactCommand("人生", "atlas", "199006150830 男")),
        ("/紫微 图谱", CompactCommand("紫微", "atlas", "")),
        ("/紫微图谱", CompactCommand("紫微", "atlas", "", True)),
        ("/紫微 图谱bad", CompactCommand("紫微", "atlas", "bad", True)),
        (
            "/紫微 图谱 199006150830男",
            CompactCommand("紫微", "atlas", "199006150830 男", True),
        ),
        (
            "/人生 199002300830男",
            CompactCommand("人生", "atlas", "199002300830 男", True),
        ),
    ],
)
def test_full_parser_retains_routing_and_compact_flag_for_strict_parser(text, expected):
    assert parse_compact_command(text) == expected


@pytest.mark.parametrize(
    "tail,args",
    [
        ("帮助", "帮助"),
        ("help", "help"),
        ("?", "?"),
        ("主题=夜间", "主题=夜间"),
        ("农历199005230830男", "农历 199005230830 男"),
        (" bad", "bad"),
    ],
)
def test_joined_atlas_subcommand_requires_a_determined_argument_boundary(tail, args):
    assert parse_compact_command("/紫微图谱" + tail) == CompactCommand(
        "紫微", "atlas", args, True
    )


@pytest.mark.parametrize(
    "text",
    [
        "人生苦短",
        "人生感悟",
        "紫薇花",
        "紫微星",
        "紫微开启新篇",
        "紫微农历新年",
        "紫微排盘很好看",
        "紫微图谱很好看",
        "/紫微图谱bad",
        "紫微图谱分析",
        "紫微排盘图谱概念",
        "紫薇图谱走势",
        "/ziwei图谱故事",
        "紫微图谱开启",
        "紫微图谱农历新年",
        "ziweix199006150830男",
        "帮我看看人生199006150830男",
        "/人生图谱199006150830男",
        "/图谱199006150830男",
        "//人生199006150830男",
        "",
    ],
)
def test_chat_and_unrequested_aliases_are_not_commands(text):
    assert parse_compact_command(text) is None
