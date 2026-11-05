"""Local authenticated Dashboard doubles; no real AstrBot or QQ connection."""

import asyncio
import copy
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.admin_service import AdminError
from astrbot_plugin_ziwei.archive_store import ArchiveStore
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.life_atlas import build_life_atlas
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .helpers import allow, invoke_command
from .test_command_only import NoModelContext
from .test_readings import natal


class Config(dict):
    def __init__(self, path, values=None):
        super().__init__(values or {})
        self.path = path
        self.fail = False

    def save_config(self):
        if self.fail:
            raise OSError("PRIVATE_CONFIG_ERROR")
        self.path.write_text(json.dumps(self), encoding="utf-8")


class PageContext(NoModelContext):
    def __init__(self):
        super().__init__()
        self.registered_web_apis = []

    def register_web_api(self, route, handler, methods, desc):
        self.registered_web_apis.append((route, handler, methods, desc))


@pytest.fixture
def page(monkeypatch, tmp_path):
    class Request:
        username = "demo-admin"
        headers = {}
        query = {}
        content_type = "application/json"
        raw = b"{}"

        async def body(self):
            return self.raw

    request = Request()
    web = SimpleNamespace(
        request=request,
        json_response=lambda data, **kwargs: {"data": data, "status": 200, **kwargs},
        error_response=lambda message, status_code=400: {
            "message": message,
            "status": status_code,
        },
    )
    monkeypatch.setattr(sys.modules["astrbot.api"], "web", web, raising=False)
    config = Config(tmp_path / "config.json")
    plugin = ZiweiPlugin(PageContext(), config)
    # Most tests exercise real calculation and storage, without expensive rendering.
    plugin.renderer = SimpleNamespace(render=lambda chart, theme: b"demo-png")

    async def call(route, payload=None, **params):
        if payload is not None:
            request.raw = json.dumps(payload, allow_nan=True).encode()
        handler = next(
            row[1]
            for row in plugin.context.registered_web_apis
            if row[0].endswith("/" + route)
        )
        return await handler(**params)

    return plugin, request, config, call


def web_payload(args=DEMO_BIRTH, key="demo-request-00001"):
    return {"args": args, "request_id": key}


@pytest.mark.parametrize(
    "route,post",
    [
        ("overview", False),
        ("settings", False),
        ("records", False),
        ("settings/save", True),
        ("charts", True),
        ("records/<record_id>", False),
    ],
)
def test_all_admin_routes_require_dashboard_identity(page, route, post):
    plugin, request, config, call = page
    request.username = None
    result = asyncio.run(
        call(
            route,
            web_payload() if post else None,
            **({"record_id": "a" * 32} if "<" in route else {}),
        )
    )
    assert result["status"] == 401
    assert plugin.archive.records.overview()["counts"] == {}
    assert not config.path.exists()


def test_cross_site_reload_and_route_cleanup(page):
    plugin, request, _, call = page
    request.headers = {"Sec-Fetch-Site": "cross-site"}
    assert asyncio.run(call("overview"))["status"] == 403
    request.headers = {}
    handler = plugin.context.registered_web_apis[0][1]
    plugin.context.registered_web_apis.append(
        ("/other", lambda: None, ["GET"], "other")
    )
    asyncio.run(plugin.terminate())
    assert [row[0] for row in plugin.context.registered_web_apis] == ["/other"]
    assert asyncio.run(handler())["status"] == 503


@pytest.mark.parametrize(
    "raw,content_type,status",
    [
        (b"{", "application/json", 400),
        (b"[]", "application/json", 400),
        (b"{}", "text/plain", 415),
        (b" " * 16385, "application/json", 413),
    ],
)
def test_invalid_request_body_is_rejected_before_work(page, raw, content_type, status):
    _, request, _, call = page
    request.raw, request.content_type = raw, content_type
    assert asyncio.run(call("charts"))["status"] == status


