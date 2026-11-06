"""Personal natal bindings: real scopes, snapshot integrity and mutation races."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.archive_store import ArchiveStore
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.compact_commands import parse_compact_command
from astrbot_plugin_ziwei.compact_filter import CompactCommandFilter
from astrbot_plugin_ziwei.engine import apply_decade, apply_flow
from astrbot_plugin_ziwei.main import ZiweiPlugin
from astrbot_plugin_ziwei.personal_store import natal_snapshot

from .conftest import Event
from .helpers import allow, invoke_command
from .test_command_only import NoModelContext
from .test_readings import natal


@pytest.fixture
def plugin():
    plugin = ZiweiPlugin(NoModelContext(), {})
    plugin.renderer = SimpleNamespace(render=lambda chart, theme: b"demo-png")
    return plugin


def bound(plugin, event=None):
    return plugin.archive.bindings.current(capture_group(event or Event()))


def test_cross_group_fixed_binding_and_shortcut_preserve_snapshot(plugin):
    async def run():
        first, other_group = Event(), Event(group="20002")
        await allow(plugin, first)
        await allow(plugin, other_group)
        result = await invoke_command(plugin, "绑定 " + DEMO_BIRTH, first)
        assert "已绑定" in result[0] and result[1][0].data == b"demo-png"
        original = bound(plugin)["chart"]
        # Someone else's birth in an ordinary request never silently rebinds.
        await invoke_command(plugin, "199207201430 女", first)
        plugin.config.update(longitude=101, late_zi_rule="midnight", tianma="month")
        captured = []
        plugin.renderer = SimpleNamespace(
            render=lambda chart, theme: captured.append(chart) or b"same-png"
        )
        answer = [reply async for reply in plugin.my_chart(other_group, "")]
        assert answer[0][0].data == b"same-png"
        assert captured == [original]
        assert (
            plugin.archive.get_chart(
                capture_group(other_group), bound(plugin)["source_record"]
            )
            is None
        )
        assert plugin.chart_cache.get(capture_group(first)).chart != original
        assert "1990" not in json.dumps(plugin.db)
        assert plugin.archive.records.list(source="group")["total"] == 2

    asyncio.run(run())


@pytest.mark.parametrize("other", [Event(user="10002"), Event(platform="other-bot")])
def test_user_and_instance_isolation(plugin, other):
    async def run():
        await allow(plugin)
        await invoke_command(plugin, "绑定 " + DEMO_BIRTH)
        await allow(plugin, other)
        assert "尚未绑定" in (await invoke_command(plugin, "我的命盘", other))[0]
        assert bound(plugin, other) is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "args", ["绑定 bad", "绑定", "换绑 bad", "解绑", "我的命盘", "流年 2026", "大运 2"]
)
@pytest.mark.parametrize("event", [Event(group=""), Event(group="20099", admin=True)])
def test_unauthorized_never_reads_or_computes(plugin, monkeypatch, args, event):
    def forbidden(*args, **kwargs):
        raise AssertionError("must check actual group first")

    monkeypatch.setattr(plugin.archive.bindings, "state", forbidden)
    monkeypatch.setattr(plugin, "_compute_chart", forbidden)
    reply = asyncio.run(invoke_command(plugin, args, event))
    assert "已开启" in reply[0] or "尚未开启" in reply[0]


def test_bind_replace_unbind_reload_dedup_and_history(plugin, tmp_path):
    async def run():
        await allow(plugin)
        event = Event()
        event.message_obj.message_id = "message-1"
        await invoke_command(plugin, "绑定 " + DEMO_BIRTH, event)
        state = bound(plugin)
        assert (
            "已处理" in (await invoke_command(plugin, "绑定 " + DEMO_BIRTH, event))[0]
        )
        assert "换绑" in (await invoke_command(plugin, "绑定 199207201430 女"))[0]
        assert bound(plugin)["revision"] == state["revision"]
        assert "已更换" in (await invoke_command(plugin, "换绑 199207201430 女"))[0]
        assert bound(plugin)["chart"]["sex"] == "女"
        assert "已解除" in (await invoke_command(plugin, "解绑"))[0]
        assert bound(plugin) is None
        tombstone = plugin.archive.bindings.state("qq-one", "10001")
        assert tombstone["revision"] and not tombstone["active"]
        assert plugin.archive.records.list()["total"] == 2
        # A retry of an old bind cannot undo the subsequent unbind.
        assert (
            "当前未绑定"
            in (await invoke_command(plugin, "绑定 " + DEMO_BIRTH, event))[0]
        )
        assert bound(plugin) is None
        await invoke_command(plugin, "绑定 " + DEMO_BIRTH)
        path = tmp_path / "reload.sqlite3"
        reloaded = ArchiveStore(path)
        plugin.archive.db.backup(reloaded.db)
        assert (
            reloaded.bindings.state("qq-one", "10001")["chart"]
            == bound(plugin)["chart"]
        )
        await plugin.access.set_enabled(capture_group(Event()), False)
        assert bound(plugin)
        assert "尚未开启" in (await invoke_command(plugin, "我的命盘"))[0]
        await allow(plugin)
        assert (await invoke_command(plugin, "我的命盘"))[0][0].data == b"demo-png"

    asyncio.run(run())


def test_flow_snapshot_has_no_stale_layers_and_preserves_shared_stars():
    original = natal()
    archived = json.loads(json.dumps(apply_flow(original, year=2026)))
    cleaned = natal_snapshot(archived)
    assert cleaned == original
    assert any(s["secondary"] for s in cleaned["stars"])
    for palace in cleaned["palaces"]:
        assert all(
            any(s is global_star for global_star in cleaned["stars"])
            for s in palace["stars"]
        )
    derived = apply_decade(cleaned, 2)
    assert set(derived["flow"]["layers"]) == {"大限"}
    assert not any("流年" in s["hua"] for s in derived["stars"])
    assert any("大限" in s["hua"] for s in derived["stars"])
    assert archived["flow"]["year"] == 2026


def test_personal_flow_commands_use_snapshot_and_leave_binding(plugin):
    async def run():
        await allow(plugin)
        await invoke_command(plugin, "绑定 " + DEMO_BIRTH)
        initial = bound(plugin)
        charts = []
        plugin.renderer = SimpleNamespace(
            render=lambda chart, theme: charts.append(chart) or b"png"
        )
        await invoke_command(plugin, "流年2026主题=白天")
        await invoke_command(plugin, "大运0")
        assert charts[0] == apply_flow(initial["chart"], year=2026)
        assert charts[1] == apply_decade(initial["chart"], 0)
        assert bound(plugin) == initial
        assert plugin.archive.records.list()["total"] == 3

    asyncio.run(run())


def test_existing_charts_cannot_be_selected_for_binding(plugin):
    async def run():
        event = Event(group="20002")
        own = plugin.archive.save_chart(
            capture_group(event), apply_flow(natal(), year=2026)
        )
        foreign = plugin.archive.save_chart(capture_group(Event(user="99999")), natal())
        await allow(plugin)
        listing = (await invoke_command(plugin, "绑定"))[0]
        assert "重新填写" in listing and own[:12] not in listing
        for record in (own, own[:12], foreign):
            reply = (await invoke_command(plugin, "绑定 命盘=" + record))[0]
            assert "不支持选择" in reply
        assert not bound(plugin)
        assert plugin.archive.records.list()["total"] == 2

    asyncio.run(run())


def test_legacy_charts_remain_readable_but_cannot_be_selected(plugin):
    own_scope, other_scope = capture_group(Event()), capture_group(Event(group="20002"))
    own = plugin.archive.save_chart(own_scope, natal())
    foreign = plugin.archive.save_chart(other_scope, natal())
    with plugin.archive.transaction() as db:
        db.execute(
            "UPDATE admin_records SET source='legacy',platform_id='',group_id='',"
            "user_id='' WHERE id IN (?,?)",
            (own, foreign),
        )

    async def run():
        await allow(plugin)
        for tail in ("命盘=" + own, "命盘=" + foreign, "页=1"):
            assert "不支持选择" in (await invoke_command(plugin, "绑定 " + tail))[0]
        assert not bound(plugin)
        assert plugin.archive.records.list()["total"] == 2
        assert plugin.archive.records.get(own)["chart"] == natal()

    asyncio.run(run())


@pytest.mark.parametrize(
    "tail",
    [
        "199002300830 男",
        "199006152460 男",
        DEMO_BIRTH + " 流年=2026",
        DEMO_BIRTH + " 运限=2",
        DEMO_BIRTH + " 不存在=1",
    ],
)
def test_bad_bind_input_has_no_side_effect(plugin, tail):
    async def run():
        await allow(plugin)
        replies = await invoke_command(plugin, "绑定 " + tail)
        assert replies and isinstance(replies[0], str)
        assert bound(plugin) is None
        assert plugin.archive.records.list()["total"] == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    "tail",
    ["流年 1899", "流年 1990 流年=2026", "大运 13", "我的命盘 经度=110", "解绑 now"],
)
def test_invalid_bound_options_do_not_change_binding(plugin, tail):
    async def run():
        await allow(plugin)
        await invoke_command(plugin, "绑定 " + DEMO_BIRTH)
        original = bound(plugin)
        result = await invoke_command(plugin, tail)
        assert isinstance(result[0], str)
        assert bound(plugin) == original
        assert plugin.archive.records.list()["total"] == 1

    asyncio.run(run())


def test_image_failure_keeps_valid_binding_with_text_fallback(plugin):
    async def run():
        await allow(plugin)
        plugin.renderer = None
        replies = await invoke_command(plugin, "绑定 " + DEMO_BIRTH)
        assert "已绑定" in replies[0] and "图片暂不可用" in replies[1]
        assert bound(plugin)

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["disable", "reload", "replace"])
def test_rendering_race_prevents_stale_write_or_reply(plugin, mode, monkeypatch):
    async def run():
        await allow(plugin)
        await invoke_command(plugin, "绑定 " + DEMO_BIRTH)
        original = bound(plugin)

        async def intercepted(awaitable):
            result = await awaitable
            if mode == "disable":
                await plugin.access.set_enabled(capture_group(Event()), False)
            elif mode == "reload":
                plugin.closed = True
            else:
                store = plugin.archive.bindings
                store.mutate(
                    "qq-one",
                    "10001",
                    action="unbind",
                    expected=original["revision"],
                    actor="demo-admin",
                    via="admin",
                    request_id="race",
                    fingerprint="race",
                )
            return result

        monkeypatch.setattr(
            "astrbot_plugin_ziwei.personal_service.finish_worker", intercepted
        )
        replies = await invoke_command(plugin, "我的命盘")
        assert not any(isinstance(reply, list) for reply in replies)
        assert plugin.pending == 0

    asyncio.run(run())


def test_atomic_binding_failure_rolls_back_new_record(plugin, monkeypatch):
    async def run():
        await allow(plugin)

        def failed(*args, **kwargs):
            raise ValueError("simulated archive failure")

        monkeypatch.setattr(plugin.archive.records, "add", failed)
        await invoke_command(plugin, "绑定 " + DEMO_BIRTH)
        assert bound(plugin) is None
        assert (
            plugin.archive.db.execute("SELECT count(*) FROM charts").fetchone()[0] == 0
        )

    asyncio.run(run())


def test_optimistic_replace_rejects_concurrent_write(plugin):
    async def run():
        await allow(plugin)
        replies = await asyncio.gather(
            invoke_command(plugin, "绑定 " + DEMO_BIRTH),
            invoke_command(plugin, "绑定 199207201430 女"),
        )
        assert sum("已绑定本命盘" in str(reply) for reply in replies) == 1
        assert plugin.archive.records.list()["total"] == 1

    asyncio.run(run())


def test_atlas_uses_binding_before_cache_but_explicit_archive_wins(plugin):
    async def run():
        await allow(plugin)
        await invoke_command(plugin, "绑定 " + DEMO_BIRTH)
        await invoke_command(plugin, "199207201430 女")
        scope = capture_group(Event())
        row, _ = plugin.life_atlases._archived_chart(scope, "")
        assert row["chart"] == bound(plugin)["chart"] and row["id"] == ""
        explicit, _ = plugin.life_atlases._archived_chart(
            scope, plugin.chart_cache.get(scope).chart_id
        )
        assert explicit["chart"]["sex"] == "女"

    asyncio.run(run())


@pytest.mark.parametrize(
    "command",
    [
        "紫微绑定199006150830男",
        "紫薇绑定 199006150830 男",
        "ziwei绑定199006150830男",
        "紫微我的命盘",
        "我的命盘主题=白天",
        "紫微流年2026",
        "紫薇大运2",
        "紫微解绑",
    ],
)
def test_compact_personal_commands_are_explicit_and_wake_bound(command):
    assert parse_compact_command(command)
    assert CompactCommandFilter().filter(Event(text=command), {})
    assert not CompactCommandFilter().filter(Event(text=command, wake=False), {})
    assert not CompactCommandFilter().filter(Event(text="/" + command), {})


@pytest.mark.parametrize(
    "command",
    [
        "紫微绑定人生",
        "我的命盘很好",
        "紫微流年运势",
        "紫薇解绑了吗",
        "我的命盘 主题=白天",
    ],
)
def test_natural_or_standard_commands_not_owned_by_compact_filter(command):
    assert not CompactCommandFilter().filter(Event(text=command), {})


def test_direct_shortcut_compact_and_personal_help(plugin):
    async def run():
        await allow(plugin)
        event = Event(text="紫薇绑定199006150830男")
        assert (
            await anext(plugin.compact_command(event))
        ) == "已绑定本命盘。同一机器人内各已开启的群均可发送 /我的命盘 查看。"
        event = Event(text="我的命盘主题=白天")
        replies = [reply async for reply in plugin.compact_command(event)]
        assert replies[0][0].data == b"demo-png"
        help_reply = await invoke_command(plugin, "绑定 帮助", Event(group=""))
        assert "/我的命盘" in help_reply[0]

    asyncio.run(run())


@pytest.mark.parametrize("selection", ["命盘=", "命盘=aaaaaaaa"])
def test_empty_or_ambiguous_record_selection_does_not_bind(plugin, selection):
    async def run():
        await allow(plugin)
        for suffix in ("1", "2"):
            record = plugin.archive.save_chart(capture_group(Event()), natal())
            with plugin.archive.transaction() as db:
                db.execute(
                    "UPDATE admin_records SET id=? WHERE id=?",
                    ("a" * 31 + suffix, record),
                )
        result = await invoke_command(plugin, "绑定 " + selection)
        assert result and isinstance(result[0], str)
        assert bound(plugin) is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "command", ["紫薇绑定帮助文字", "紫微绑定HELPtext", "我的命盘帮助"]
)
def test_compact_personal_help_without_group_authorization(plugin, command):
    async def run():
        event = Event(group="", text=command)
        replies = [reply async for reply in plugin.compact_command(event)]
        assert len(replies) == 1 and "/我的命盘" in replies[0]
        assert plugin.db == {}

    asyncio.run(run())


@pytest.mark.parametrize("explicit", [False, True])
def test_atlas_in_another_group_archives_bound_or_explicit_input(
    plugin, monkeypatch, explicit
):
    captured = []

    class AtlasRenderer:
        def __init__(self, font):
            pass

        def render(self, atlas, theme):
            captured.append(atlas)
            return b"atlas-png"

    monkeypatch.setattr(
        "astrbot_plugin_ziwei.life_atlas_service.LifeAtlasRenderer", AtlasRenderer
    )

    async def run():
        await allow(plugin)
        await invoke_command(plugin, "绑定 " + DEMO_BIRTH)
        event = Event(group="20002")
        await allow(plugin, event)
        args = "199207201430 女" if explicit else ""
        replies = [reply async for reply in plugin.life_atlas(event, args)]
        assert replies[0][0].data == b"atlas-png"
        chart = plugin.chart_cache.get(capture_group(event))
        assert chart.chart["sex"] == ("女" if explicit else "男")
        assert plugin.archive.get_chart(capture_group(event), chart.chart_id)
        assert plugin.archive.get_chart(capture_group(Event()), chart.chart_id) is None
        assert bound(plugin)["chart"]["sex"] == "男"
        if not explicit:
            assert captured[0]["natal"] == bound(plugin)["chart"]

    asyncio.run(run())


def test_atlas_does_not_save_or_send_after_binding_changes(plugin, monkeypatch):
    class AtlasRenderer:
        def __init__(self, font):
            pass

        def render(self, atlas, theme):
            plugin.archive.bindings.mutate(
                "qq-one",
                "10001",
                action="unbind",
                expected=bound(plugin)["revision"],
                actor="demo-admin",
                via="admin",
                request_id="race",
                fingerprint="race",
            )
            return b"obsolete-atlas"

    monkeypatch.setattr(
        "astrbot_plugin_ziwei.life_atlas_service.LifeAtlasRenderer", AtlasRenderer
    )

    async def run():
        await allow(plugin)
        await invoke_command(plugin, "绑定 " + DEMO_BIRTH)
        replies = [reply async for reply in plugin.life_atlas(Event(), "")]
        assert "绑定已变更" in replies[0]
        assert plugin.archive.records.list(kind="atlas")["total"] == 0

    asyncio.run(run())


def test_cancelled_binding_waits_for_worker_and_does_not_commit(plugin):
    started, release = threading.Event(), threading.Event()

    def render(chart, theme):
        started.set()
        assert release.wait(5)
        return b"obsolete-png"

    plugin.renderer = SimpleNamespace(render=render)

    async def run():
        await allow(plugin)
        task = asyncio.create_task(invoke_command(plugin, "绑定 " + DEMO_BIRTH))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert plugin.pending == 1 and not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert plugin.pending == 0 and bound(plugin) is None
        assert plugin.archive.records.list()["total"] == 0

    asyncio.run(run())
