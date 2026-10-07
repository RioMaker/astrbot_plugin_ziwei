import asyncio
import inspect
import json
import threading

import docstring_parser
import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request
from astrbot_plugin_ziwei.engine import build_chart
from astrbot_plugin_ziwei.llm_data import ChartCache, chart_payload
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event


class Context:
    def __init__(self, failure=False, accepted=True):
        self.db, self.sent = {}, []
        self.failure, self.accepted = failure, accepted

    async def send_message(self, umo, chain):
        if self.failure:
            raise OSError("send failed")
        self.sent.append((umo, chain))
        return self.accepted


async def allow(plugin, event=None):
    await plugin.access.set_enabled(capture_group(event or Event()), True)


async def invoke_command(plugin, args, event=None):
    return [reply async for reply in plugin.ziwei(event or Event(), args)]


def test_tool_schema_uses_actual_docstring_parser():
    for function, name, params in (
        (
            ZiweiPlugin.ziwei_paipan,
            "ziwei_paipan",
            {"birth_info": "string", "send_image": "boolean"},
        ),
        (
            ZiweiPlugin.ziwei_get_chart,
            "ziwei_get_chart",
            {"chart_id": "string", "palace": "string"},
        ),
    ):
        assert function.tool_metadata["name"] == name
        parsed = docstring_parser.parse(function.__doc__)
        assert {p.arg_name: p.type_name for p in parsed.params} == params
        assert all(p.description for p in parsed.params)
        assert set(inspect.signature(function).parameters) == {"self", "event", *params}


@pytest.mark.parametrize(
    "event", [Event(), Event(group=""), Event(admin=True), Event(platform="")]
)
def test_tool_denial_has_no_birth_data_or_compute_side_effect(event, monkeypatch):
    from astrbot_plugin_ziwei import main

    p = ZiweiPlugin(Context(), {})

    def forbidden(*args):
        raise AssertionError("permission must be checked before computation")

    monkeypatch.setattr(main, "build_chart", forbidden)

    async def run():
        a = json.loads(await p.ziwei_paipan(event, DEMO_BIRTH))
        b = json.loads(await p.ziwei_get_chart(event))
        assert a["status"] == b["status"] == "denied"
        assert "data" not in a and "data" not in b

    asyncio.run(run())
    assert p.pending == 0 and not p.chart_cache.entries


def test_tool_can_compute_complete_flow_data_without_stopping_llm_or_rendering(
    monkeypatch,
):
    p = ZiweiPlugin(Context(), {})

    class ForbiddenRenderer:
        def render(self, chart, theme="day"):
            raise AssertionError("no unsolicited image")

    p.renderer = ForbiddenRenderer()
    event = Event()

    async def run():
        await allow(p)
        result = json.loads(
            await p.ziwei_paipan(event, DEMO_BIRTH + " 流盘=2026-10-06 流时=10:00")
        )
        assert result["status"] == "ok" and result["image_status"] == "not_requested"
        data = result["data"]
        assert len(data["palaces"]) == 12 and len(data["decades"]) == 13
        assert sum(len(item["stars"]) for item in data["palaces"]) == 70
        assert data["summary"]["bureau_name"] == "土五局"
        assert data["flow"]["age"] == 37
        assert set(data["flow"]["layers"]) == {
            "大限",
            "小限",
            "流年",
            "流月",
            "流日",
            "流时",
        }
        assert "original" in data["normalized_birth"]
        assert all(
            "hua" in s and "self_hua" in s and "visual_category" in s
            for item in data["palaces"]
            for s in item["stars"]
        )
        assert result["expires_in_seconds"] <= 900
        assert len(json.dumps(result, ensure_ascii=False)) < 45000

    asyncio.run(run())
    assert not getattr(event, "stopped", False)
    assert p.pending == 0 and p.context.sent == []


