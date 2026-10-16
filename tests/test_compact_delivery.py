"""Awakened compact-command delivery with local AstrBot doubles and fictional data."""

import asyncio
from copy import deepcopy

import pytest

from astrbot_plugin_ziwei import life_atlas_service, main
from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.compact_filter import CompactCommandFilter
from astrbot_plugin_ziwei.engine import build_chart
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .test_llm_tools import Context, allow

DEMO = "199006150830男"
ALIASES = ("紫微", "紫薇", "紫微排盘", "紫薇排盘", "ziwei")


@pytest.fixture
def rendered(monkeypatch):
    calls = []

    class NatalRenderer:
        def __init__(self, *args):
            pass

        def render(self, chart, theme="day"):
            calls.append(("natal", deepcopy(chart), theme))
            return b"natal-png"

    class AtlasRenderer:
        def __init__(self, *args):
            pass

        def render(self, atlas, theme="night"):
            calls.append(("atlas", deepcopy(atlas["natal"]), theme))
            return b"atlas-png"

    monkeypatch.setattr(main, "Renderer", NatalRenderer)
    monkeypatch.setattr(life_atlas_service, "LifeAtlasRenderer", AtlasRenderer)
    return calls


def archive_counts(plugin):
    return tuple(
        plugin.archive.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("charts", "life_atlases", "ai_usage")
    )


async def invoke_compact(plugin, event):
    return [reply async for reply in plugin.compact_command(event)]


async def invoke_standard(plugin, event, target, args):
    method = plugin.life_atlas if target == "atlas" else plugin.ziwei
    return [reply async for reply in method(event, args)]


def assert_image(replies, kind):
    assert len(replies) == 1 and len(replies[0]) == 1
    assert replies[0][0].type == "image"
    assert replies[0][0].data == f"{kind}-png".encode()


def assert_claimed_without_llm_fallback(event, original):
    assert event.message_str == original
    assert event.stopped
    # AstrBot's should_call_llm(True) suppresses its automatic default LLM path.
    assert event.call_llm is True


@pytest.mark.parametrize(
    "message,kind",
    [("人生" + DEMO, "atlas")]
    + [(name + DEMO, "natal") for name in ALIASES]
    + [(name + "图谱" + DEMO, "atlas") for name in ALIASES],
)
def test_compact_names_and_existing_aliases_deliver_and_archive_once(
    message, kind, rendered
):
    plugin = ZiweiPlugin(Context(), {"output_mode": "image"})
    event = Event(text=message)
    assert CompactCommandFilter().filter(event, {})
    assert event.message_str == message

    async def run():
        await allow(plugin)
        assert_image(await invoke_compact(plugin, event), kind)

    asyncio.run(run())
    assert_claimed_without_llm_fallback(event, message)
    assert len(rendered) == 1 and rendered[0][0] == kind
    request = parse_request("1990-06-15 08:30 男")
    assert rendered[0][1] == build_chart(request.birth, request.profile)
    assert archive_counts(plugin) == (1, int(kind == "atlas"), 0)
    assert plugin.pending == 0


@pytest.mark.parametrize(
    "target,args,kind",
    [
        ("atlas", DEMO, "atlas"),
        ("natal", DEMO, "natal"),
        ("natal", "图谱" + DEMO, "atlas"),
        ("natal", "图谱 " + DEMO, "atlas"),
    ],
)
def test_standard_entry_normalizes_glued_arguments_without_second_route(
    target, args, kind, rendered
):
    plugin = ZiweiPlugin(Context(), {})
    message = ("人生 " if target == "atlas" else "紫微 ") + args
    event = Event(text=message)
    assert not CompactCommandFilter().filter(event, {})

    async def run():
        await allow(plugin)
        assert await invoke_compact(plugin, event) == []
        assert not hasattr(event, "stopped")
        assert_image(await invoke_standard(plugin, event, target, args), kind)
        assert await invoke_compact(plugin, event) == []

    asyncio.run(run())
    assert_claimed_without_llm_fallback(event, message)
    assert len(rendered) == 1
    assert archive_counts(plugin) == (1, int(kind == "atlas"), 0)


@pytest.mark.parametrize("name", ["人生", "紫微", "紫薇图谱"])
@pytest.mark.parametrize("private", [False, True])
def test_compact_requests_do_not_bypass_group_authorization(
    name, private, rendered, monkeypatch
):
    plugin = ZiweiPlugin(Context(), {})
    event = Event(text=name + DEMO, group="" if private else "20001", admin=True)

    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized compact requests must not parse or compute")

    monkeypatch.setattr(main, "parse_request", forbidden)
    monkeypatch.setattr(main, "build_chart", forbidden)
    monkeypatch.setattr(life_atlas_service, "parse_life_atlas_request", forbidden)
    assert asyncio.run(invoke_compact(plugin, event)) == []
    assert_claimed_without_llm_fallback(event, name + DEMO)
    assert not rendered
    assert archive_counts(plugin) == (0, 0, 0)
    assert not plugin.chart_cache.entries and plugin.pending == 0