def test_web_chart_is_private_admin_source_persistent_and_retry_safe(page):
    plugin, _, config, call = page

    async def run():
        first, retry = await asyncio.gather(
            call("charts", web_payload()), call("charts", web_payload())
        )
        assert first["status"] == retry["status"] == 200
        assert first["headers"]["Cache-Control"] == "no-store"
        assert first["data"]["id"] == retry["data"]["id"]
        row = first["data"]
        assert row["source"] == "web" and row["actor"] == "demo-admin"
        assert row["platform_id"] == row["group_id"] == row["user_id"] == ""
        assert row["image"].startswith("data:image/png;base64,")
        assert "紫微斗数排盘" in row["text"]
        assert "chart" not in row and "atlas" not in row
        changed = await call("charts", web_payload(DEMO_BIRTH + " 流年=2026"))
        assert changed["status"] == 409
        return row["id"]

    record_id = asyncio.run(run())
    assert plugin.pending == 0 and plugin.db == {} and not plugin.chart_cache.entries
    for table in ("charts", "groups_state", "ai_usage", "readings"):
        assert (
            plugin.archive.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            == 0
        )
    assert plugin.archive.records.list()["total"] == 1
    path = config.path.parent / "plugin_data/astrbot_plugin_ziwei/archives.sqlite3"
    plugin.archive.close()
    restored = ArchiveStore(path, resume_ai_jobs=False)
    assert restored.records.get(record_id)["chart"]["sex"] == "男"
    assert restored.records.list()["total"] == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"group_id": "20001"},
        {"user_id": "10001"},
        {"actor": "another-admin"},
        {"request_id": "short"},
        {"args": "bad birth input"},
    ],
)
def test_web_request_cannot_supply_chat_identity_or_invalid_birth(page, extra):
    plugin, _, _, call = page
    result = asyncio.run(call("charts", web_payload() | extra))
    assert result["status"] == 400
    assert plugin.archive.records.list()["total"] == 0
    assert plugin.pending == 0


@pytest.mark.parametrize(
    "args,kind",
    [
        (DEMO_BIRTH, "natal"),
        (DEMO_BIRTH + " 运限=0", "decade"),
        (DEMO_BIRTH + " 运限=2", "decade"),
        (DEMO_BIRTH + " 流年=2026", "year"),
        (DEMO_BIRTH + " 流盘=20261006 流时=1000", "date"),
        ("农历 2023闰02161200 女", "natal"),
    ],
)
def test_page_uses_same_chart_modes_as_commands(page, args, kind):
    plugin, _, _, call = page
    result = asyncio.run(call("charts", web_payload(args)))
    assert result["status"] == 200 and result["data"]["kind"] == kind
    row = plugin.archive.records.get(result["data"]["id"])
    if "运限=" in args:
        assert row["chart"]["flow"]["year"] is None
        assert row["chart"]["flow"]["target"] is None
        assert set(row["chart"]["flow"]["layers"]) == {"大限"}
    assert plugin.archive.records.list()["total"] == 1


def test_command_records_real_scope_names_theme_and_failed_commands_do_not_record(page):
    plugin, _, _, _ = page
    event = Event(group="20001", user="10001", platform="qq-demo")
    event.message_obj.raw_message["sender"]["card"] = "演示群友 <img onerror=alert(1)>"

    async def run():
        await invoke_command(plugin, DEMO_BIRTH, event)
        assert plugin.archive.records.list()["total"] == 0
        await allow(plugin, event)
        await invoke_command(plugin, "invalid", event)
        assert plugin.archive.records.list()["total"] == 0
        await invoke_command(plugin, DEMO_BIRTH + " 主题=白天", event)
        other = Event(group="20002", user="10001", platform="qq-demo")
        await allow(plugin, other)
        await invoke_command(plugin, DEMO_BIRTH, other)

    asyncio.run(run())
    rows = plugin.archive.records.list(group="20001", platform="qq-demo")["items"]
    assert len(rows) == 1
    assert rows[0]["user_id"] == "10001" and rows[0]["theme"] == "day"
    assert rows[0]["user_name"] == "演示群友 <img onerror=alert(1)>"
    assert rows[0]["source"] == "group"
    assert plugin.archive.records.overview()["users"] == 2
    assert DEMO_BIRTH not in json.dumps(plugin.db)
    assert (
        plugin.archive.get_chart(capture_group(Event(user="wrong")), rows[0]["id"])
        is None
    )


def test_old_archive_migration_preserves_snapshots_and_unknown_identity(page):
    plugin, _, config, _ = page
    chart = natal()
    scope = capture_group(Event())
    record_id = plugin.archive.save_chart(scope, chart)
    with plugin.archive.transaction() as db:
        db.execute("DELETE FROM admin_records")
    plugin.archive.close()
    path = config.path.parent / "plugin_data/astrbot_plugin_ziwei/archives.sqlite3"
    restored = ArchiveStore(path, resume_ai_jobs=False)
    row = restored.records.get(record_id)
    assert row["chart"] == chart and row["source"] == "legacy"
    assert row["platform_id"] == row["group_id"] == row["user_id"] == ""
    assert restored.get_chart(scope, record_id)["chart"] == chart
    restored.close()
    again = ArchiveStore(path, resume_ai_jobs=False)
    assert again.records.list()["total"] == 1
    assert again.db.execute("PRAGMA user_version").fetchone()[0] == 5


