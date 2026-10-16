"""Real atlas computation with local adapter doubles, not a live QQ delivery."""

import asyncio
import sqlite3
import threading
from copy import deepcopy

import pytest

from astrbot_plugin_ziwei import life_atlas_service, main
from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.engine import apply_decade, apply_flow, build_chart
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .test_llm_tools import Context, allow, invoke_command

DEMO = "199006150830 男"
OTHER_DEMO = "1995-07-20 12:00 女"


def chart(text=DEMO):
    request = parse_request(text)
    return build_chart(request.birth, request.profile)


async def invoke(plugin, args="", event=None, entry="alias"):
    event = event or Event()
    command = (
        plugin.ziwei(event, "图谱 " + args)
        if entry == "nested"
        else plugin.life_atlas(event, args)
    )
    return [reply async for reply in command]


def assert_image(replies):
    assert len(replies) == 1
    assert len(replies[0]) == 1
    assert replies[0][0].type == "image"
    assert replies[0][0].data == b"atlas-png"


def database_path(plugin):
    return plugin.archive.db.execute("PRAGMA database_list").fetchone()[2]


def archive_counts(path):
    with sqlite3.connect(path) as db:
        return tuple(
            db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("charts", "life_atlases")
        )


@pytest.fixture
def rendered(monkeypatch):
    calls = []

    class StubRenderer:
        def __init__(self, font_path=""):
            self.font_path = font_path

        def render(self, atlas, theme="night", *, demo=False):
            calls.append((deepcopy(atlas), theme, demo, self.font_path))
            return b"atlas-png"

    monkeypatch.setattr(life_atlas_service, "LifeAtlasRenderer", StubRenderer)
    return calls


@pytest.mark.parametrize("birth", [DEMO, "19900615083045 男"])
def test_both_commands_parse_the_same_real_chart_and_ignore_global_display(
    birth, rendered
):
    p = ZiweiPlugin(Context(), {"image_theme": "day", "output_mode": "text"})

    async def run():
        await allow(p)
        for entry in ("alias", "nested"):
            event = Event()
            assert_image(await invoke(p, birth, event, entry))
            assert event.stopped

    asyncio.run(run())
    assert p.life_atlas.command_metadata == {"name": "人生"}
    assert rendered[0][0] == rendered[1][0]
    assert rendered[0][0]["natal"] == chart(birth)
    assert all(call[1:3] == ("night", False) for call in rendered)
    assert archive_counts(database_path(p)) == (2, 2)
    assert p.pending == 0
    assert list(p.db.values()) == [True]


@pytest.mark.parametrize("entry", ["alias", "nested"])
@pytest.mark.parametrize(
    "event",
    [
        Event(),
        Event(admin=True),
        Event(group="", admin=True),
        Event(platform=""),
        Event(user=""),
    ],
)
def test_authorization_precedes_parse_archive_and_computation(
    entry, event, monkeypatch, rendered
):
    p = ZiweiPlugin(Context(), {})
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("unauthorized access")
        raise AssertionError("authorization must precede work")

    monkeypatch.setattr(life_atlas_service, "parse_life_atlas_request", forbidden)
    monkeypatch.setattr(p.life_atlases, "_archived_chart", forbidden)
    monkeypatch.setattr(p, "_compute_chart", forbidden)
    monkeypatch.setattr(life_atlas_service, "build_life_atlas", forbidden)

    async def run():
        if not event.user:
            await allow(p)
        result = await invoke(p, "invalid birth data", event, entry)
        assert len(result) == 1 and isinstance(result[0], str)
        assert any(
            word in result[0] for word in ("未开启", "群聊", "平台实例", "发送者")
        )
        assert "invalid birth data" not in result[0]

    asyncio.run(run())
    assert not calls and not rendered
    assert p.pending == 0 and not p.chart_cache.entries
    assert archive_counts(database_path(p)) == (0, 0)


@pytest.mark.parametrize("entry", ["alias", "nested"])
def test_help_is_public_without_parsing_or_computing(entry, monkeypatch, rendered):
    p = ZiweiPlugin(Context(), {})

    def forbidden(*args, **kwargs):
        pytest.fail("help must not parse or compute birth information")

    monkeypatch.setattr(life_atlas_service, "parse_life_atlas_request", forbidden)
    result = asyncio.run(invoke(p, "帮助", Event(group=""), entry))
    assert len(result) == 1 and "/人生 199006150830 男" in result[0]
    assert not rendered and p.pending == 0


