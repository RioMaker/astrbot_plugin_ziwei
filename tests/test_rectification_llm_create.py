"""AI batch creation and single-collage delivery using local AstrBot doubles."""

import asyncio
import copy
import inspect
import io
import json
import sqlite3
import threading
import time

import docstring_parser
import pytest
from PIL import Image as PillowImage

from astrbot_plugin_ziwei import rectification_service
from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.main import ZiweiPlugin
from astrbot_plugin_ziwei.rectification_renderer import plan_candidate_collage

from .conftest import Event
from .test_ai_limits import usage
from .test_llm_tools import Context, allow, invoke_command
from .test_readings import natal

DEMO = "1990-06-15 男 时辰=辰,巳 真太阳时=关"
ALL_DAY = "1990-06-15 男"


def admin(**kwargs):
    kwargs.setdefault("text", "请按虚构出生资料生成定盘候选")
    return Event(admin=True, **kwargs)


def message(message_id, **kwargs):
    event = admin(**kwargs)
    event.message_obj.message_id = message_id
    return event


async def create(plugin, event=None, birth=DEMO, send_image=False):
    return json.loads(
        await plugin.ziwei_rectification_create(event or admin(), birth, send_image)
    )


def session_count(plugin):
    return plugin.archive.db.execute("SELECT count(*) FROM rectifications").fetchone()[
        0
    ]


def assert_single_image_delivery(context, count=1):
    assert len(context.sent) == count
    for _, chain in context.sent:
        assert len(chain.chain) == 1
        assert chain.chain[0].type == "image"
        assert chain.chain[0].data == b"complete-candidate-collage"


@pytest.fixture
def rendered(monkeypatch):
    calls = []

    def render(*args, **kwargs):
        candidates = next(
            value
            for value in (*args, *kwargs.values())
            if isinstance(value, list)
            and value
            and isinstance(value[0], dict)
            and "chart" in value[0]
        )
        calls.append(copy.deepcopy(candidates))
        return b"complete-candidate-collage"

    monkeypatch.setattr(rectification_service, "render_candidate_collage", render)
    return calls


def test_new_tool_schemas_document_actual_parameters():
    for fn, expected in (
        (
            ZiweiPlugin.ziwei_rectification_create,
            {"birth_info": "string", "send_image": "boolean"},
        ),
        (
            ZiweiPlugin.ziwei_rectification_get,
            {"session_id": "string", "send_image": "boolean"},
        ),
    ):
        parsed = docstring_parser.parse(fn.__doc__)
        assert {
            parameter.arg_name: parameter.type_name for parameter in parsed.params
        } == expected
        assert set(inspect.signature(fn).parameters) == {"self", "event", *expected}
        assert all(parameter.description for parameter in parsed.params)


@pytest.mark.parametrize(
    "event,enabled",
    [
        (Event(text="请求定盘"), True),
        (Event(role="owner", text="请求定盘"), True),
        (Event(role="admin", text="请求定盘"), True),
        (admin(), False),
        (admin(group=""), False),
        (admin(platform=""), False),
        (admin(user=""), True),
    ],
)
def test_authorization_precedes_parse_compute_and_image_delivery(
    event, enabled, monkeypatch, rendered
):
    plugin = ZiweiPlugin(Context(), {})

    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized request must not parse or calculate")

    monkeypatch.setattr(rectification_service, "parse_rectification_request", forbidden)
    monkeypatch.setattr(rectification_service, "build_candidates", forbidden)

    async def run():
        if enabled:
            await allow(plugin, event)
        result = await create(plugin, event, send_image=True)
        assert result["status"] == "denied", result
        replay = json.loads(await plugin.ziwei_rectification_get(event, "", True))
        assert replay["status"] == "denied", replay
        assert not rendered and not plugin.context.sent
        assert session_count(plugin) == 0 and usage(plugin, "natal") == 0
        assert not plugin.chart_cache.entries and plugin.pending == 0
        await plugin.terminate()

    asyncio.run(run())