def test_record_filters_pagination_and_literal_search(page):
    plugin, request, _, call = page
    chart = natal()
    for index in range(23):
        event = Event(user=str(10001 + index), group="20001")
        event.message_obj.raw_message["sender"]["nickname"] = "演示群友"
        plugin.archive.save_chart(capture_group(event), chart)
    request.query = {
        "source": "group",
        "group": "20001",
        "platform": "qq-one",
        "search": "演示",
        "page": "2",
    }
    result = asyncio.run(call("records"))["data"]
    assert result["total"] == 23 and result["page"] == 2 and len(result["items"]) == 3
    for search in ("%", "' OR 1=1 --", "未存在"):
        request.query["search"] = search
        assert asyncio.run(call("records"))["data"]["total"] == 0
    request.query = {"start": "2000-01-01", "end": "2000-01-02"}
    assert asyncio.run(call("records"))["data"]["total"] == 0


@pytest.mark.parametrize(
    "query",
    [
        {"page": "0"},
        {"page": "1.5"},
        {"source": "all-users"},
        {"kind": "ai"},
        {"start": "invalid"},
        {"start": "2026-11-06", "end": "2026-11-05"},
        {"search": "x" * 257},
    ],
)
def test_bad_record_filters_are_rejected(page, query):
    _, request, _, call = page
    request.query = query
    assert asyncio.run(call("records"))["status"] == 400


def test_settings_save_persists_and_preserves_old_chart_and_revision(page):
    plugin, _, config, call = page

    async def run():
        initial = (await call("settings"))["data"]
        record = (await call("charts", web_payload()))["data"]
        before = plugin.archive.records.get(record["id"])["chart"]
        values = copy.deepcopy(initial["values"])
        values.update(image_theme="day", longitude=116.4, true_solar=False, void_rule=1)
        saved = await call(
            "settings/save", {"values": values, "revision": initial["revision"]}
        )
        assert (
            saved["status"] == 200 and saved["data"]["revision"] != initial["revision"]
        )
        assert (
            config.path.exists()
            and json.loads(config.path.read_text())["longitude"] == 116.4
        )
        assert plugin.config["image_theme"] == "day"
        new = (await call("charts", web_payload(key="demo-request-00002")))["data"]
        assert new["theme"] == "day"
        assert (
            plugin.archive.records.get(new["id"])["chart"]["profile"]["void_rule"] == 1
        )
        assert plugin.archive.records.get(record["id"])["chart"] == before
        conflict = await call(
            "settings/save", {"values": values, "revision": initial["revision"]}
        )
        assert conflict["status"] == 409

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"longitude": 181},
        {"timezone": -13},
        {"longitude": float("nan")},
        {"true_solar": "false"},
        {"kuiyue": True},
        {"image_theme": "bad"},
        {"void_rule": 4},
        {"font_path": "Z:/missing-font.ttf"},
        {"unknown": 1},
    ],
)
def test_invalid_settings_never_write_or_change_runtime(page, changes):
    plugin, _, config, call = page
    state = asyncio.run(call("settings"))["data"]
    before = dict(plugin.config)
    result = asyncio.run(
        call(
            "settings/save",
            {"values": state["values"] | changes, "revision": state["revision"]},
        )
    )
    assert result["status"] == 400
    assert dict(plugin.config) == before and dict(config) == before
    assert not config.path.exists()


def test_failed_save_rolls_back_runtime_without_logging_values(page, caplog):
    plugin, _, config, call = page
    state = asyncio.run(call("settings"))["data"]
    config.fail = True
    values = state["values"] | {"longitude": 116.4}
    result = asyncio.run(
        call("settings/save", {"values": values, "revision": state["revision"]})
    )
    assert result["status"] == 500
    assert plugin.config == {} and config == {}
    assert "PRIVATE_CONFIG_ERROR" not in caplog.text


def test_render_failure_keeps_record_and_does_not_log_birth(page, caplog):
    plugin, _, _, call = page

    def broken(*args):
        raise RuntimeError("PRIVATE_BIRTH_INFORMATION")

    plugin.renderer = SimpleNamespace(render=broken)
    result = asyncio.run(call("charts", web_payload()))
    assert result["status"] == 200 and result["data"]["image"] is None
    assert result["data"]["warning"] and result["data"]["text"]
    assert plugin.archive.records.list()["total"] == 1
    assert "PRIVATE_BIRTH_INFORMATION" not in caplog.text


