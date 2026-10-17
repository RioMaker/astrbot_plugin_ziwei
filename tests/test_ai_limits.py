import asyncio
import json
import sqlite3

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.archive_store import ArchiveStore
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .test_llm_tools import Context, allow, invoke_command
from .test_readings import answer, natal


def usage(plugin, kind):
    return plugin.archive.db.execute(
        "SELECT count(*) FROM ai_usage WHERE kind=?", (kind,)
    ).fetchone()[0]


@pytest.mark.parametrize(
    "suffix,limit,kind",
    [("", 2, "natal"), (" 运限=1", 3, "decade"), (" 运限=0", 3, "decade")],
)
def test_exact_rolling_window_and_failed_attempt_does_not_extend_it(
    suffix, limit, kind
):
    plugin = ZiweiPlugin(Context(), {})
    now = [1000.0]
    plugin.archive.clock = lambda: now[0]

    async def run():
        await allow(plugin)
        for i in range(limit):
            now[0] = 1000 + i * 100
            assert (
                json.loads(await plugin.ziwei_paipan(Event(), DEMO_BIRTH + suffix))[
                    "status"
                ]
                == "ok"
            )
        now[0] = 4599.25
        blocked = json.loads(await plugin.ziwei_paipan(Event(), DEMO_BIRTH + suffix))
        assert blocked["status"] == "rate_limited" and blocked["quota"] == kind
        assert blocked["retry_after_seconds"] == 1
        assert usage(plugin, kind) == limit
        now[0] = 4600
        assert (
            json.loads(await plugin.ziwei_paipan(Event(), DEMO_BIRTH + suffix))[
                "status"
            ]
            == "ok"
        )
        blocked = json.loads(await plugin.ziwei_paipan(Event(), DEMO_BIRTH + suffix))
        assert blocked["retry_after_seconds"] == 100
        await plugin.terminate()

    asyncio.run(run())


def test_same_person_across_groups_shares_quota_and_reload_keeps_it():
    context = Context()
    plugin = ZiweiPlugin(context, {})

    async def run():
        for group in ("A", "B", "C"):
            await allow(plugin, Event(group=group))
        for group in ("A", "B"):
            assert (
                json.loads(await plugin.ziwei_paipan(Event(group=group), DEMO_BIRTH))[
                    "status"
                ]
                == "ok"
            )
        assert (
            json.loads(await plugin.ziwei_paipan(Event(group="C"), DEMO_BIRTH))[
                "status"
            ]
            == "rate_limited"
        )
        await plugin.terminate()
        restored = ZiweiPlugin(context, {})
        assert (
            json.loads(await restored.ziwei_paipan(Event(group="A"), DEMO_BIRTH))[
                "status"
            ]
            == "rate_limited"
        )
        assert (
            json.loads(
                await restored.ziwei_paipan(Event(group="A", user="other"), DEMO_BIRTH)
            )["status"]
            == "ok"
        )
        other_platform = Event(group="A", platform="second-platform")
        await allow(restored, other_platform)
        assert (
            json.loads(await restored.ziwei_paipan(other_platform, DEMO_BIRTH))[
                "status"
            ]
            == "ok"
        )
        await restored.terminate()

    asyncio.run(run())


def test_manual_commands_and_annual_generation_do_not_consume_limited_buckets():
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        await allow(plugin)
        for _ in range(3):
            await plugin.ziwei_paipan(Event(), DEMO_BIRTH)
        for suffix in ("", " 运限=1"):
            for _ in range(4):
                replies = await invoke_command(plugin, DEMO_BIRTH + suffix + " 文字")
                assert any("命宫" in str(r) for r in replies)
        for _ in range(3):
            assert (
                json.loads(
                    await plugin.ziwei_paipan(Event(), DEMO_BIRTH + " 流年=2026")
                )["status"]
                == "ok"
            )
        assert usage(plugin, "natal") == 2 and usage(plugin, "decade") == 0
        assert usage(plugin, "question") == 0
        assert (
            "尚未开启"
            in (await invoke_command(plugin, DEMO_BIRTH, Event(group="closed")))[0]
        )
        await plugin.terminate()

    asyncio.run(run())