def test_default_full_day_is_one_natal_charge_without_render_or_current_chart(rendered):
    plugin = ZiweiPlugin(Context(), {})
    event = admin()
    scope = capture_group(event)

    async def run():
        await allow(plugin, event)
        result = await create(plugin, event, ALL_DAY)
        assert result["status"] == "ok", result
        assert result["image_status"] == "not_requested"
        assert len(result["candidates"]) == 14
        stored = plugin.archive.rectifications.get(scope, result["session_id"])
        assert len(stored["candidates"]) == 14 and stored["selected"] == ""
        assert session_count(plugin) == usage(plugin, "natal") == 1
        assert usage(plugin, "question") == usage(plugin, "decade") == 0
        assert not plugin.archive.list_charts(scope) and not plugin.chart_cache.entries
        assert not rendered and not plugin.context.sent
        assert all(value is True for value in plugin.db.values())
        await plugin.terminate()

    asyncio.run(run())


def test_requested_image_contains_all_candidates_in_one_delivery(rendered):
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        await allow(plugin)
        result = await create(plugin, birth=ALL_DAY, send_image=True)
        assert result["status"] == "ok" and result["image_status"] == "sent", result
        assert len(rendered) == 1 and len(rendered[0]) == 14
        assert {item["id"] for item in rendered[0]} == {
            item["candidate_id"] for item in result["candidates"]
        }
        assert_single_image_delivery(plugin.context)
        assert usage(plugin, "natal") == 1 and usage(plugin, "question") == 0
        assert not plugin.chart_cache.entries
        await plugin.terminate()

    asyncio.run(run())


def test_repeated_real_message_reuses_batch_and_sends_requested_collage_once(rendered):
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        await allow(plugin)
        first = await create(plugin, message("same-message"))
        second = await create(plugin, message("same-message"), send_image=True)
        third = await create(plugin, message("same-message"), send_image=True)
        assert first["status"] == second["status"] == third["status"] == "ok"
        assert first["session_id"] == second["session_id"] == third["session_id"]
        assert first["candidates"] == second["candidates"] == third["candidates"]
        replay = json.loads(
            await plugin.ziwei_rectification_get(
                message("same-message"), first["session_id"], True
            )
        )
        assert replay["status"] == "ok", replay
        assert_single_image_delivery(plugin.context)
        assert len(rendered) == session_count(plugin) == usage(plugin, "natal") == 1
        altered = await create(
            plugin,
            message("same-message"),
            DEMO.replace("1990-06-15", "1991-06-15"),
            True,
        )
        assert altered["status"] == "invalid_input", altered
        assert session_count(plugin) == usage(plugin, "natal") == 1
        assert len(plugin.context.sent) == 1
        await plugin.terminate()

    asyncio.run(run())


def test_concurrent_creation_deduplicates_candidates_quota_and_delivery(rendered):
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        await allow(plugin)
        first, second = await asyncio.gather(
            create(plugin, message("concurrent-create"), send_image=True),
            create(plugin, message("concurrent-create"), send_image=True),
        )
        assert first["status"] == second["status"] == "ok", (first, second)
        assert first["session_id"] == second["session_id"]
        assert session_count(plugin) == usage(plugin, "natal") == 1
        assert len(rendered) == 1
        assert_single_image_delivery(plugin.context)
        assert plugin.pending == 0
        await plugin.terminate()

    asyncio.run(run())


def test_concurrent_get_resend_is_once_per_message_and_never_charges(rendered):
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        await allow(plugin)
        created = await create(plugin)
        sid = created["session_id"]
        results = await asyncio.gather(
            plugin.ziwei_rectification_get(message("resend-once"), sid, True),
            plugin.ziwei_rectification_get(message("resend-once"), sid, True),
        )
        assert all(json.loads(result)["status"] == "ok" for result in results), results
        assert_single_image_delivery(plugin.context)
        assert len(rendered) == 1
        again = json.loads(
            await plugin.ziwei_rectification_get(message("later-resend"), sid, True)
        )
        assert again["image_status"] == "sent", again
        assert_single_image_delivery(plugin.context, 2)
        assert session_count(plugin) == usage(plugin, "natal") == 1
        assert usage(plugin, "question") == 0
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["exception", "rejected"])
def test_failed_delivery_can_retry_same_batch_without_new_charge(failure, rendered):
    context = Context(failure=failure == "exception", accepted=failure != "rejected")
    plugin = ZiweiPlugin(context, {})

    async def run():
        await allow(plugin)
        first = await create(plugin, message("retry-send"), send_image=True)
        assert first["status"] == "ok" and first["image_status"] == "failed", first
        assert session_count(plugin) == usage(plugin, "natal") == 1
        context.failure, context.accepted = False, True
        retried = await create(plugin, message("retry-send"), send_image=True)
        assert retried["status"] == "ok" and retried["image_status"] == "sent", retried
        assert retried["session_id"] == first["session_id"]
        assert session_count(plugin) == usage(plugin, "natal") == 1
        count = len(context.sent)
        await create(plugin, message("retry-send"), send_image=True)
        assert len(context.sent) == count
        assert context.sent[-1][1].chain[0].data == b"complete-candidate-collage"
        await plugin.terminate()

    asyncio.run(run())