def test_queued_page_work_aborts_on_reload_and_cancellation_holds_slot(page):
    plugin, _, _, _ = page
    service = plugin.admin_page.service

    async def run():
        await plugin.gate.acquire()
        await plugin.gate.acquire()
        task = asyncio.create_task(service.create(web_payload(), "admin"))
        await asyncio.sleep(0)
        plugin.closed = True
        plugin.gate.release()
        with pytest.raises(AdminError) as error:
            await task
        assert error.value.status == 503 and plugin.pending == 0
        assert plugin.archive.records.list()["total"] == 0
        plugin.gate.release()
        plugin.closed = False
        started, release = asyncio.Event(), asyncio.Event()

        async def compute(request):
            started.set()
            await release.wait()
            return natal()

        plugin._compute_chart = compute
        task = asyncio.create_task(service.create(web_payload(), "admin"))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert plugin.pending == 1 and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert plugin.pending == 0 and plugin.archive.records.list()["total"] == 0

    asyncio.run(run())


def test_queued_chat_uses_the_configuration_and_renderer_captured_at_submission(page):
    plugin, _, _, _ = page
    rendered = []
    plugin.renderer = SimpleNamespace(
        render=lambda chart, theme: rendered.append((chart, theme)) or b"png"
    )
    service = plugin.admin_page.service

    async def run():
        await allow(plugin)
        await plugin.gate.acquire()
        await plugin.gate.acquire()
        task = asyncio.create_task(invoke_command(plugin, DEMO_BIRTH))
        await asyncio.sleep(0)
        state = service.settings()
        await service.save_settings(
            {
                "values": state["values"] | {"void_rule": 1, "image_theme": "day"},
                "revision": state["revision"],
            }
        )
        plugin.gate.release()
        await task
        plugin.gate.release()

    asyncio.run(run())
    assert len(rendered) == 1
    assert rendered[0][0]["profile"]["void_rule"] == 2 and rendered[0][1] == "night"
    assert plugin.archive.records.list()["items"][0]["theme"] == "night"


def test_records_date_filter_includes_whole_beijing_day(page):
    plugin, request, _, call = page
    chart = natal()
    times = (
        "2026-11-04T15:59:59+00:00",
        "2026-11-04T16:00:00+00:00",
        "2026-11-05T15:59:59+00:00",
        "2026-11-05T16:00:00+00:00",
    )
    for stamp in times:
        plugin.archive.clock = lambda stamp=stamp: (
            datetime.fromisoformat(stamp).astimezone(timezone.utc).timestamp()
        )
        plugin.archive.save_chart(capture_group(Event()), chart)
    request.query = {"start": "2026-11-05", "end": "2026-11-05"}
    result = asyncio.run(call("records"))["data"]
    assert result["total"] == 2
    assert sorted(row["created"] for row in result["items"]) == [
        datetime.fromisoformat(stamp).timestamp() for stamp in times[1:3]
    ]


def test_group_record_and_chart_are_saved_atomically(page, monkeypatch):
    plugin, _, _, _ = page

    def fail(*args, **kwargs):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(plugin.archive.records, "add", fail)
    with pytest.raises(RuntimeError):
        plugin.archive.save_chart(capture_group(Event()), natal())
    assert plugin.archive.records.list()["total"] == 0
    assert plugin.archive.db.execute("SELECT count(*) FROM charts").fetchone()[0] == 0


def test_life_atlas_record_keeps_its_saved_data_without_extra_natal_entry(page):
    plugin, _, config, _ = page
    atlas = build_life_atlas(natal())
    record_id, chart_id = plugin.archive.save_life_atlas(
        capture_group(Event()), atlas, "day"
    )
    row = plugin.archive.records.get(record_id)
    assert row["kind"] == "atlas" and row["atlas"] == atlas and row["theme"] == "day"
    assert plugin.archive.records.list()["total"] == 1
    plugin.archive.close()
    path = config.path.parent / "plugin_data/astrbot_plugin_ziwei/archives.sqlite3"
    restored = ArchiveStore(path, resume_ai_jobs=False)
    assert restored.records.list()["total"] == 1
    assert restored.records.get(record_id)["atlas"] == atlas
    assert (
        restored.get_chart(capture_group(Event()), chart_id)["chart"] == atlas["natal"]
    )