def test_command_chart_can_be_read_by_same_user_then_filtered_by_palace():
    p = ZiweiPlugin(Context(), {"output_mode": "text"})

    async def run():
        await allow(p)
        assert "土五局" in "".join(await invoke_command(p, DEMO_BIRTH))
        whole = json.loads(await p.ziwei_get_chart(Event()))
        selected = json.loads(
            await p.ziwei_get_chart(Event(), whole["chart_id"], "财帛宫")
        )
        assert whole["status"] == selected["status"] == "ok"
        assert len(selected["data"]["palaces"]) == 1
        assert selected["data"]["palaces"][0]["name"] == "财帛"
        assert len(selected["data"]["decades"]) == 13
        assert (
            json.loads(await p.ziwei_get_chart(Event(), palace="invalid"))["status"]
            == "invalid_input"
        )
        assert (
            json.loads(await p.ziwei_get_chart(Event(), palace="命宫宫"))["status"]
            == "invalid_input"
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "other",
    [Event(user="another-user"), Event(group="20002"), Event(platform="qq-two")],
)
def test_cache_isolation_even_when_other_group_or_instance_is_enabled(other):
    p = ZiweiPlugin(Context(), {})

    async def run():
        await allow(p)
        first = json.loads(await p.ziwei_paipan(Event(), DEMO_BIRTH))
        await allow(p, other)
        response = json.loads(await p.ziwei_get_chart(other, first["chart_id"]))
        assert response["status"] == "not_found" and "data" not in response

    asyncio.run(run())


def test_close_revokes_tools_and_purges_group_cache_even_after_reopen():
    p = ZiweiPlugin(Context(), {})

    async def run():
        await allow(p)
        await p.ziwei_paipan(Event(), DEMO_BIRTH)
        await invoke_command(p, "关闭", Event(role="owner"))
        assert not p.chart_cache.entries
        assert json.loads(await p.ziwei_get_chart(Event()))["status"] == "denied"
        await invoke_command(p, "开启", Event(role="owner"))
        assert json.loads(await p.ziwei_get_chart(Event()))["status"] == "not_found"

    asyncio.run(run())


def test_cache_expires_without_read_refresh_and_latest_chart_replaces_old_id():
    now = [0.0]
    p = ZiweiPlugin(Context(), {})
    p.chart_cache = ChartCache(clock=lambda: now[0])

    async def run():
        await allow(p)
        first = json.loads(await p.ziwei_paipan(Event(), DEMO_BIRTH))
        now[0] = 800
        assert json.loads(await p.ziwei_get_chart(Event()))["expires_in_seconds"] == 100
        now[0] = 901
        assert (
            json.loads(await p.ziwei_get_chart(Event(), first["chart_id"]))["status"]
            == "not_found"
        )
        fresh = json.loads(await p.ziwei_paipan(Event(), DEMO_BIRTH))
        newest = json.loads(await p.ziwei_paipan(Event(), "1995-07-20 12:00 女"))
        assert newest["chart_id"] != fresh["chart_id"]
        assert (
            json.loads(await p.ziwei_get_chart(Event(), fresh["chart_id"]))["status"]
            == "not_found"
        )

    asyncio.run(run())


def test_cache_capacity_and_copy_isolation():
    chart = build_chart(parse_request(DEMO_BIRTH).birth)
    cache = ChartCache(capacity=2)
    a, b, c = (capture_group(Event(user=x)) for x in ("a", "b", "c"))
    cache.put(a, chart)
    cache.put(b, chart)
    cache.put(c, chart)
    assert cache.get(a) is None
    row = cache.get(b)
    result = json.loads(chart_payload(row, remaining_seconds=100))
    result["data"]["palaces"].clear()
    row.chart["palaces"].clear()
    assert len(cache.get(b).chart["palaces"]) == 12