def test_render_failure_preserves_batch_for_free_get_retry(monkeypatch, rendered):
    plugin = ZiweiPlugin(Context(), {})
    original = rectification_service.render_candidate_collage

    def broken(*args, **kwargs):
        raise OSError("synthetic render failure")

    async def run():
        await allow(plugin)
        monkeypatch.setattr(rectification_service, "render_candidate_collage", broken)
        failed = await create(plugin, send_image=True)
        assert failed["status"] == "ok" and failed["image_status"] == "failed", failed
        assert session_count(plugin) == usage(plugin, "natal") == 1
        assert not plugin.context.sent
        monkeypatch.setattr(rectification_service, "render_candidate_collage", original)
        retried = json.loads(
            await plugin.ziwei_rectification_get(admin(), failed["session_id"], True)
        )
        assert retried["image_status"] == "sent", retried
        assert_single_image_delivery(plugin.context)
        assert session_count(plugin) == usage(plugin, "natal") == 1
        await plugin.terminate()

    asyncio.run(run())


def test_batch_shares_existing_natal_limit_but_manual_creation_remains_unlimited(
    rendered,
):
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        await allow(plugin)
        assert (
            json.loads(await plugin.ziwei_paipan(admin(), DEMO_BIRTH))["status"] == "ok"
        )
        batch = await create(plugin, message("last-slot"))
        assert batch["status"] == "ok"
        blocked = await create(plugin, message("third-generation"))
        assert blocked["status"] == "rate_limited" and blocked["quota"] == "natal"
        assert (
            json.loads(await plugin.ziwei_paipan(admin(), DEMO_BIRTH))["status"]
            == "rate_limited"
        )
        duplicate = await create(plugin, message("last-slot"), send_image=True)
        assert (
            duplicate["status"] == "ok"
            and duplicate["session_id"] == batch["session_id"]
        )
        await invoke_command(plugin, "定盘 " + DEMO, admin())
        assert session_count(plugin) == 2 and usage(plugin, "natal") == 2
        assert usage(plugin, "question") == 0
        await plugin.terminate()

    asyncio.run(run())


def test_reload_preserves_idempotent_batch_and_confirmed_usage(rendered):
    context = Context()
    plugin = ZiweiPlugin(context, {})

    async def run():
        await allow(plugin)
        first = await create(plugin, message("before-reload"))
        assert first["status"] == "ok"
        await plugin.terminate()
        restored = ZiweiPlugin(context, {})
        result = await create(restored, message("before-reload"), send_image=True)
        assert result["status"] == "ok" and result["session_id"] == first["session_id"]
        assert session_count(restored) == usage(restored, "natal") == 1
        assert not restored.chart_cache.entries
        assert_single_image_delivery(context)
        await restored.terminate()

    asyncio.run(run())


