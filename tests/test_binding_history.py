"""Permanent subject-owned records, audit links and additive legacy recovery."""

import asyncio
import json
import sqlite3

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.archive_store import ArchiveStore
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.engine import apply_flow
from astrbot_plugin_ziwei.personal_store import encode

from . import test_admin_page
from .conftest import Event
from .helpers import allow, invoke_command
from .test_admin_bindings import payload
from .test_readings import natal

page = test_admin_page.page


def test_every_successful_chat_binding_keeps_a_distinct_chart_and_audit(page, tmp_path):
    plugin, _, _, _ = page

    async def run():
        await allow(plugin)
        for command in (DEMO_BIRTH, "绑定 " + DEMO_BIRTH, "换绑 " + DEMO_BIRTH):
            await invoke_command(plugin, command)
        before = plugin.archive.bindings.history.list("qq-one", "10001")
        assert before["total"] == 2
        newer, older = before["items"]
        assert newer["record_id"] != older["record_id"]
        assert newer["previous_record"] == older["record_id"]
        assert all(item["group_id"] == "20001" for item in before["items"])
        assert all(item["actor"] == "10001" for item in before["items"])
        charts = [
            plugin.archive.records.get(i["record_id"])["chart"] for i in (newer, older)
        ]
        assert charts[0] == charts[1]
        plugin.config.update(longitude=80, true_solar=False)
        await invoke_command(plugin, "解绑")
        assert (
            plugin.archive.records.list(platform="qq-one", user="10001")["total"] == 3
        )
        after = plugin.archive.bindings.history.list("qq-one", "10001")
        assert after["items"][1:] == before["items"]
        assert after["items"][0]["record_id"] == newer["record_id"]
        assert after["items"][0]["action"] == "unbind"
        path = tmp_path / "history.sqlite3"
        copied = sqlite3.connect(path)
        plugin.archive.db.backup(copied)
        copied.close()
        reloaded = ArchiveStore(path)
        assert reloaded.bindings.history.list("qq-one", "10001") == after
        assert reloaded.records.get(older["record_id"])["chart"] == charts[1]

    asyncio.run(run())


def test_admin_history_and_records_belong_to_target_not_actor(page):
    plugin, request, _, call = page
    for event in (Event(), Event(user="100010"), Event(platform="qq-two")):
        plugin.archive.save_chart(capture_group(event), natal())
    groups_before = list(plugin.archive.db.execute("SELECT * FROM groups_state"))

    async def run():
        first = await call("bindings/save", payload())
        request.username = "second-admin"
        second_payload = payload(
            action="replace",
            revision=first["data"]["revision"],
            request_id="demo-history-0002",
            args="199207201430 女",
        )
        second = await call("bindings/save", second_payload)
        assert second["status"] == 200
        assert (await call("bindings/save", second_payload))["data"]["replayed"]
        request.query = {"platform_id": "qq-one", "user_id": "10001"}
        history = (await call("bindings/history"))["data"]
        assert history["total"] == 2
        assert "chart" not in json.dumps(history)
        newer, older = history["items"]
        assert newer["summary"]["sex"] == "女"
        assert newer["previous_summary"]["sex"] == "男"
        assert newer["actor"] == "second-admin"
        assert older["actor"] == "demo-admin"
        assert newer["previous_record"] == older["record_id"]
        for item in history["items"]:
            record = plugin.archive.records.get(item["record_id"])
            assert (record["platform_id"], record["user_id"]) == ("qq-one", "10001")
            assert record["actor"] == item["actor"] and record["group_id"] == ""
            assert record["summary"]["binding_action"] == item["action"]
            preview = await call("records/<record_id>", record_id=record["id"])
            assert preview["status"] == 200 and preview["data"]["image"]
        request.query = {"platform": "qq-one", "user": "10001"}
        records = (await call("records"))["data"]
        assert records["total"] == 3
        assert all(i["user_id"] == "10001" for i in records["items"])
        assert (
            plugin.archive.db.execute("SELECT count(*) FROM charts").fetchone()[0] == 3
        )
        assert (
            list(plugin.archive.db.execute("SELECT * FROM groups_state"))
            == groups_before
        )
        assert plugin.db == {}

    asyncio.run(run())


def test_history_failure_rolls_back_chart_binding_and_audit(page, monkeypatch):
    plugin, _, _, call = page

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("simulated history write failure")

    monkeypatch.setattr(plugin.archive.bindings.history, "record", fail)
    assert asyncio.run(call("bindings/save", payload()))["status"] == 500
    assert plugin.archive.records.list()["total"] == 0
    assert not plugin.archive.bindings.state("qq-one", "10001")["active"]
    assert plugin.archive.bindings.history.list("qq-one", "10001")["total"] == 0