def test_no_arguments_require_an_existing_owner_chart(rendered):
    p = ZiweiPlugin(Context(), {})

    async def run():
        await allow(p)
        result = await invoke(p)
        assert "没有本人在本群的对应命盘" in result[0]

    asyncio.run(run())
    assert not rendered and not p.chart_cache.entries
    assert archive_counts(database_path(p)) == (0, 0)


def test_one_archived_family_works_without_cache_and_preserves_original_flow(
    rendered,
):
    p = ZiweiPlugin(Context(), {})
    scope = capture_group(Event())
    base = chart()
    p.archive.save_chart(scope, base)
    annual = apply_flow(base, year=2026)
    chart_id = p.archive.save_chart(scope, annual)

    async def run():
        await allow(p)
        assert not p.chart_cache.entries
        assert_image(await invoke(p, "主题=白天"))
        cached = p.chart_cache.get(scope)
        assert cached.chart_id == chart_id
        assert cached.chart == p.archive.get_chart(scope, chart_id)["chart"] == annual
        assert cached.chart["flow"]["year"] == 2026

    asyncio.run(run())
    assert rendered[0][1] == "day"
    assert rendered[0][0]["natal"] == base
    assert archive_counts(database_path(p)) == (2, 1)


def test_multiple_families_require_selection_and_cached_selection_takes_priority(
    rendered,
):
    p = ZiweiPlugin(Context(), {})
    scope = capture_group(Event())
    base, other = chart(), chart(OTHER_DEMO)
    selected = p.archive.save_chart(scope, apply_flow(base, year=2026))
    newest = p.archive.save_chart(scope, other)

    async def run():
        await allow(p)
        choice = (await invoke(p))[0]
        assert "多份命盘" in choice and selected in choice and newest in choice
        assert "1990-06-15" not in choice and "1995-07-20" not in choice
        assert not rendered
        assert archive_counts(database_path(p)) == (2, 0)
        assert_image(await invoke(p, f"命盘={selected}"))
        assert p.chart_cache.get(scope).chart["flow"]["year"] == 2026
        assert_image(await invoke(p, entry="nested"))
        assert p.chart_cache.get(scope).chart_id == selected

    asyncio.run(run())
    assert len(rendered) == 2
    assert all(call[0]["natal"] == base for call in rendered)
    assert archive_counts(database_path(p)) == (2, 2)


@pytest.mark.parametrize(
    "other",
    [Event(user="another-user"), Event(group="20002"), Event(platform="qq-two")],
)
def test_archive_cannot_be_read_from_other_user_group_or_platform(other, rendered):
    p = ZiweiPlugin(Context(), {})
    scope = capture_group(Event())
    chart_id = p.archive.save_chart(scope, chart())
    p.chart_cache.put(scope, chart(), chart_id=chart_id)

    async def run():
        await allow(p, other)
        for args in ("", f"命盘={chart_id}"):
            result = await invoke(p, args, other)
            assert "没有本人在本群的对应命盘" in result[0]
            assert chart_id not in result[0]

    asyncio.run(run())
    assert not rendered
    assert archive_counts(database_path(p)) == (1, 0)


def test_direct_atlases_do_not_charge_or_depend_on_exhausted_ai_quotas(rendered):
    p = ZiweiPlugin(Context(), {})
    scope = capture_group(Event())
    base = chart()
    for kind, limit in (("natal", 2), ("decade", 3)):
        for index in range(limit):
            token = p.archive.reserve_chart(scope, kind)
            source = base if kind == "natal" else apply_decade(base, index)
            p.archive.save_chart(scope, source, token)
    for index in range(6):
        p.archive.charge_question(scope, f"question-{index}")
    before = list(p.archive.db.execute("SELECT * FROM ai_usage ORDER BY kind,token"))
    assert len(before) == 11

    async def run():
        await allow(p)
        assert_image(await invoke(p, DEMO))
        assert_image(await invoke(p, entry="nested"))

    asyncio.run(run())
    assert (
        list(p.archive.db.execute("SELECT * FROM ai_usage ORDER BY kind,token"))
        == before
    )
    assert len(rendered) == 2