def test_compute_failure_refunds_and_next_attempt_can_reuse_same_event(
    monkeypatch, rendered
):
    plugin = ZiweiPlugin(Context(), {})
    original = rectification_service.build_candidates

    def broken(*args, **kwargs):
        raise OSError("synthetic calculation failure")

    async def run():
        await allow(plugin)
        monkeypatch.setattr(rectification_service, "build_candidates", broken)
        failed = await create(plugin, message("retry-calculation"), send_image=True)
        assert failed["status"] == "unavailable", failed
        assert session_count(plugin) == usage(plugin, "natal") == 0
        assert not rendered and not plugin.context.sent
        monkeypatch.setattr(rectification_service, "build_candidates", original)
        retried = await create(plugin, message("retry-calculation"), send_image=True)
        assert retried["status"] == "ok", retried
        assert session_count(plugin) == usage(plugin, "natal") == 1
        assert_single_image_delivery(plugin.context)
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("transition", ["close", "reload"])
def test_rendering_finishes_without_delivery_after_group_close_or_reload(
    transition, monkeypatch, rendered
):
    plugin = ZiweiPlugin(Context(), {})
    entered, released = threading.Event(), threading.Event()
    original = rectification_service.render_candidate_collage

    def delayed(*args, **kwargs):
        entered.set()
        assert released.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(rectification_service, "render_candidate_collage", delayed)

    async def run():
        await allow(plugin)
        task = asyncio.create_task(create(plugin, send_image=True))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            if transition == "close":
                await invoke_command(plugin, "关闭", admin())
            else:
                await plugin.terminate()
        finally:
            released.set()
        result = await asyncio.wait_for(task, 5)
        assert result["status"] in {"denied", "unavailable"}, result
        assert not plugin.context.sent and not plugin.chart_cache.entries
        assert plugin.pending == 0
        if transition == "close":
            assert usage(plugin, "natal") == 1 and session_count(plugin) == 1
            await plugin.terminate()

    asyncio.run(run())


def test_authorization_await_cannot_redirect_batch_owner_or_image_route(rendered):
    plugin = ZiweiPlugin(Context(), {})
    event = admin()
    original_scope = capture_group(event)
    original_get = plugin.access.get

    async def mutate(*args):
        event.platform, event.group, event.user = (
            "other-platform",
            "other-group",
            "other-user",
        )
        event.admin = False
        event.unified_msg_origin = "other-platform:GroupMessage:other-group"
        return await original_get(*args)

    async def run():
        await allow(plugin, event)
        plugin.access.get = mutate
        result = await create(plugin, event, send_image=True)
        assert result["status"] == "ok", result
        assert plugin.archive.rectifications.get(original_scope, result["session_id"])
        assert plugin.context.sent[0][0] == original_scope.umo
        assert_single_image_delivery(plugin.context)
        plugin.access.get = original_get
        outsider = admin(
            platform="other-platform", group="other-group", user="other-user"
        )
        await allow(plugin, outsider)
        denied = json.loads(
            await plugin.ziwei_rectification_get(outsider, result["session_id"], True)
        )
        assert denied["status"] in {"invalid_input", "not_found"}, denied
        assert len(plugin.context.sent) == 1
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("transition", ["close", "reload"])
def test_queued_batch_checks_permission_before_compute(
    transition, monkeypatch, rendered
):
    plugin = ZiweiPlugin(Context(), {})
    computed = []

    def forbidden(*args, **kwargs):
        computed.append(True)
        raise AssertionError("closed queue must not compute")

    monkeypatch.setattr(rectification_service, "build_candidates", forbidden)

    async def run():
        await allow(plugin)
        await plugin.gate.acquire()
        await plugin.gate.acquire()
        task = asyncio.create_task(create(plugin, send_image=True))
        try:
            for _ in range(100):
                if plugin.pending or task.done():
                    break
                await asyncio.sleep(0)
            assert plugin.pending == 1 and not task.done()
            if transition == "close":
                await invoke_command(plugin, "关闭", admin())
            else:
                await plugin.terminate()
        finally:
            plugin.gate.release()
            plugin.gate.release()
        result = await asyncio.wait_for(task, 3)
        assert result["status"] in {"denied", "unavailable"}, result
        assert not computed and not rendered and not plugin.context.sent
        assert plugin.pending == 0
        if transition == "close":
            assert usage(plugin, "natal") == session_count(plugin) == 0
            await plugin.terminate()

    asyncio.run(run())


