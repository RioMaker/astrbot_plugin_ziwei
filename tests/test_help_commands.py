"""Route whole help commands through the wake-aware local AstrBot adapters."""

import asyncio

import pytest

from astrbot_plugin_ziwei import main
from astrbot_plugin_ziwei.commands import HELP
from astrbot_plugin_ziwei.compact_commands import parse_compact_command
from astrbot_plugin_ziwei.compact_filter import CompactCommandFilter
from astrbot_plugin_ziwei.life_atlas_service import LIFE_ATLAS_HELP
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .test_command_only import NoModelContext


def forbid_chart_work(*args, **kwargs):
    pytest.fail(
        "help must not parse birth data, compute a chart, or require group access"
    )


def assert_no_chart_side_effects(plugin, event, original):
    assert event.message_str == original
    assert event.stopped and event.call_llm is True
    assert plugin.pending == 0 and not plugin.chart_cache.entries
    assert plugin.db == {}
    for table in ("charts", "life_atlases", "ai_usage"):
        assert (
            plugin.archive.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            == 0
        )


async def dispatch_help(plugin, event):
    # WakingCheck removes the configured slash before either plugin filter runs.
    command = parse_compact_command("/" + event.message_str)
    assert command is not None
    remainder = event.message_str[len(command.command) :]
    compact = CompactCommandFilter().filter(event, {})
    assert compact is bool(remainder and not remainder[0].isspace())
    if compact:
        return [reply async for reply in plugin.compact_command(event)]
    method = plugin.life_atlas if command.command == "人生" else plugin.ziwei
    return [reply async for reply in method(event, remainder.strip())]


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize(
    "message,text_only",
    [
        ("紫薇 help", False),
        ("紫薇 帮助", False),
        ("紫薇help", False),
        ("紫薇帮助", False),
        ("紫微help", False),
        ("紫微帮助", False),
        ("紫薇排盘帮助", False),
        ("紫微排盘help", False),
        ("ziweihelp", False),
        ("紫薇帮助文字", True),
        ("紫薇helptext", True),
        ("紫薇帮助 文字", True),
        ("紫薇 HELP  TEXT", True),
        ("紫微 HELPtext", True),
        ("ziweihelptext", True),
        ("紫微排盘帮助文字", True),
        ("紫薇排盘帮助文字", True),
    ],
)
def test_spaced_and_glued_help_replies_once_without_group_access(
    monkeypatch, private, message, text_only
):
    plugin = ZiweiPlugin(NoModelContext(), {})
    calls = []

    class HelpImage:
        def render(self, theme):
            calls.append(theme)
            return b"help-png"

    plugin.help_renderer = HelpImage()
    monkeypatch.setattr(main, "parse_request", forbid_chart_work)
    monkeypatch.setattr(main, "build_chart", forbid_chart_work)
    monkeypatch.setattr(plugin, "_group_access", forbid_chart_work)
    event = Event(text=message, group="" if private else "20001")
    replies = asyncio.run(dispatch_help(plugin, event))
    assert len(replies) == 1
    if text_only:
        assert replies == [HELP] and calls == []
    else:
        assert len(replies[0]) == 1 and replies[0][0].type == "image"
        assert replies[0][0].data == b"help-png" and calls == ["night"]
    assert_no_chart_side_effects(plugin, event, message)


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize(
    "message",
    [
        "人生help",
        "人生帮助",
        "紫薇图谱帮助",
        "紫薇图谱helptext",
        "紫薇 图谱帮助文字",
    ],
)
def test_atlas_help_keeps_its_own_help_without_spaces(monkeypatch, private, message):
    plugin = ZiweiPlugin(NoModelContext(), {})
    monkeypatch.setattr(plugin, "_chart_access", forbid_chart_work)
    monkeypatch.setattr(plugin, "_compute_chart", forbid_chart_work)
    event = Event(text=message, group="" if private else "20001")
    assert asyncio.run(dispatch_help(plugin, event)) == [LIFE_ATLAS_HELP]
    assert_no_chart_side_effects(plugin, event, message)


@pytest.mark.parametrize(
    "message,wake",
    [
        ("紫薇帮助我", True),
        ("紫薇helpful", True),
        ("紫薇帮助文字说明", True),
        ("紫薇helptextx", True),
        ("帮我看紫薇帮助", True),
        ("紫薇图谱帮助我", True),
        ("紫薇帮助", False),
        ("/紫薇帮助", True),
    ],
)
def test_compact_help_does_not_consume_chat_or_bypass_wake_state(message, wake):
    plugin = ZiweiPlugin(NoModelContext(), {})
    event = Event(text=message, wake=wake)
    assert not CompactCommandFilter().filter(event, {})

    async def invoke():
        return [reply async for reply in plugin.compact_command(event)]

    assert asyncio.run(invoke()) == []
    assert event.message_str == message
    assert not hasattr(event, "stopped") and event.call_llm is False
    assert not plugin.chart_cache.entries and plugin.db == {}
