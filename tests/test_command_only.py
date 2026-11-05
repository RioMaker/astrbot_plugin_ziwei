"""The current adapter has no model route, even after upgrading old archives."""

import ast
import asyncio
import inspect
import io
from pathlib import Path

import pytest
from PIL import Image

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import DEMO_BIRTH, HELP, parse_request
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .helpers import Context, allow, invoke_command
from .test_readings import natal, queued


class NoModelContext(Context):
    async def llm_generate(self, **kwargs):
        raise AssertionError("command-only mode must never call a model")

    async def get_current_chat_provider_id(self, **kwargs):
        raise AssertionError("command-only mode must not resolve a provider")


def test_adapter_registers_only_explicit_commands():
    functions = inspect.getmembers(ZiweiPlugin, inspect.isfunction)
    assert not any(hasattr(fn, "tool_metadata") for _, fn in functions)
    assert not any(name.startswith("ziwei_") for name, _ in functions)
    assert not hasattr(ZiweiPlugin, "reading_request")
    source = Path(inspect.getfile(ZiweiPlugin)).read_text("utf-8-sig")
    tree = ast.parse(source)
    decorators = [
        d.func.attr
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        for d in n.decorator_list
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
    ]
    assert set(decorators) == {"command", "custom_filter"}


@pytest.mark.parametrize("text", ["", "帮助", "帮助 文字", "help text", "HELP TEXT"])
@pytest.mark.parametrize("private", [True, False])
def test_help_is_available_without_authorization_and_suppresses_model(text, private):
    plugin = ZiweiPlugin(NoModelContext(), {})
    event = Event(group="" if private else "20001")
    replies = asyncio.run(invoke_command(plugin, text, event))
    assert len(replies) == 1
    if text.lower().endswith(("文字", "text")):
        assert replies == [HELP]
    else:
        with Image.open(io.BytesIO(replies[0][0].data)) as image:
            assert image.width == 1080 and image.height > 1000
            image.verify()
    assert event.call_llm is True and event.stopped
    assert plugin.db == {} and not plugin.chart_cache.entries
    assert plugin.archive.db.execute("SELECT count(*) FROM charts").fetchone()[0] == 0


def test_broken_help_renderer_returns_copyable_text_without_logging_payload(caplog):
    plugin = ZiweiPlugin(NoModelContext(), {})

    class Broken:
        def render(self, theme):
            raise RuntimeError("NEVER_LOG_THIS_PRIVATE_TEXT")

    plugin.help_renderer = Broken()
    assert asyncio.run(invoke_command(plugin, "帮助")) == [HELP]
    assert "NEVER_LOG_THIS_PRIVATE_TEXT" not in caplog.text


@pytest.mark.parametrize("state", ["busy", "closed"])
def test_busy_or_reloading_help_returns_text_without_starting_render(state):
    plugin = ZiweiPlugin(NoModelContext(), {})
    plugin.pending = 4 if state == "busy" else 0
    plugin.closed = state == "closed"

    class Forbidden:
        def render(self, theme):
            raise AssertionError("must not add another background render")

    plugin.help_renderer = Forbidden()
    assert asyncio.run(invoke_command(plugin, "帮助")) == [HELP]
    assert plugin.pending == (4 if state == "busy" else 0)


@pytest.mark.parametrize("failure", ["empty", "exception"])
def test_bad_chart_image_returns_complete_text_and_keeps_archive(failure, caplog):
    plugin = ZiweiPlugin(NoModelContext(), {})

    class Broken:
        def render(self, chart, theme):
            if failure == "exception":
                raise RuntimeError("NEVER_LOG_THIS_PRIVATE_TEXT")
            return b""

    plugin.renderer = Broken()

    async def run():
        await allow(plugin)
        replies = await invoke_command(plugin, DEMO_BIRTH)
        assert replies[0].startswith("图片暂不可用")
        assert "土五局" in "".join(replies) and "太阳禄" in "".join(replies)
        assert plugin.pending == 0
        assert len(plugin.archive.list_charts(capture_group(Event()))) == 1

    asyncio.run(run())
    assert "NEVER_LOG_THIS_PRIVATE_TEXT" not in caplog.text


