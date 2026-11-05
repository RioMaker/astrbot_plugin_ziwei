import asyncio

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.engine import build_chart
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .helpers import Context, allow, invoke_command


@pytest.mark.parametrize(
    "compact,separate",
    [
        ("199006150830 男", "1990-06-15 08:30 男"),
        ("19900615083045 女", "1990-06-15 08:30:45 女"),
        ("19900615 0830 male", "1990-06-15 08:30 male"),
        ("1990-06-15 083045 female", "1990-06-15 08:30:45 female"),
        (
            "公历 199006150830 男 文字 经度=116.4 流年=2026",
            "公历 1990-06-15 08:30 男 文字 经度=116.4 流年=2026",
        ),
        ("农历 199005230830 男", "农历 1990-05-23 08:30 男"),
        ("农历 2023-闰02-16 123000 女", "农历 2023-闰02-16 12:30:00 女"),
        ("农历 2023闰02161200 女", "农历 2023-闰02-16 12:00 女"),
        ("农历 2023闰0216123045 女", "农历 2023-闰02-16 12:30:45 女"),
        ("199006150000 男 真太阳时=关", "1990-06-15 00:00 男 真太阳时=关"),
        ("19900615235959 男", "1990-06-15 23:59:59 男"),
    ],
)
def test_compact_input_matches_existing_request(compact, separate):
    assert parse_request(compact) == parse_request(separate)


@pytest.mark.parametrize(
    "text",
    [
        "1990061508 男",
        "19900615083 男",
        "1990061508304 男",
        "199006150830456 男",
        "199006152400 男",
        "199006150860 男",
        "19900615083060 男",
        "199102290830 男",
        "199013150830 男",
        "199006150830",
        "19900615 男",
        "199006150830 09:00 男",
        "农历 199005320830 男",
        "19900615 2400 男",
        "19900615 083060 男",
        "2023闰02161200 女",
        "农历 2023闰0216120 女",
        "农历 2023闰02162400 女",
        "农历 2023闰0216120060 女",
    ],
)
def test_invalid_or_incomplete_compact_input_is_rejected(text):
    with pytest.raises(ValueError):
        parse_request(text)


def test_compact_birth_produces_the_same_chart():
    compact = parse_request("199006150830 男")
    separate = parse_request("1990-06-15 08:30 男")
    assert build_chart(compact.birth, compact.profile) == build_chart(
        separate.birth, separate.profile
    )


def test_command_aliases_share_compact_parser_and_authorization():
    p = ZiweiPlugin(Context(), {"output_mode": "text"})
    assert p.ziwei.command_metadata["alias"] == {
        "紫薇",
        "紫微排盘",
        "紫薇排盘",
        "ziwei",
    }

    async def run():
        denied = await invoke_command(p, "199006150830 男")
        assert "尚未开启" in denied[0]
        await allow(p)
        replies = await invoke_command(p, "199006150830 男")
        assert "1990-06-15 08:30:00" in "".join(replies)
        await invoke_command(p, "19900615083045 男")
        chart = p.chart_cache.get(capture_group(Event())).chart
        assert chart["normalized"]["original"]["second"] == 45

    asyncio.run(run())