def test_concurrent_reservations_and_failed_computation_refund(monkeypatch):
    plugin = ZiweiPlugin(Context(), {})
    original = plugin._compute_chart

    async def slow(request):
        await asyncio.sleep(0.01)
        return await original(request)

    async def broken(request):
        raise OSError("synthetic failure")

    async def run():
        await allow(plugin)
        assert (
            json.loads(await plugin.ziwei_paipan(Event(), "invalid"))["status"]
            == "invalid_input"
        )
        monkeypatch.setattr(plugin, "_compute_chart", broken)
        assert (
            json.loads(await plugin.ziwei_paipan(Event(), DEMO_BIRTH))["status"]
            == "unavailable"
        )
        assert usage(plugin, "natal") == 0
        monkeypatch.setattr(plugin, "_compute_chart", slow)
        responses = await asyncio.gather(
            *(plugin.ziwei_paipan(Event(), DEMO_BIRTH) for _ in range(4))
        )
        assert sorted(json.loads(r)["status"] for r in responses) == [
            "ok",
            "ok",
            "rate_limited",
            "rate_limited",
        ]
        assert plugin.pending == 0
        await plugin.terminate()

    asyncio.run(run())


def test_question_limit_deduplicates_tools_and_allows_feedback_and_replay(monkeypatch):
    plugin = ZiweiPlugin(Context(), {})
    now = [1000.0]
    plugin.archive.clock = lambda: now[0]

    class Renderer:
        def render(self, chart, theme):
            return b"synthetic-image"

    plugin.renderer = Renderer()

    async def run():
        await allow(plugin)
        chart = json.loads(await plugin.ziwei_paipan(Event(), DEMO_BIRTH))
        await plugin.ziwei_paipan(Event(), DEMO_BIRTH)
        first = Event(text="分析事业")
        first.message_obj.message_id = "question-one"
        reading = json.loads(await plugin.ziwei_analysis_context(first, topic="career"))
        rid = reading["reading_id"]
        again = Event(text="分析事业")
        again.message_obj.message_id = "question-one"
        assert (
            json.loads(await plugin.ziwei_analysis_context(again, topic="career"))[
                "reading_id"
            ]
            == rid
        )
        assert (
            json.loads(await plugin.ziwei_query_knowledge(first, rid))["status"] == "ok"
        )
        assert usage(plugin, "question") == 1
        await answer(plugin, first)
        for i in range(5):
            result = json.loads(
                await plugin.ziwei_continue_reading(Event(text=f"追问{i}"), rid)
            )
            assert result["status"] == "ok"
        assert usage(plugin, "question") == 6
        blocked = json.loads(
            await plugin.ziwei_continue_reading(Event(text="第七问"), rid)
        )
        assert (
            blocked["status"] == "rate_limited"
            and blocked["retry_after_seconds"] == 3600
        )
        assert (
            json.loads(await plugin.ziwei_analysis_context(Event(text="新问题")))[
                "status"
            ]
            == "rate_limited"
        )
        assert (
            json.loads(await plugin.ziwei_query_knowledge(Event(), rid))["status"]
            == "rate_limited"
        )
        assert (
            json.loads(await plugin.ziwei_reference_cases(Event(), rid))["status"]
            == "rate_limited"
        )
        assert (
            json.loads(await plugin.ziwei_reading_history(Event()))["status"]
            == "rate_limited"
        )
        feedback = json.loads(
            await plugin.ziwei_continue_reading(
                Event(text="谢谢，反馈不符"), rid, "feedback"
            )
        )
        assert feedback["status"] == "ok" and "knowledge" not in feedback
        assert usage(plugin, "question") == 6

        async def forbidden(*args, **kwargs):
            raise AssertionError("replay must not compute")

        monkeypatch.setattr(plugin, "_compute_chart", forbidden)
        plugin.chart_cache.clear()
        count = len(plugin.archive.list_charts(capture_group(first)))
        for _ in range(3):
            replay = json.loads(
                await plugin.ziwei_get_chart(
                    Event(), chart["chart_id"], send_image=True
                )
            )
            assert (
                replay["image_status"] == "sent"
                and replay["chart_id"] == chart["chart_id"]
            )
        assert len(plugin.context.sent) == 3
        assert usage(plugin, "natal") == 2 and usage(plugin, "question") == 6
        assert len(plugin.archive.list_charts(capture_group(first))) == count
        assert (
            json.loads(
                await plugin.ziwei_get_chart(
                    Event(user="other"), chart["chart_id"], send_image=True
                )
            )["status"]
            == "not_found"
        )
        now[0] = 4600
        assert (
            json.loads(
                await plugin.ziwei_continue_reading(Event(text="恢复提问"), rid)
            )["status"]
            == "ok"
        )
        await plugin.terminate()

    asyncio.run(run())