@pytest.mark.parametrize(
    "message",
    [
        "人生199006150830",
        "人生199013150830男",
        "人生199006152400男",
        "紫微199006150830男未知=1",
        "紫微图谱199006150830男流年=2026",
        "人生199006150830男主题=不存在",
    ],
)
def test_invalid_compact_commands_are_silent_and_have_no_side_effects(
    message, rendered, caplog
):
    plugin = ZiweiPlugin(Context(), {})
    event = Event(text=message)
    assert CompactCommandFilter().filter(event, {})

    async def run():
        await allow(plugin)
        assert await invoke_compact(plugin, event) == []

    asyncio.run(run())
    assert_claimed_without_llm_fallback(event, message)
    assert not rendered and not plugin.chart_cache.entries
    assert archive_counts(plugin) == (0, 0, 0)
    assert plugin.pending == 0
    assert "1990" not in caplog.text


@pytest.mark.parametrize("target", ["atlas", "natal"])
def test_glued_invalid_arguments_on_standard_route_are_silent(target, rendered):
    plugin = ZiweiPlugin(Context(), {})
    args = "199013150830男"
    event = Event(text=("人生 " if target == "atlas" else "紫微 ") + args)

    async def run():
        await allow(plugin)
        assert await invoke_standard(plugin, event, target, args) == []

    asyncio.run(run())
    assert_claimed_without_llm_fallback(event, event.message_str)
    assert not rendered and archive_counts(plugin) == (0, 0, 0)


@pytest.mark.parametrize("target", ["atlas", "natal"])
def test_standard_spaced_bad_input_keeps_existing_error_message(target, rendered):
    plugin = ZiweiPlugin(Context(), {})
    args = "1990-13-15 08:30 男"
    message = ("人生 " if target == "atlas" else "紫微 ") + args
    event = Event(text=message)
    assert not CompactCommandFilter().filter(event, {})

    async def run():
        await allow(plugin)
        replies = await invoke_standard(plugin, event, target, args)
        assert len(replies) == 1 and "参数有误" in replies[0]
        assert "查看格式" in replies[0]

    asyncio.run(run())
    assert event.message_str == message and event.stopped
    assert not rendered and archive_counts(plugin) == (0, 0, 0)


@pytest.mark.parametrize(
    "message,wake",
    [
        ("人生" + DEMO, False),
        ("紫微" + DEMO, False),
        ("/人生" + DEMO, True),
        ("人生总有起伏", True),
        ("我想看人生" + DEMO, True),
        ("紫微斗数很好玩", True),
        ("紫薇开门", True),
        ("人生 " + DEMO, True),
        ("紫微 图谱 " + DEMO, True),
    ],
)
def test_unawakened_chat_and_standard_commands_are_untouched(message, wake, rendered):
    plugin = ZiweiPlugin(Context(), {})
    event = Event(text=message, wake=wake)
    assert not CompactCommandFilter().filter(event, {})
    assert asyncio.run(invoke_compact(plugin, event)) == []
    assert event.message_str == message
    assert not hasattr(event, "stopped") and event.call_llm is False
    assert not rendered and archive_counts(plugin) == (0, 0, 0)


@pytest.mark.parametrize("owner", [False, True])
def test_glued_management_keeps_original_authorization(owner, rendered):
    plugin = ZiweiPlugin(Context(), {})
    event = Event(text="紫薇开启", role="owner" if owner else "member")
    assert CompactCommandFilter().filter(event, {})

    async def run():
        replies = await invoke_compact(plugin, event)
        assert len(replies) == 1
        assert ("已开启" if owner else "仅群主") in replies[0]
        assert await plugin.access.enabled(capture_group(event)) is owner

    asyncio.run(run())
    assert_claimed_without_llm_fallback(event, "紫薇开启")
    assert not rendered and archive_counts(plugin) == (0, 0, 0)


def test_existing_command_registration_is_unchanged():
    assert ZiweiPlugin.ziwei.command_metadata == {
        "name": "紫微",
        "alias": {"紫薇", "紫微排盘", "紫薇排盘", "ziwei"},
    }
    assert ZiweiPlugin.life_atlas.command_metadata == {"name": "人生"}
    assert ZiweiPlugin.compact_command.custom_filter_class is CompactCommandFilter