def test_total_image_command_delivers_one_complete_collage_without_quota(rendered):
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        await allow(plugin)
        await invoke_command(plugin, "定盘 " + ALL_DAY, admin())
        sid = plugin.archive.rectifications.get(capture_group(admin()))["id"]
        replies = await invoke_command(plugin, f"定盘 总图 {sid}", admin())
        images = [
            component
            for reply in replies
            if isinstance(reply, list)
            for component in reply
            if getattr(component, "type", None) == "image"
        ]
        sent_images = [
            component
            for _, chain in plugin.context.sent
            for component in chain.chain
            if getattr(component, "type", None) == "image"
        ]
        assert len(images) + len(sent_images) == 1
        assert len(rendered) == 1 and len(rendered[0]) == 14
        assert usage(plugin, "natal") == usage(plugin, "question") == 0
        assert session_count(plugin) == 1 and not plugin.chart_cache.entries
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize(
    "birth,send_image",
    [
        ("not-a-birth-date", False),
        ("", False),
        (None, False),
        ({"date": "1990-06-15"}, False),
        (DEMO, "true"),
        (DEMO, 1),
        (DEMO + " 流年=2026", False),
    ],
)
def test_invalid_creation_inputs_never_consume_quota_or_create_records(
    birth, send_image, rendered
):
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        await allow(plugin)
        result = await create(plugin, birth=birth, send_image=send_image)
        assert result["status"] == "invalid_input", result
        assert session_count(plugin) == usage(plugin, "natal") == 0
        assert not rendered and not plugin.context.sent
        assert plugin.pending == 0
        await plugin.terminate()

    asyncio.run(run())


def test_archive_failure_refunds_reserved_natal_usage_and_allows_same_message_retry(
    monkeypatch, rendered
):
    plugin = ZiweiPlugin(Context(), {})
    original = plugin.archive.rectifications.create

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic archive failure")

    async def run():
        await allow(plugin)
        monkeypatch.setattr(plugin.archive.rectifications, "create", broken)
        failed = await create(plugin, message("retry-archive"), send_image=True)
        assert failed["status"] == "unavailable", failed
        assert session_count(plugin) == usage(plugin, "natal") == 0
        assert not rendered and not plugin.context.sent
        monkeypatch.setattr(plugin.archive.rectifications, "create", original)
        retried = await create(plugin, message("retry-archive"), send_image=True)
        assert retried["status"] == "ok" and retried["image_status"] == "sent", retried
        assert session_count(plugin) == usage(plugin, "natal") == 1
        assert_single_image_delivery(plugin.context)
        await plugin.terminate()

    asyncio.run(run())


def test_successful_delivery_claim_survives_reload_but_new_message_can_resend(rendered):
    context = Context()
    plugin = ZiweiPlugin(context, {})

    async def run():
        await allow(plugin)
        first = await create(
            plugin, message("delivered-before-reload"), send_image=True
        )
        assert first["status"] == "ok" and first["image_status"] == "sent", first
        assert_single_image_delivery(context)
        await plugin.terminate()
        restored = ZiweiPlugin(context, {})
        repeated = await create(
            restored, message("delivered-before-reload"), send_image=True
        )
        assert repeated["status"] == "ok", repeated
        assert repeated["session_id"] == first["session_id"]
        assert len(context.sent) == len(rendered) == 1
        fresh = json.loads(
            await restored.ziwei_rectification_get(
                message("new-replay-after-reload"), first["session_id"], True
            )
        )
        assert fresh["status"] == "ok" and fresh["image_status"] == "sent", fresh
        assert_single_image_delivery(context, 2)
        assert session_count(restored) == usage(restored, "natal") == 1
        await restored.terminate()

    asyncio.run(run())