def test_history_pagination_is_exact_and_rejects_unloaded_plugin(page):
    plugin, request, _, call = page
    store = plugin.archive.bindings
    chart, revision = natal(), ""
    for i in range(23):
        state = store.mutate(
            "qq-one",
            "10001",
            action="bind" if i == 0 else "replace",
            expected=revision,
            actor="demo-admin",
            via="admin",
            request_id=str(i),
            fingerprint=str(i),
            chart=chart,
        )
        revision = state["revision"]
    store.mutate(
        "qq-two",
        "10001",
        action="bind",
        expected="",
        actor="demo-admin",
        via="admin",
        request_id="other",
        fingerprint="other",
        chart=chart,
    )
    request.query = {"platform_id": "qq-one", "user_id": "10001", "page": "2"}
    result = asyncio.run(call("bindings/history"))["data"]
    assert (result["total"], result["pages"], len(result["items"])) == (23, 2, 3)
    assert result["items"][-1]["action"] == "bind"
    assert len({i["record_id"] for i in result["items"]}) == 3
    request.query["user_id"] = "1000"
    assert asyncio.run(call("bindings/history"))["data"]["total"] == 0
    request.query["page"] = "0"
    assert asyncio.run(call("bindings/history"))["status"] == 400
    plugin.closed = True
    assert asyncio.run(call("bindings/history"))["status"] == 503


def legacy_change(db, action, revision, source="", *, user="10001"):
    db.execute(
        "INSERT INTO binding_changes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            encode(["admin", "old-admin"]),
            revision,
            revision,
            "qq-one",
            user,
            action,
            revision,
            100.0,
            "old-admin",
            "admin",
            source,
        ),
    )


def test_legacy_history_is_copied_to_subject_without_reassigning_sources(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    archive = ArchiveStore(path)
    first = archive.records.save_web(natal(), "day", "old-admin", None, None)
    foreign = archive.save_chart(
        capture_group(Event(user="10002")), apply_flow(natal(), year=2026)
    )
    originals = [archive.records.get(i) for i in (first, foreign)]
    with archive.transaction() as db:
        legacy_change(db, "bind", "z-first", first)
        legacy_change(db, "replace", "a-second", foreign)
        legacy_change(db, "unbind", "b-third")
    archive.close()
    recovered = ArchiveStore(path)
    history = recovered.bindings.history.list("qq-one", "10001")
    assert history["total"] == 3
    unbind, replace, bind = history["items"]
    assert [i["action"] for i in history["items"]] == ["unbind", "replace", "bind"]
    assert all(i["restored"] and i["available"] for i in history["items"])
    assert unbind["record_id"] == replace["record_id"]
    assert replace["previous_record"] == bind["record_id"]
    assert [recovered.records.get(i) for i in (first, foreign)] == originals
    records = recovered.records.list(platform="qq-one", user="10001")
    assert records["total"] == 2
    assert all(i["source"] == "binding_import" for i in records["items"])
    for item in records["items"]:
        assert item["group_id"] == "" and item["actor"] == "old-admin"
        assert recovered.records.get(item["id"])["chart"] == natal()
    assert recovered.db.execute("SELECT count(*) FROM charts").fetchone()[0] == 1
    recovered.close()
    again = ArchiveStore(path)
    assert again.bindings.history.list("qq-one", "10001") == history
    assert again.records.list()["total"] == 4


@pytest.mark.parametrize("corrupt_source", [False, True])
def test_legacy_missing_source_uses_current_snapshot_only_for_exact_revision(
    tmp_path, corrupt_source
):
    path = tmp_path / "missing.sqlite3"
    archive = ArchiveStore(path)
    current = archive.bindings.mutate(
        "qq-one",
        "10001",
        action="bind",
        expected="",
        actor="old-admin",
        via="admin",
        request_id="latest",
        fingerprint="latest",
        chart=natal(),
    )
    original = current["source_record"]
    with archive.transaction() as db:
        db.execute("DELETE FROM binding_changes")
        db.execute("DELETE FROM binding_history_records")
        if corrupt_source:
            db.execute(
                "UPDATE admin_records SET web_chart='{}' WHERE id=?", (original,)
            )
        else:
            db.execute("DELETE FROM admin_records WHERE id=?", (original,))
        legacy_change(db, "bind", "unrecoverable-earlier", "missing")
        legacy_change(db, "replace", current["revision"], original)
    archive.close()
    restored = ArchiveStore(path)
    newer, older = restored.bindings.history.list("qq-one", "10001")["items"]
    assert older["available"] is False and older["summary"] is None
    assert newer["available"] and not newer["previous_available"]
    assert newer["record_id"] != original
    assert restored.records.get(newer["record_id"])["chart"] == natal()
    assert (
        restored.bindings.state("qq-one", "10001")["source_record"]
        == newer["record_id"]
    )