@pytest.mark.parametrize("args", ["定盘", "定盘 帮助", "定盘 1990-06-15 男"])
def test_retired_rectification_command_cannot_create_candidates(args):
    plugin = ZiweiPlugin(NoModelContext(), {})
    event = Event(admin=True)

    async def run():
        await allow(plugin)
        await plugin.initialize()
        result = await invoke_command(plugin, args, event)
        assert "暂不提供 AI 辅助定盘" in result[0]
        for table in ("charts", "rectifications", "ai_usage", "readings"):
            assert (
                plugin.archive.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                == 0
            )

    asyncio.run(run())
    assert event.call_llm is True
    assert not any(
        hasattr(plugin, name)
        for name in ("readings", "rectifications", "summary_worker")
    )


def test_reload_preserves_all_legacy_jobs_and_does_not_resume_them():
    context = NoModelContext()
    plugin = ZiweiPlugin(context, {"ai_quota_window_seconds": -1})
    store = plugin.archive
    scope, rid, _ = queued(store, Event())
    assert store.claim_job()["id"] == rid  # A legacy running summary.
    chart = natal()
    sid = store.rectifications.create(
        scope,
        "legacy",
        {},
        [
            {
                "id": "c01",
                "label": "虚构候选",
                "chart": chart,
                "intervals": [{"start": "07:00", "end": "08:59"}],
            }
        ],
    )
    with store.transaction() as db:
        db.execute(
            "UPDATE rectifications SET summary_status='running' WHERE id=?", (sid,)
        )
    store.reserve_chart(scope, "natal")
    tables = ("charts", "readings", "turns", "rectifications", "ai_usage")

    def snapshot(archive):
        return {
            table: [tuple(r) for r in archive.db.execute(f"SELECT * FROM {table}")]
            for table in tables
        }

    before = snapshot(store)

    async def run():
        await plugin.terminate()
        restored = ZiweiPlugin(context, {})
        await restored.initialize()
        await invoke_command(restored, "开启", Event(admin=True))
        assert snapshot(restored.archive) == before
        assert not restored.chart_cache.entries
        await restored.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("transition", ["close", "reload"])
def test_queued_command_checks_permission_before_computation(transition):
    plugin = ZiweiPlugin(NoModelContext(), {"output_mode": "text"})

    async def forbidden(_request):
        raise AssertionError("revoked queued work must not compute")

    plugin._compute_chart = forbidden

    async def run():
        await allow(plugin)
        plugin.gate = asyncio.Semaphore(0)
        task = asyncio.create_task(invoke_command(plugin, DEMO_BIRTH))
        for _ in range(100):
            if plugin.pending:
                break
            await asyncio.sleep(0)
        assert plugin.pending == 1
        if transition == "close":
            await invoke_command(plugin, "关闭", Event(admin=True))
        else:
            await plugin.terminate()
        plugin.gate.release()
        result = await asyncio.wait_for(task, 2)
        assert "尚未开启" in result[0] or "重载" in result[0]
        assert not plugin.chart_cache.entries and plugin.pending == 0

    asyncio.run(run())


def test_command_archive_owner_and_cache_survive_reload_without_ai(caplog):
    context = NoModelContext()
    plugin = ZiweiPlugin(context, {"output_mode": "text"})
    scope = capture_group(Event())

    async def run():
        await invoke_command(plugin, "开启", Event(admin=True))
        replies = await invoke_command(plugin, DEMO_BIRTH)
        assert "土五局" in "".join(replies)
        row = plugin.chart_cache.get(scope)
        for event in (
            Event(user="other"),
            Event(group="other"),
            Event(platform="other"),
        ):
            assert plugin.archive.get_chart(capture_group(event), row.chart_id) is None
        await plugin.terminate()
        restored = ZiweiPlugin(context, {})
        await restored.initialize()
        assert not restored.chart_cache.entries
        assert restored.archive.get_chart(scope, row.chart_id)["chart"] == row.chart
        assert (
            restored.archive.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
            == 0
        )
        await restored.terminate()

    asyncio.run(run())
    assert "1990-06-15" not in str(context.db) + caplog.text
    assert parse_request(DEMO_BIRTH).image_theme == "night"
    assert parse_request(DEMO_BIRTH, {"image_theme": "day"}).image_theme == "day"