def test_real_full_day_creation_sends_one_vertical_png_and_preserves_current_chart(
    tmp_path,
):
    """No candidate or image stub: run the real calculation and complete PNG path."""
    plugin = ZiweiPlugin(Context(), {})
    if plugin.renderer is None:
        pytest.skip("需要中文字体验收真实全天候选拼图")
    scope = capture_group(admin())
    previous_chart = natal()
    previous_id = plugin.archive.save_chart(scope, previous_chart)
    plugin.chart_cache.put(scope, previous_chart, chart_id=previous_id)
    original = plugin.renderer.render
    source_pngs, image_sizes = [], []

    def observe_real_render(chart, theme="day"):
        png = original(chart, theme)
        source_pngs.append(png)
        with PillowImage.open(io.BytesIO(png)) as image:
            image_sizes.append(image.size)
        return png

    plugin.renderer.render = observe_real_render

    async def run():
        await allow(plugin)
        started = time.perf_counter()
        result = await create(plugin, birth=ALL_DAY, send_image=True)
        elapsed = time.perf_counter() - started
        assert result["status"] == "ok" and result["image_status"] == "sent", result
        assert len(result["candidates"]) == 14
        stored = plugin.archive.rectifications.get(scope, result["session_id"])
        assert len(stored["candidates"]) == len(source_pngs) == 14
        assert [candidate["id"] for candidate in stored["candidates"]] == [
            f"c{index:02}" for index in range(1, 15)
        ]
        assert len(plugin.context.sent) == 1
        route, chain = plugin.context.sent[0]
        assert route == scope.umo
        assert len(chain.chain) == 1 and chain.chain[0].type == "image"
        png = chain.chain[0].data
        output = tmp_path / "rectification-collage-default.png"
        output.write_bytes(png)
        plan = plan_candidate_collage(
            stored["candidates"], image_sizes, plugin.renderer.font_path
        )
        assert plan["columns"] == 1 and plan["rows"] == 14
        assert len(plan["cells"]) == 14 and not plan["empty_cells"]
        with PillowImage.open(output) as image:
            image.load()
            assert image.format == "PNG" and image.mode == "RGB"
            assert image.size == tuple(plan["size"])
            assert image.height > 10 * image.width
            for cell, source_png in zip(plan["cells"], source_pngs, strict=True):
                with PillowImage.open(io.BytesIO(source_png)) as source:
                    with source.convert("RGB") as rgb:
                        with rgb.resize(
                            tuple(cell["image_size"]), PillowImage.Resampling.LANCZOS
                        ) as expected:
                            with image.crop(tuple(cell["image_box"])) as actual:
                                assert actual.size == expected.size
                                assert actual.tobytes() == expected.tobytes()
        current = plugin.chart_cache.get(scope)
        assert current.chart_id == previous_id and current.chart == previous_chart
        assert len(plugin.archive.list_charts(scope)) == 1
        assert session_count(plugin) == usage(plugin, "natal") == 1
        assert usage(plugin, "question") == usage(plugin, "decade") == 0
        assert stored["selected"] == "" and stored["chart_id"] == ""
        print(
            f"real_collage_file={output}; size={plan['size']}; "
            f"bytes={len(png)}; candidates=14; images=1; "
            f"natal_usage=1; elapsed_seconds={elapsed:.3f}"
        )
        await plugin.terminate()

    asyncio.run(run())


def test_cancelled_render_holds_image_lock_until_thread_exits_and_allows_retry(
    monkeypatch, rendered
):
    plugin = ZiweiPlugin(Context(), {})
    first_entered, first_release, second_entered = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    original = rectification_service.render_candidate_collage
    calls = []

    def delayed(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            first_entered.set()
            assert first_release.wait(5)
        else:
            second_entered.set()
        return original(*args, **kwargs)

    async def run():
        await allow(plugin)
        batch = await create(plugin)
        sid = batch["session_id"]
        monkeypatch.setattr(rectification_service, "render_candidate_collage", delayed)
        first = asyncio.create_task(
            plugin.ziwei_rectification_get(message("cancelled-collage"), sid, True)
        )
        second = None
        try:
            assert await asyncio.to_thread(first_entered.wait, 3)
            first.cancel()
            await asyncio.sleep(0)
            assert not first.done()
            second = asyncio.create_task(
                plugin.ziwei_rectification_get(message("next-collage"), sid, True)
            )
            for _ in range(100):
                if plugin.pending == 2:
                    break
                await asyncio.sleep(0)
            assert plugin.pending == 2
            first.cancel()
            await asyncio.sleep(0)
            assert not first.done() and not second_entered.is_set()
            assert not plugin.context.sent and len(calls) == 1
        finally:
            first_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 5)
        result = json.loads(await asyncio.wait_for(second, 5))
        assert result["status"] == "ok" and result["image_status"] == "sent", result
        assert second_entered.is_set() and len(calls) == 2
        assert_single_image_delivery(plugin.context)
        assert plugin.pending == 0
        retry = json.loads(
            await plugin.ziwei_rectification_get(
                message("cancelled-collage"), sid, True
            )
        )
        assert retry["status"] == "ok" and retry["image_status"] == "sent", retry
        assert_single_image_delivery(plugin.context, 2)
        assert session_count(plugin) == usage(plugin, "natal") == 1
        assert plugin.pending == 0
        await plugin.terminate()

    asyncio.run(run())
