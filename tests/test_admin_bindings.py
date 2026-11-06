"""Authenticated admin bindings, conflict detection and real-event readback."""

import asyncio
import json

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.engine import apply_flow

from . import test_admin_page
from .conftest import Event
from .helpers import allow, invoke_command
from .test_readings import natal

page = test_admin_page.page


def payload(**changes):
    return (
        dict(
            platform_id="qq-one",
            user_id="10001",
            action="bind",
            revision="",
            request_id="demo-binding-0001",
            args=DEMO_BIRTH,
            record_id="",
        )
        | changes
    )


@pytest.mark.parametrize("route", ["bindings", "bindings/target", "bindings/save"])
def test_bindings_routes_require_real_dashboard_identity(page, route):
    plugin, request, _, call = page
    request.username = None
    result = asyncio.run(call(route, payload() if route.endswith("save") else None))
    assert result["status"] == 401
    assert plugin.archive.bindings.users()["total"] == 0


def test_admin_bind_readback_replace_unbind_and_stale_retry(page):
    plugin, request, _, call = page

    async def run():
        first = await call("bindings/save", payload())
        assert first["status"] == 200
        state = first["data"]
        assert (
            state["active"]
            and state["via"] == "admin"
            and state["actor"] == "demo-admin"
        )
        assert "chart" not in state
        assert plugin.archive.records.list(source="web")["total"] == 1
        assert (
            plugin.archive.db.execute("SELECT count(*) FROM charts").fetchone()[0] == 0
        )
        assert (
            plugin.archive.db.execute("SELECT count(*) FROM groups_state").fetchone()[0]
            == 0
        )
        assert (
            plugin.db == {} and plugin.chart_cache.get(capture_group(Event())) is None
        )
        for group in ("20001", "20002"):
            event = Event(group=group)
            await allow(plugin, event)
            answer = [reply async for reply in plugin.my_chart(event, "")]
            assert answer[0][0].data == b"demo-png"
        request.query = {"platform_id": "qq-one", "user_id": "10001", "render": "1"}
        preview = await call("bindings/target")
        assert preview["data"]["preview"]["source"] == "binding"
        assert "chart" not in preview["data"]["preview"]
        replacement = await call(
            "bindings/save",
            payload(
                action="replace",
                revision=state["revision"],
                request_id="demo-binding-0002",
                args="199207201430 女",
            ),
        )
        assert (
            replacement["status"] == 200
            and replacement["data"]["summary"]["sex"] == "女"
        )
        stale = await call(
            "bindings/save",
            payload(
                action="replace",
                revision=state["revision"],
                request_id="demo-binding-0003",
            ),
        )
        assert stale["status"] == 409
        unbound = await call(
            "bindings/save",
            payload(
                action="unbind",
                revision=replacement["data"]["revision"],
                request_id="demo-binding-0004",
                args="",
            ),
        )
        assert unbound["status"] == 200 and not unbound["data"]["active"]
        retry = await call("bindings/save", payload())
        assert retry["status"] == 200 and retry["data"]["replayed"]
        assert not retry["data"]["active"]
        assert plugin.archive.records.list(source="web")["total"] == 2
        audit = plugin.archive.db.execute(
            "SELECT actor,via,action FROM binding_changes ORDER BY created"
        ).fetchall()
        assert [tuple(row) for row in audit] == [
            ("demo-admin", "admin", action) for action in ("bind", "replace", "unbind")
        ]

    asyncio.run(run())


def test_admin_existing_record_uses_natal_even_from_another_user(page):
    plugin, _, _, call = page
    record = plugin.archive.save_chart(
        capture_group(Event(user="10002")), apply_flow(natal(), year=2026)
    )
    result = asyncio.run(call("bindings/save", payload(args="", record_id=record)))
    assert result["status"] == 200
    assert plugin.archive.bindings.state("qq-one", "10001")["chart"] == natal()
    assert plugin.archive.bindings.state("qq-one", "10002")["active"] is False
    assert plugin.archive.records.list()["total"] == 1