def test_v1_database_upgrades_without_changing_charts_and_discards_pending(tmp_path):
    path = tmp_path / "archive.sqlite3"
    store = ArchiveStore(path)
    scope = capture_group(Event())
    chart_id = store.save_chart(scope, natal())
    token = store.reserve_chart(scope, "natal")
    assert token
    store.close()
    resumed = ArchiveStore(path)
    assert resumed.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 0
    resumed.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE ai_usage")
        db.execute("PRAGMA user_version=1")
        db.execute("INSERT INTO groups_state VALUES ('unchanged',1)")
    upgraded = ArchiveStore(path)
    assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 5
    assert (
        upgraded.db.execute(
            "SELECT enabled FROM groups_state WHERE group_key='unchanged'"
        ).fetchone()[0]
        == 1
    )
    assert upgraded.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 0
    assert upgraded.get_chart(scope, chart_id) is not None
    upgraded.close()


def test_replay_rechecks_authorization_before_delivery(monkeypatch):
    plugin = ZiweiPlugin(Context(), {})
    event = Event()
    chart_id = plugin.archive.save_chart(capture_group(event), natal())
    plugin.renderer = type("Renderer", (), {"render": lambda *a, **k: "image.png"})()
    checks = []

    async def access(scope):
        checks.append(scope)
        return None if len(checks) == 1 else "本群已关闭"

    monkeypatch.setattr(plugin, "_chart_access", access)

    async def run():
        result = json.loads(
            await plugin.ziwei_get_chart(event, chart_id, send_image=True)
        )
        assert result["status"] == "denied" and "data" not in result
        assert not plugin.context.sent and plugin.pending == 0
        await plugin.terminate()

    asyncio.run(run())


def test_storage_failure_refunds_but_delivery_failure_preserves_quota(monkeypatch):
    plugin = ZiweiPlugin(Context(failure=True), {})
    save = plugin.archive.save_chart
    plugin.renderer = type("Renderer", (), {"render": lambda *a, **k: b"synthetic"})()

    def broken_save(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic storage failure")

    async def run():
        await allow(plugin)
        monkeypatch.setattr(plugin.archive, "save_chart", broken_save)
        failed = json.loads(await plugin.ziwei_paipan(Event(), DEMO_BIRTH))
        assert failed["status"] == "unavailable" and usage(plugin, "natal") == 0
        monkeypatch.setattr(plugin.archive, "save_chart", save)
        result = json.loads(
            await plugin.ziwei_paipan(Event(), DEMO_BIRTH, send_image=True)
        )
        assert (
            result["archive_status"] == "saved" and result["image_status"] == "failed"
        )
        assert usage(plugin, "natal") == 1
        assert (
            plugin.archive.db.execute("SELECT pending FROM ai_usage").fetchone()[0] == 0
        )
        plugin.context.failure = False
        replay = json.loads(
            await plugin.ziwei_get_chart(Event(), result["chart_id"], send_image=True)
        )
        assert replay["image_status"] == "sent" and usage(plugin, "natal") == 1
        await plugin.terminate()

    asyncio.run(run())