@pytest.mark.parametrize(
    "birth_info,image",
    [
        ("", False),
        ("开启", False),
        ("1990-02-30 08:00 男", False),
        (None, False),
        (DEMO_BIRTH, "false"),
    ],
)
def test_invalid_tool_input_does_not_mutate_authorization_or_cache(birth_info, image):
    p = ZiweiPlugin(Context(), {})

    async def run():
        await allow(p)
        before = dict(p.db)
        response = json.loads(await p.ziwei_paipan(Event(), birth_info, image))
        assert response["status"] == "invalid_input"
        assert p.db == before and not p.chart_cache.entries

    asyncio.run(run())
    assert p.pending == 0


@pytest.mark.parametrize("event", [Event(user=""), Event(group="", admin=True)])
def test_unidentified_or_private_sender_cannot_use_tool(event):
    p = ZiweiPlugin(Context(), {})

    async def run():
        await allow(p)
        assert json.loads(await p.ziwei_paipan(event, DEMO_BIRTH))["status"] == "denied"
        assert json.loads(await p.ziwei_get_chart(event))["status"] == "denied"

    asyncio.run(run())


def test_reload_clears_cache_and_kv_contains_only_group_authorization():
    context = Context()
    p = ZiweiPlugin(context, {})

    async def run():
        await allow(p)
        await p.ziwei_paipan(Event(), DEMO_BIRTH)
        assert list(context.db.values()) == [True]
        await p.terminate()
        assert not p.chart_cache.entries
        assert json.loads(await p.ziwei_get_chart(Event()))["status"] == "unavailable"
        replacement = ZiweiPlugin(context, {})
        assert (
            json.loads(await replacement.ziwei_get_chart(Event()))["status"]
            == "not_found"
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure,accepted,status",
    [(False, True, "sent"), (True, True, "failed"), (False, False, "failed")],
)
def test_optional_image_delivery_does_not_lose_structured_results(
    failure, accepted, status
):
    p = ZiweiPlugin(Context(failure, accepted), {"image_theme": "night"})

    class FakeRenderer:
        def render(self, chart, theme="day"):
            assert theme == "day"
            return b"png"

    p.renderer = FakeRenderer()
    event = Event()

    async def run():
        await allow(p)
        result = json.loads(
            await p.ziwei_paipan(event, DEMO_BIRTH + " 主题=白天", True)
        )
        assert result["status"] == "ok" and result["image_status"] == status
        if p.context.sent:
            assert p.context.sent[0][0] == event.unified_msg_origin
            assert p.context.sent[0][1].chain[0].data == b"png"

    asyncio.run(run())


def test_kv_failure_fails_closed_and_logs_no_birth_data(caplog):
    p = ZiweiPlugin(Context(), {})

    async def fail(*args):
        raise OSError("unavailable")

    p.access.get = fail
    assert (
        json.loads(asyncio.run(p.ziwei_paipan(Event(), DEMO_BIRTH)))["status"]
        == "unavailable"
    )
    assert (
        json.loads(asyncio.run(p.ziwei_get_chart(Event())))["status"] == "unavailable"
    )
    assert DEMO_BIRTH not in caplog.text


def test_closing_group_while_computation_runs_denies_result_and_keeps_cache_empty(
    monkeypatch,
):
    from astrbot_plugin_ziwei import main

    p = ZiweiPlugin(Context(), {})
    started, release = threading.Event(), threading.Event()
    original = main.build_chart

    def blocked(*args):
        started.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(main, "build_chart", blocked)

    async def run():
        await allow(p)
        task = asyncio.create_task(p.ziwei_paipan(Event(), DEMO_BIRTH))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            await invoke_command(p, "关闭", Event(role="owner"))
        finally:
            release.set()
        assert json.loads(await task)["status"] == "denied"
        assert not p.chart_cache.entries

    asyncio.run(run())
    assert p.pending == 0


def test_manual_and_tool_requests_share_busy_limit():
    p = ZiweiPlugin(Context(), {})

    async def run():
        await allow(p)
        p.pending = 4
        assert json.loads(await p.ziwei_paipan(Event(), DEMO_BIRTH))["status"] == "busy"
        assert "请求较多" in (await invoke_command(p, DEMO_BIRTH))[0]
        assert p.pending == 4

    asyncio.run(run())