def test_parallel_admin_retry_creates_one_record_and_actor_namespace(page):
    plugin, request, _, call = page

    async def run():
        results = await asyncio.gather(
            call("bindings/save", payload()), call("bindings/save", payload())
        )
        assert all(result["status"] == 200 for result in results)
        assert results[0]["data"]["revision"] == results[1]["data"]["revision"]
        assert plugin.archive.records.list()["total"] == 1
        request.username = "other-admin"
        other = await call("bindings/save", payload(user_id="10002"))
        assert other["status"] == 200
        assert plugin.archive.records.list()["total"] == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"platform_id": ""},
        {"user_id": None},
        {"user_id": "x\n1"},
        {"actor": "pretend-admin"},
        {"group_id": "20001"},
        {"action": "delete"},
        {"revision": 1},
        {"request_id": "short"},
        {"record_id": "x" * 32, "args": ""},
        {"args": ""},
        {"args": []},
        {"record_id": "a" * 32},
        {"args": DEMO_BIRTH + " 运限=2"},
        {"args": "199002300830 男"},
        {"action": "unbind"},
    ],
)
def test_invalid_admin_mutations_never_store_data(page, changes):
    plugin, _, _, call = page
    result = asyncio.run(call("bindings/save", payload(**changes)))
    assert result["status"] == 400
    assert plugin.archive.records.list()["total"] == 0
    assert plugin.archive.bindings.users()["total"] == 0


def test_unknown_record_and_unbound_replace_are_explicit_errors(page):
    _, _, _, call = page
    assert (
        asyncio.run(call("bindings/save", payload(record_id="a" * 32, args="")))[
            "status"
        ]
        == 404
    )
    assert (
        asyncio.run(call("bindings/save", payload(action="replace")))["status"] == 409
    )


def test_admin_request_fingerprint_prevents_reusing_key_for_another_user(page):
    plugin, _, _, call = page

    async def run():
        assert (await call("bindings/save", payload()))["status"] == 200
        assert (await call("bindings/save", payload(user_id="10002")))["status"] == 409
        assert plugin.archive.bindings.users()["total"] == 1

    asyncio.run(run())


def test_list_deduplicates_groups_preserves_instance_and_filters(page):
    plugin, request, _, call = page
    for group in ("20001", "20002"):
        event = Event(group=group)
        event.message_obj.raw_message["sender"]["nickname"] = "演示昵称"
        plugin.archive.save_chart(capture_group(event), natal())
    plugin.archive.save_chart(capture_group(Event(platform="qq-two")), natal())

    async def run():
        request.query = {}
        listed = await call("bindings")
        assert listed["data"]["total"] == 2
        assert set(listed["data"]["platforms"]) == {"qq-one", "qq-two"}
        request.query = {"platform": "qq-one", "search": "演示"}
        selected = await call("bindings")
        assert selected["data"]["total"] == 1
        assert "civil" not in json.dumps(selected["data"])
        request.query = {"page": "0"}
        assert (await call("bindings"))["status"] == 400

    asyncio.run(run())


def test_queued_admin_save_stops_on_reload(page):
    plugin, _, _, call = page

    async def run():
        await plugin.gate.acquire()
        await plugin.gate.acquire()
        task = asyncio.create_task(call("bindings/save", payload()))
        await asyncio.sleep(0)
        plugin.closed = True
        plugin.gate.release()
        plugin.gate.release()
        result = await task
        assert result["status"] == 503
        assert plugin.archive.records.list()["total"] == 0
        assert plugin.pending == 0

    asyncio.run(run())


def test_binding_survives_config_change_and_preview_rejects_concurrent_unbind(
    page, monkeypatch
):
    plugin, request, _, call = page

    async def run():
        await call("bindings/save", payload())
        state = plugin.archive.bindings.state("qq-one", "10001")
        plugin.config.update(longitude=80, true_solar=False)

        async def intercepted(awaitable):
            result = await awaitable
            plugin.archive.bindings.mutate(
                "qq-one",
                "10001",
                action="unbind",
                expected=state["revision"],
                actor="demo-admin",
                via="admin",
                request_id="race",
                fingerprint="race",
            )
            return result

        monkeypatch.setattr(
            "astrbot_plugin_ziwei.admin_service.finish_worker", intercepted
        )
        request.query = {"platform_id": "qq-one", "user_id": "10001", "render": "1"}
        assert (await call("bindings/target"))["status"] == 409

    asyncio.run(run())


def test_chat_cannot_select_admin_record_before_explicit_admin_binding(page):
    plugin, _, _, call = page

    async def run():
        result = await call("bindings/save", payload(user_id="10002"))
        source = result["data"]["source_record"]
        await allow(plugin)
        answer = await invoke_command(plugin, "绑定 命盘=" + source)
        assert "未找到" in answer[0]
        assert not plugin.archive.bindings.state("qq-one", "10001")["active"]

    asyncio.run(run())
