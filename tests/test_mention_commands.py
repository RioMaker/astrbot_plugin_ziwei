"""Real leading @self components, retained slashes and exclusive routing."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from astrbot.api.message_components import At, Plain, Reply

from astrbot_plugin_ziwei import main
from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.compact_filter import CompactCommandFilter, fallback_command
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .helpers import allow
from .test_command_only import NoModelContext
from .test_compact_delivery import archive_counts
from .test_compact_delivery import rendered as rendered


class MentionEvent(Event):
    def __init__(self, command, *, raw=None, components=None, **kwargs):
        super().__init__(text=raw if raw is not None else command, **kwargs)
        self.components = (
            components if components is not None else [At("bot"), Plain(command)]
        )

    def get_messages(self):
        return self.components

    def get_self_id(self):
        return "bot"


async def invoke(plugin, event):
    return [reply async for reply in plugin.compact_command(event)]


@pytest.mark.parametrize(
    "name", ["紫微", "紫薇", "紫微排盘", "紫薇排盘", "ziwei", "人生", "紫微 图谱"]
)
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("marker", ["", "@演示机器人(bot) "])
def test_mentioned_calendar_commands_preserve_identity_and_archive_once(
    name, compact, marker, rendered
):
    plugin = ZiweiPlugin(NoModelContext(), {})
    args = "阴历1990年五月廿三日0830男" if compact else "阴历 1990年五月廿三日 0830 男"
    command = "/" + name + ("" if compact else " ") + args
    event = MentionEvent(command, raw=marker + command)
    original = deepcopy(event.__dict__)
    assert CompactCommandFilter().filter(event, {})

    async def run():
        await allow(plugin, event)
        result = await invoke(plugin, event)
        assert len(result) == 1 and result[0][0].type == "image"

    asyncio.run(run())
    assert event.message_str == original["message_str"]
    assert event.get_sender_id() == "10001" and event.get_self_id() == "bot"
    assert event.stopped and event.call_llm is True
    assert len(rendered) == 1
    assert rendered[0][1]["normalized"]["civil"] == "1990-06-15 08:30:00"
    assert archive_counts(plugin) == (1, int("人生" in name or "图谱" in name), 0)
    record = plugin.archive.records.list()["items"][0]
    assert record["user_id"] == "10001" and record["group_id"] == "20001"


@pytest.mark.parametrize(
    "command",
    [
        "紫微",
        "紫微 帮助",
        "我的命盘",
        "紫微 流年 2026",
        "人生 199006150830 男",
        "紫微 阴历1990年五月廿三日0830男",
    ],
)
def test_already_normalized_standard_commands_belong_only_to_framework(command):
    event = MentionEvent(command)
    assert fallback_command(event) is None
    assert not CompactCommandFilter().filter(event, {})


@pytest.mark.parametrize("slash", ["", "/"])
def test_real_mention_with_adapter_marker_supports_help_without_group(slash):
    command = slash + "紫薇帮助文字"
    event = MentionEvent(command, raw="@演示机器人(bot) " + command, group="")
    plugin = ZiweiPlugin(NoModelContext(), {})
    replies = asyncio.run(invoke(plugin, event))
    assert len(replies) == 1 and "紫微斗数排盘" in replies[0]
    assert archive_counts(plugin) == (0, 0, 0)


@pytest.mark.parametrize("group", ["", "20099"])
@pytest.mark.parametrize(
    "command",
    [
        "/紫微 阴历 199005230830 男",
        "/紫微 绑定 阴历 199005230830 男",
        "/人生 阴历 199005230830 男",
    ],
)
def test_mentions_cannot_bypass_group_authorization(group, command, monkeypatch):
    plugin = ZiweiPlugin(NoModelContext(), {})

    def forbidden(*args, **kwargs):
        pytest.fail("permission must precede parsing and computation")

    monkeypatch.setattr(main, "parse_request", forbidden)
    monkeypatch.setattr(plugin, "_compute_chart", forbidden)
    event = MentionEvent(command, group=group, admin=True)
    replies = asyncio.run(invoke(plugin, event))
    assert len(replies) == 1 and ("开启" in replies[0] or "群聊" in replies[0])
    assert archive_counts(plugin) == (0, 0, 0)
    assert not plugin.chart_cache.entries


@pytest.mark.parametrize(
    "components,wake",
    [
        ([At("other"), Plain("/紫微帮助")], True),
        ([At("all"), Plain("/紫微帮助")], True),
        ([Plain("@bot /紫微帮助")], True),
        ([Plain("[CQ:at,qq=bot] /紫微帮助")], True),
        ([Reply("/紫微帮助"), At("bot"), Plain("你好")], True),
        ([Plain("帮我看"), At("bot"), Plain("/紫微帮助")], True),
        ([At("bot"), Plain("请帮我 紫微帮助")], True),
        ([At("bot"), Plain("紫薇花很好看")], True),
        ([At("bot"), Plain("//紫微帮助")], True),
        ([At("bot"), At("other"), Plain("/紫微帮助")], True),
        ([At("bot"), Plain("/紫"), At("other"), Plain("微帮助")], True),
        ([At("bot"), SimpleNamespace(type="image"), Plain("/紫微帮助")], True),
        ([At("bot"), Plain("/紫微帮助")], False),
        ([At("bot")], True),
    ],
)
def test_fake_mentions_quotes_other_recipients_and_chat_are_untouched(components, wake):
    event = MentionEvent("unparsed adapter text", components=components, wake=wake)
    plugin = ZiweiPlugin(NoModelContext(), {})
    assert fallback_command(event) is None
    assert asyncio.run(invoke(plugin, event)) == []
    assert not hasattr(event, "stopped") and event.call_llm is False
    assert archive_counts(plugin) == (0, 0, 0)


def test_reply_then_self_mentions_and_split_plain_segments():
    event = MentionEvent(
        "adapter marker",
        components=[
            Reply("/紫微 关闭"),
            Plain(" \n"),
            At("bot"),
            At("bot"),
            Plain("　/紫薇帮"),
            Plain("助文字"),
        ],
    )
    command = fallback_command(event)
    assert command.command == "紫薇" and command.target == "help"
    assert command.args == "帮助文字"


def test_mentions_share_personal_bindings_and_history(rendered):
    plugin = ZiweiPlugin(NoModelContext(), {})

    async def run():
        await allow(plugin)
        event = MentionEvent("/紫微 绑定 阴历 1990年五月廿三日 0830 男")
        assert "已绑定" in (await invoke(plugin, event))[0]
        scope = capture_group(event)
        original = plugin.archive.bindings.current(scope)["chart"]
        assert original["normalized"]["original"]["calendar"] == "lunar"
        plugin.config.update(longitude=75, true_solar=False)
        assert (await invoke(plugin, MentionEvent("/我的命盘")))[0][0].type == "image"
        assert plugin.archive.bindings.current(scope)["chart"] == original
        assert (await invoke(plugin, MentionEvent("/紫微 流年 2026")))[0][
            0
        ].type == "image"
        assert (await invoke(plugin, MentionEvent("/紫微 大运 2")))[0][
            0
        ].type == "image"
        assert (
            "已更换绑定"
            in (await invoke(plugin, MentionEvent("/紫微换绑阳历1992年7月20日1430女")))[
                0
            ]
        )
        assert "已解除绑定" in (await invoke(plugin, MentionEvent("/紫微 解绑")))[0]

    asyncio.run(run())
    assert len(rendered) == 5


def test_invalid_mentioned_standard_command_keeps_error_reply():
    plugin = ZiweiPlugin(NoModelContext(), {})

    async def run():
        await allow(plugin)
        event = MentionEvent("/紫微 阴历 2024年闰二月初一日 1200 女")
        replies = await invoke(plugin, event)
        assert len(replies) == 1 and "参数有误" in replies[0]

    asyncio.run(run())
    assert archive_counts(plugin) == (0, 0, 0)