@pytest.mark.parametrize("entry", ["alias", "nested"])
def test_busy_limit_is_shared_and_does_not_start_parsing(entry, monkeypatch, rendered):
    p = ZiweiPlugin(Context(), {})

    def forbidden(*args):
        pytest.fail("full queue must be rejected before parsing")

    monkeypatch.setattr(life_atlas_service, "parse_life_atlas_request", forbidden)

    async def run():
        await allow(p)
        p.pending = 4
        result = await invoke(p, DEMO, entry=entry)
        assert "请求较多" in result[0]
        assert p.pending == 4

    asyncio.run(run())
    assert not rendered and archive_counts(database_path(p)) == (0, 0)


@pytest.mark.parametrize("transition", ["close", "reload"])
def test_queued_work_checks_authorization_again_before_computing(
    transition, monkeypatch, rendered
):
    p = ZiweiPlugin(Context(), {})
    path = database_path(p)
    parsed = asyncio.Event()
    original = life_atlas_service.parse_life_atlas_request
    computation_calls = []

    def parse(*args):
        result = original(*args)
        parsed.set()
        return result

    def forbidden(*args):
        computation_calls.append(True)
        raise AssertionError("queued chart must not compute after closure")

    monkeypatch.setattr(life_atlas_service, "parse_life_atlas_request", parse)
    monkeypatch.setattr(p, "_compute_chart", forbidden)

    async def run():
        await allow(p)
        await p.gate.acquire()
        await p.gate.acquire()
        task = asyncio.create_task(invoke(p, DEMO))
        try:
            await asyncio.wait_for(parsed.wait(), 2)
            assert p.pending == 1
            if transition == "close":
                await invoke_command(p, "关闭", Event(role="owner"))
            else:
                await p.terminate()
        finally:
            p.gate.release()
            p.gate.release()
        result = await asyncio.wait_for(task, 3)
        assert all(isinstance(reply, str) for reply in result)
        assert ("未开启" if transition == "close" else "重载") in result[0]

    asyncio.run(run())
    assert not computation_calls and not rendered
    assert p.pending == 0 and not p.chart_cache.entries
    assert archive_counts(path) == (0, 0)


@pytest.mark.parametrize("stage", ["chart", "atlas", "render"])
@pytest.mark.parametrize("transition", ["close", "reload"])
def test_in_flight_closure_or_reload_prevents_image_and_archive(
    stage, transition, monkeypatch, rendered
):
    p = ZiweiPlugin(Context(), {})
    path = database_path(p)
    started, release = threading.Event(), threading.Event()
    if stage == "chart":
        target, name = main, "build_chart"
    elif stage == "atlas":
        target, name = life_atlas_service, "build_life_atlas"
    else:
        target, name = life_atlas_service.LifeAtlasRenderer, "render"
    original = getattr(target, name)

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, blocked)

    async def run():
        await allow(p)
        task = asyncio.create_task(invoke(p, DEMO))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            if transition == "close":
                await invoke_command(p, "关闭", Event(role="owner"))
            else:
                await p.terminate()
        finally:
            release.set()
        result = await asyncio.wait_for(task, 5)
        assert all(isinstance(reply, str) for reply in result)
        assert ("未开启" if transition == "close" else "重载") in result[0]

    asyncio.run(run())
    assert p.pending == 0 and not p.chart_cache.entries
    assert archive_counts(path) == (0, 0)


@pytest.mark.parametrize("error", [OSError, ValueError, RuntimeError])
def test_render_failure_does_not_archive_or_fall_back_to_natal_text(
    error, monkeypatch, rendered, caplog
):
    p = ZiweiPlugin(Context(), {})

    def fail(*args, **kwargs):
        raise error("render error with private content " + DEMO)

    monkeypatch.setattr(life_atlas_service.LifeAtlasRenderer, "render", fail)

    async def run():
        await allow(p)
        result = await invoke(p, DEMO)
        assert len(result) == 1 and isinstance(result[0], str)
        assert "暂" in result[0]
        assert DEMO not in result[0]

    asyncio.run(run())
    assert DEMO not in caplog.text
    assert not rendered and not p.chart_cache.entries
    assert p.pending == 0 and archive_counts(database_path(p)) == (0, 0)
