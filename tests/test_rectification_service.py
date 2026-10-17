"""Real candidate calculations with local AstrBot doubles, never live QQ evidence."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.engine import apply_decade
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .test_ai_limits import usage
from .test_llm_tools import Context, allow, invoke_command
from .test_readings import ModelContext, answer, natal

DEMO = "1990-06-15 男 时辰=辰,巳 真太阳时=关"


def administrator(**kwargs):
    return Event(admin=True, **kwargs)


async def invoke(plugin, args, event=None):
    return await invoke_command(plugin, "定盘 " + args, event or administrator())


async def create_session(plugin, event=None, args=DEMO):
    event = event or administrator()
    await allow(plugin, event)
    replies = await invoke(plugin, args, event)
    assert replies and all(isinstance(reply, str) for reply in replies), replies
    result = json.loads(await plugin.ziwei_rectification_get(event))
    assert result["status"] == "ok", (replies, result)
    assert result["session_id"]
    assert len(result["candidates"]) >= 2
    return result


@pytest.mark.parametrize("role", ["member", "admin", "owner"])
def test_group_members_and_group_managers_cannot_use_any_rectification_entry(role):
    plugin = ZiweiPlugin(Context(), {})
    event = Event(role=role, text="这段经历与盘面是否一致？")

    async def run():
        await allow(plugin, event)
        for args in (DEMO, "状态", "查看 c01", "确认 c01", "取消", "重试总结"):
            replies = await invoke(plugin, args, event)
            assert len(replies) == 1 and "AstrBot" in replies[0], replies
        results = [
            await plugin.ziwei_rectification_get(event),
            await plugin.ziwei_rectification_context(event),
            await plugin.ziwei_rectification_feedback(event, "invalid-session"),
            await plugin.ziwei_rectification_assess(event, "invalid-session", "{}"),
            await plugin.ziwei_rectification_finish(event, "invalid-session", True),
        ]
        for result in results:
            value = json.loads(result)
            assert value["status"] == "denied", value
            assert "candidates" not in value and "facts" not in value
        assert not plugin.archive.list_charts(capture_group(event))
        assert not plugin.chart_cache.entries and plugin.pending == 0
        assert usage(plugin, "question") == usage(plugin, "natal") == 0
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize(
    "event,expected",
    [
        (administrator(), "未开启"),
        (administrator(group=""), "群聊"),
        (administrator(platform=""), "平台实例"),
        (administrator(user=""), "发送者"),
    ],
)
def test_admin_requires_enabled_identified_group_and_user(event, expected):
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        if not event.user:
            await allow(plugin, event)
        replies = await invoke(plugin, DEMO, event)
        assert len(replies) == 1 and expected in replies[0], replies
        result = json.loads(await plugin.ziwei_rectification_context(event))
        assert result["status"] == "denied" and "candidates" not in result
        assert plugin.pending == 0 and not plugin.chart_cache.entries
        assert usage(plugin, "question") == usage(plugin, "natal") == 0
        await plugin.terminate()

    asyncio.run(run())


def test_candidate_creation_and_status_preserve_selected_chart_without_ai_charges():
    plugin = ZiweiPlugin(Context(), {})
    event = administrator()
    scope = capture_group(event)

    async def run():
        await allow(plugin, event)
        current = json.loads(await plugin.ziwei_paipan(event, DEMO_BIRTH))
        assert current["status"] == "ok"
        before = plugin.chart_cache.get(scope)
        row = await create_session(plugin, event)
        assert plugin.chart_cache.get(scope).chart_id == before.chart_id
        assert len(plugin.archive.list_charts(scope)) == 1
        assert usage(plugin, "question") == 0
        assert usage(plugin, "natal") == 1
        assert not plugin.context.sent
        assert all(value is True for value in plugin.db.values())
        for _ in range(3):
            repeated = json.loads(
                await plugin.ziwei_rectification_get(event, row["session_id"])
            )
            assert repeated["session_id"] == row["session_id"]
            assert "knowledge" not in repeated and "facts" not in repeated
        assert usage(plugin, "question") == 0
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize(
    "outsider",
    [
        administrator(user="other-user"),
        administrator(group="other-group"),
        administrator(platform="other-platform"),
    ],
)
def test_administrators_cannot_read_or_mutate_another_owners_session(outsider):
    plugin = ZiweiPlugin(Context(), {})
    owner = administrator(text="请比较候选时辰")

    async def run():
        row = await create_session(plugin, owner)
        sid = row["session_id"]
        await allow(plugin, outsider)
        for result in (
            await plugin.ziwei_rectification_get(outsider, sid),
            await plugin.ziwei_rectification_context(outsider, sid),
            await plugin.ziwei_rectification_feedback(outsider, sid),
            await plugin.ziwei_rectification_assess(outsider, sid, "{}"),
            await plugin.ziwei_rectification_finish(outsider, sid, True),
        ):
            value = json.loads(result)
            assert value["status"] in {"not_found", "invalid_input"}, value
            assert "candidates" not in value and "facts" not in value
        for args in (f"查看 c01 {sid}", f"确认 c01 {sid}", f"取消 {sid}"):
            replies = await invoke(plugin, args, outsider)
            assert replies and "1990-06-15" not in "\n".join(replies)
        original = json.loads(await plugin.ziwei_rectification_get(owner, sid))
        assert original["session_id"] == sid
        assert original["candidates"] == row["candidates"]
        assert usage(plugin, "question") == 0
        assert not plugin.archive.list_charts(capture_group(owner))
        assert not plugin.archive.list_charts(capture_group(outsider))
        await plugin.terminate()

    asyncio.run(run())


def test_context_deduplicates_real_messages_and_feedback_cannot_bypass_quota():
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        row = await create_session(plugin)
        sid = row["session_id"]
        first = administrator(text="请先比较事业经历差异")
        first.message_obj.message_id = "same-question"
        result = json.loads(await plugin.ziwei_rectification_context(first, sid))
        assert result["status"] == "ok", result
        assert len(result["candidates"]) == 2
        assert usage(plugin, "question") == 1
        again = administrator(text=first.message_str)
        again.message_obj.message_id = "same-question"
        assert (
            json.loads(await plugin.ziwei_rectification_context(again, sid))["status"]
            == "ok"
        )
        assert usage(plugin, "question") == 1
        await answer(plugin, first, "两组候选均保留，请描述已发生的职业变动。")
        for index in range(5):
            followup = administrator(text=f"继续比较第 {index + 2} 项经历")
            result = json.loads(await plugin.ziwei_rectification_context(followup, sid))
            assert result["status"] == "ok", result
        assert usage(plugin, "question") == 6
        blocked = administrator(text="请继续提供新的分析")
        assert (
            json.loads(await plugin.ziwei_rectification_context(blocked, sid))["status"]
            == "rate_limited"
        )
        feedback_event = administrator(text="虚构反馈：没有经历上述职业变动。")
        feedback = json.loads(
            await plugin.ziwei_rectification_feedback(feedback_event, sid)
        )
        assert feedback["status"] == "recorded", feedback
        assert "knowledge" not in feedback and "facts" not in feedback
        assert "candidates" not in feedback
        assert (
            json.loads(await plugin.ziwei_rectification_context(feedback_event, sid))[
                "status"
            ]
            == "rate_limited"
        )
        assert (
            json.loads(await plugin.ziwei_rectification_get(administrator(), sid))[
                "status"
            ]
            == "ok"
        )
        assert usage(plugin, "question") == 6
        await plugin.terminate()

    asyncio.run(run())


def test_real_event_identity_and_admin_snapshot_do_not_follow_later_mutation():
    plugin = ZiweiPlugin(Context(), {})
    event = administrator(text="请开始比较候选")
    original_scope = capture_group(event)
    original_get = plugin.access.get

    async def mutate(*args):
        event.platform = "other-platform"
        event.group = "other-group"
        event.user = "other-user"
        event.admin = False
        event.unified_msg_origin = "other-platform:GroupMessage:other-group"
        return await original_get(*args)

    async def run():
        await allow(plugin, event)
        plugin.access.get = mutate
        replies = await invoke(plugin, DEMO, event)
        assert replies
        plugin.access.get = original_get
        original = json.loads(await plugin.ziwei_rectification_get(administrator()))
        assert original["status"] == "ok", (replies, original)
        assert not plugin.archive.list_charts(original_scope)
        outsider = administrator(
            platform="other-platform", group="other-group", user="other-user"
        )
        await allow(plugin, outsider)
        assert json.loads(
            await plugin.ziwei_rectification_get(outsider, original["session_id"])
        )["status"] in {"not_found", "invalid_input"}
        assert not plugin.chart_cache.entries
        await plugin.terminate()

    asyncio.run(run())


def test_reload_while_reading_group_authorization_never_returns_session_data():
    plugin = ZiweiPlugin(Context(), {})
    original_get = plugin.access.get

    async def terminating(*args):
        allowed = await original_get(*args)
        await plugin.terminate()
        return allowed

    async def run():
        row = await create_session(plugin)
        plugin.access.get = terminating
        result = json.loads(
            await plugin.ziwei_rectification_get(administrator(), row["session_id"])
        )
        assert result["status"] == "denied", result
        assert "candidates" not in result and "session_id" not in result
        assert not plugin.chart_cache.entries

    asyncio.run(run())


@pytest.mark.parametrize("transition", ["close", "reload"])
def test_generation_rechecks_authorization_before_archiving(transition, monkeypatch):
    from astrbot_plugin_ziwei import rectification_service

    plugin = ZiweiPlugin(Context(), {})
    entered, released = threading.Event(), threading.Event()
    original = rectification_service.build_candidates

    def delayed(*args, **kwargs):
        entered.set()
        assert released.wait(5), "test did not release candidate worker"
        return original(*args, **kwargs)

    monkeypatch.setattr(rectification_service, "build_candidates", delayed)

    async def run():
        await allow(plugin)
        task = asyncio.create_task(invoke(plugin, DEMO))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            if transition == "close":
                await invoke_command(plugin, "关闭", administrator())
            else:
                await plugin.terminate()
        finally:
            released.set()
        replies = await asyncio.wait_for(task, 5)
        assert replies and all(isinstance(reply, str) for reply in replies)
        assert ("未开启" if transition == "close" else "重载") in replies[0]
        assert not plugin.chart_cache.entries and plugin.pending == 0
        if transition == "close":
            await allow(plugin)
            result = json.loads(await plugin.ziwei_rectification_get(administrator()))
            assert result["status"] in {"not_found", "invalid_input"}, result
            assert not plugin.archive.list_charts(capture_group(administrator()))
            await plugin.terminate()

    asyncio.run(run())


def test_question_quota_is_shared_with_existing_reading_tools_and_survives_reload():
    context = Context()
    plugin = ZiweiPlugin(context, {})

    async def run():
        row = await create_session(plugin)
        sid = row["session_id"]
        event = administrator(text="本命事业如何")
        await plugin.ziwei_paipan(event, DEMO_BIRTH)
        reading = json.loads(await plugin.ziwei_analysis_context(event, topic="career"))
        assert reading["status"] == "ok"
        for index in range(5):
            event = administrator(text=f"候选对比问题 {index}")
            assert (
                json.loads(await plugin.ziwei_rectification_context(event, sid))[
                    "status"
                ]
                == "ok"
            )
        assert usage(plugin, "question") == 6
        await plugin.terminate()
        restored = ZiweiPlugin(context, {})
        result = json.loads(
            await restored.ziwei_rectification_context(
                administrator(text="重载后继续问"), sid
            )
        )
        assert result["status"] == "rate_limited", result
        status = json.loads(
            await restored.ziwei_rectification_get(administrator(), sid)
        )
        assert status["status"] == "ok" and status["candidates"] == row["candidates"]
        assert not restored.chart_cache.entries
        await restored.terminate()

    asyncio.run(run())


def test_view_does_not_select_chart_and_explicit_confirmation_is_idempotent():
    plugin = ZiweiPlugin(Context(), {})
    scope = capture_group(administrator())
    rendered = []

    class Renderer:
        def render(self, chart, theme="day"):
            rendered.append((chart, theme))
            return b"synthetic-preview"

    plugin.renderer = Renderer()

    async def run():
        row = await create_session(plugin)
        sid = row["session_id"]
        viewed = await invoke(plugin, f"查看 c01 {sid}")
        assert viewed and rendered
        assert not plugin.archive.list_charts(scope)
        assert not plugin.chart_cache.entries
        selected = await invoke(plugin, f"确认 c01 {sid}")
        assert selected and len(plugin.archive.list_charts(scope)) == 1
        current = plugin.chart_cache.get(scope)
        assert current
        assert {
            key: value for key, value in current.chart.items() if key != "rectification"
        } == {
            key: value
            for key, value in rendered[0][0].items()
            if key != "rectification"
        }
        assert rendered[0][0]["rectification"] == {
            "candidate_id": "c01",
            "intervals": row["candidates"][0]["intervals"],
            "representative_only": True,
        }
        assert current.chart["rectification"] == {
            "session_id": sid,
            "candidate_id": "c01",
            "intervals": row["candidates"][0]["intervals"],
            "representative_only": True,
        }
        assert current.chart.get("flow") is None
        await invoke(plugin, f"确认 c01 {sid}")
        assert len(plugin.archive.list_charts(scope)) == 1
        assert plugin.chart_cache.get(scope).chart_id == current.chart_id
        assert usage(plugin, "question") == usage(plugin, "natal") == 0
        await plugin.terminate()

    asyncio.run(run())


def test_context_requires_real_user_text_and_limits_candidate_comparison():
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        row = await create_session(plugin, args=DEMO.replace("辰,巳", "辰,巳,午"))
        sid = row["session_id"]
        blank = json.loads(
            await plugin.ziwei_rectification_context(administrator(), sid)
        )
        assert blank["status"] == "invalid_input"
        for selection in ("c01,c02,c03", "unknown-candidate", "c01,c01"):
            result = json.loads(
                await plugin.ziwei_rectification_context(
                    administrator(text="请比较候选"), sid, selection
                )
            )
            assert result["status"] == "invalid_input", result
        assert usage(plugin, "question") == 0
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("transition", ["close", "reload"])
def test_queued_creation_rechecks_access_before_computing(transition, monkeypatch):
    from astrbot_plugin_ziwei import rectification_service

    plugin = ZiweiPlugin(Context(), {})
    computed = []

    def forbidden(*args, **kwargs):
        computed.append(True)
        raise AssertionError("closed queued request must not compute")

    monkeypatch.setattr(rectification_service, "build_candidates", forbidden)

    async def run():
        await allow(plugin)
        await plugin.gate.acquire()
        await plugin.gate.acquire()
        task = asyncio.create_task(invoke(plugin, DEMO))
        try:
            for _ in range(100):
                if plugin.pending or task.done():
                    break
                await asyncio.sleep(0)
            assert plugin.pending == 1 and not task.done()
            if transition == "close":
                await invoke_command(plugin, "关闭", administrator())
            else:
                await plugin.terminate()
        finally:
            plugin.gate.release()
            plugin.gate.release()
        replies = await asyncio.wait_for(task, 3)
        assert replies and ("未开启" if transition == "close" else "重载") in replies[0]
        assert not computed and not plugin.chart_cache.entries
        assert plugin.pending == 0
        if transition == "close":
            await plugin.terminate()

    asyncio.run(run())


def test_assessment_uses_archived_evidence_and_closed_loop_has_isolated_summary():
    context = ModelContext()
    plugin = ZiweiPlugin(context, {})
    owner = administrator()
    scope = capture_group(owner)

    async def run():
        row = await create_session(plugin, owner)
        sid = row["session_id"]
        first = administrator(text="请对比两个候选的迁居经历")
        result = json.loads(await plugin.ziwei_rectification_context(first, sid))
        assert result["status"] == "ok"
        assert (
            json.loads(await plugin.ziwei_rectification_finish(first, sid, True))[
                "status"
            ]
            == "invalid_input"
        )
        await answer(plugin, first, "候选均暂时保留，请描述已经发生的迁居。")
        actual = administrator(text="虚构经历：2014 年曾迁居外省。")
        recorded = json.loads(await plugin.ziwei_rectification_feedback(actual, sid))
        assert recorded["status"] == "recorded"
        stored = plugin.archive.rectifications.get(scope, sid)
        seq = next(
            turn["seq"] for turn in stored["turns"] if turn["kind"] == "feedback"
        )
        question = administrator(text="请结合刚才的迁居经历继续对比")
        result = json.loads(await plugin.ziwei_rectification_context(question, sid))
        candidate = result["candidates"][0]
        knowledge_id = candidate["knowledge"]["cards"][0]["id"]
        assessment = {
            candidate["candidate_id"]: {
                "support": ["用户自述曾迁居，仅作为待继续核对的支持线索"],
                "conflict": [],
                "unknown": ["其他年份仍待核对"],
                "evidence_sequences": [seq],
                "knowledge_ids": [knowledge_id],
            }
        }
        invalid = json.loads(json.dumps(assessment))
        invalid[candidate["candidate_id"]]["evidence_sequences"] = [seq + 999]
        assert (
            json.loads(
                await plugin.ziwei_rectification_assess(
                    question, sid, json.dumps(invalid)
                )
            )["status"]
            == "invalid_input"
        )
        invalid = json.loads(json.dumps(assessment))
        invalid[candidate["candidate_id"]]["knowledge_ids"] = ["invented-source"]
        assert (
            json.loads(
                await plugin.ziwei_rectification_assess(
                    question, sid, json.dumps(invalid)
                )
            )["status"]
            == "invalid_input"
        )
        accepted = json.loads(
            await plugin.ziwei_rectification_assess(
                question, sid, json.dumps(assessment, ensure_ascii=False)
            )
        )
        assert accepted["status"] == "saved", accepted
        await answer(plugin, question, "迁居经历只提供有限支持，两组候选继续保留。")
        final = administrator(text="本轮已经回答完毕，暂时保留两个候选。")
        finished = json.loads(await plugin.ziwei_rectification_finish(final, sid, True))
        assert finished["status"] == "queued", finished
        for _ in range(100):
            if (
                plugin.archive.rectifications.get(scope, sid)["summary_status"]
                == "archived"
            ):
                break
            await asyncio.sleep(0.01)
        stored = plugin.archive.rectifications.get(scope, sid)
        assert stored["summary_status"] == "archived"
        assert stored["selected"] == "" and not stored["chart_id"]
        assert stored["assessment"] == assessment
        assert len(context.calls) == 1
        model_call = context.calls[0]
        assert model_call["contexts"] == [] and "tools" not in model_call
        assert model_call["chat_provider_id"] == "configured-provider"
        payload = json.loads(model_call["prompt"])
        assert payload["scope"] == "rectification"
        assert len(payload["chart"]["candidates"]) == 2
        assert payload["knowledge"]["cards"]
        assert "2014 年曾迁居" in model_call["prompt"]
        assert context.history == ["主对话保持独立"] and not context.sent
        assert not plugin.archive.list_charts(scope) and not plugin.chart_cache.entries
        assert usage(plugin, "question") == 2
        await plugin.terminate()

    asyncio.run(run())


def test_explicit_candidate_command_ignores_exhausted_ai_quotas():
    plugin = ZiweiPlugin(Context(), {})
    scope = capture_group(administrator())
    chart = natal()
    for kind, limit in (("natal", 2), ("decade", 3)):
        for _ in range(limit):
            token = plugin.archive.reserve_chart(scope, kind)
            source = chart if kind == "natal" else apply_decade(chart, 1)
            plugin.archive.save_chart(scope, source, token)
    for index in range(6):
        plugin.archive.charge_question(scope, f"previous-question-{index}")
    before = list(
        plugin.archive.db.execute("SELECT * FROM ai_usage ORDER BY kind,token")
    )

    async def run():
        row = await create_session(plugin)
        assert row["candidates"] and not plugin.chart_cache.entries
        assert len(plugin.archive.list_charts(scope)) == 5
        assert (
            list(
                plugin.archive.db.execute("SELECT * FROM ai_usage ORDER BY kind,token")
            )
            == before
        )
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("streaming", [False, True])
def test_failed_answer_capture_cannot_be_closed_as_a_completed_rectification(streaming):
    context = ModelContext()
    plugin = ZiweiPlugin(context, {})

    class BrokenEvent(Event):
        async def send(self, message):
            raise OSError("synthetic delivery failure")

    async def run():
        row = await create_session(plugin)
        sid = row["session_id"]
        question = BrokenEvent(admin=True, text="请比较两个候选")
        result = json.loads(await plugin.ziwei_rectification_context(question, sid))
        assert result["status"] == "ok", result

        async def chunks():
            yield SimpleNamespace(
                chain=[SimpleNamespace(text="尚未成功发送的定盘回答")]
            )

        with pytest.raises(OSError):
            if streaming:
                await question.send_streaming(chunks())
            else:
                await answer(plugin, question)
        final = administrator(text="谢谢，本次结束")
        result = json.loads(await plugin.ziwei_rectification_finish(final, sid, True))
        assert result["status"] == "invalid_input", result
        stored = plugin.archive.rectifications.get(capture_group(question), sid)
        assert stored["capture_error"]
        assert stored["summary_status"] == "none"
        assert not any(turn["kind"] == "answer" for turn in stored["turns"])
        assert not context.calls
        await plugin.terminate()

    asyncio.run(run())


def test_streaming_capture_excludes_unrelated_text_before_rectification_tool():
    plugin = ZiweiPlugin(Context(), {})

    async def run():
        row = await create_session(plugin)
        sid = row["session_id"]
        question = administrator(text="请比较两个时辰")
        request = SimpleNamespace(system_prompt="existing", extra_user_content_parts=[])
        await plugin.reading_request(question, request)

        async def chunks():
            yield SimpleNamespace(chain=[SimpleNamespace(text="工具调用前的无关开场")])
            result = json.loads(await plugin.ziwei_rectification_context(question, sid))
            assert result["status"] == "ok", result
            yield SimpleNamespace(chain=[SimpleNamespace(text="候选对比第一段")])
            yield SimpleNamespace(chain=[SimpleNamespace(text="候选对比第二段")])

        await question.send_streaming(chunks(), True)
        stored = plugin.archive.rectifications.get(capture_group(question), sid)
        assert [turn["kind"] for turn in stored["turns"]] == ["question", "answer"]
        assert stored["turns"][-1]["text"] == "候选对比第一段候选对比第二段"
        assert not stored["capture_error"]
        assert not plugin.archive.active_readings(capture_group(question))
        assert usage(plugin, "question") == 1
        await plugin.terminate()

    asyncio.run(run())


def test_concurrent_contexts_cannot_write_a_second_session_before_binding(monkeypatch):
    from astrbot_plugin_ziwei import rectification_service

    plugin = ZiweiPlugin(Context(), {})
    scope = capture_group(administrator())
    entered = threading.Barrier(2)
    thread_state = threading.local()
    original = rectification_service.build_facts

    def synchronized(*args, **kwargs):
        if not getattr(thread_state, "entered", False):
            thread_state.entered = True
            entered.wait(5)
        return original(*args, **kwargs)

    async def run():
        first = await create_session(plugin)
        await invoke(plugin, DEMO)
        second = plugin.archive.rectifications.get(scope)["id"]
        ids = [first["session_id"], second]
        assert len(set(ids)) == 2
        monkeypatch.setattr(rectification_service, "build_facts", synchronized)
        question = administrator(text="请对比本轮候选")
        results = await asyncio.gather(
            *(plugin.ziwei_rectification_context(question, sid) for sid in ids)
        )
        values = [json.loads(result) for result in results]
        assert sorted(value["status"] for value in values) == ["invalid_input", "ok"]
        for sid, value in zip(ids, values, strict=True):
            stored = plugin.archive.rectifications.get(scope, sid)
            if value["status"] == "ok":
                assert [turn["kind"] for turn in stored["turns"]] == ["question"]
                assert stored["knowledge"]
                assert question._ziwei_rectification_delivery[1] == sid
            else:
                assert not stored["turns"] and not stored["knowledge"]
        assert usage(plugin, "question") == 1
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("other_kind", ["rectification", "ordinary_reading"])
def test_finish_cannot_attach_current_rectification_message_to_another_record(
    other_kind,
):
    plugin = ZiweiPlugin(Context(), {})
    scope = capture_group(administrator())

    async def run():
        first = await create_session(plugin)
        sid = first["session_id"]
        previous = administrator(text="前一份记录的问题")
        if other_kind == "rectification":
            await invoke(plugin, DEMO)
            other_id = plugin.archive.rectifications.get(scope)["id"]
            response = await plugin.ziwei_rectification_context(previous, other_id)
            finish = plugin.ziwei_rectification_finish
            stored = plugin.archive.rectifications.get
        else:
            await plugin.ziwei_paipan(previous, DEMO_BIRTH)
            response = await plugin.ziwei_analysis_context(previous)
            other_id = json.loads(response)["reading_id"]
            finish = plugin.ziwei_finish_reading
            stored = plugin.archive.get_reading
        assert json.loads(response)["status"] == "ok"
        await answer(plugin, previous, "前一份记录已经回答。")
        current = administrator(text="当前定盘的新问题")
        assert (
            json.loads(await plugin.ziwei_rectification_context(current, sid))["status"]
            == "ok"
        )
        result = json.loads(await finish(current, other_id, True))
        assert result["status"] == "invalid_input", result
        assert current._ziwei_rectification_delivery[1] == sid
        await answer(plugin, current, "当前定盘问题的回答。")
        assert [
            turn["kind"]
            for turn in plugin.archive.rectifications.get(scope, sid)["turns"]
        ] == ["question", "answer"]
        assert [turn["kind"] for turn in stored(scope, other_id)["turns"]] == [
            "question",
            "answer",
        ]
        await plugin.terminate()

    asyncio.run(run())


def test_pure_feedback_can_correct_previous_evidence_without_charging_or_new_context():
    plugin = ZiweiPlugin(Context(), {})
    scope = capture_group(administrator())

    async def run():
        row = await create_session(plugin)
        sid = row["session_id"]
        question = administrator(text="请对比候选迁居经历")
        context = json.loads(await plugin.ziwei_rectification_context(question, sid))
        candidate = context["candidates"][0]
        await answer(plugin, question, "候选之间可能存在迁居差异，请核实已发生经历。")
        feedback = administrator(text="虚构反馈：没有发生此前询问的迁居。")
        assert (
            json.loads(await plugin.ziwei_rectification_feedback(feedback, sid))[
                "status"
            ]
            == "recorded"
        )
        before = plugin.archive.rectifications.get(scope, sid)
        seq = next(
            turn["seq"] for turn in before["turns"] if turn["kind"] == "feedback"
        )
        evaluation = {
            candidate["candidate_id"]: {
                "support": [],
                "conflict": ["用户反馈与此前迁居解释不符，撤回该支持线索"],
                "unknown": ["两组候选继续保留"],
                "evidence_sequences": [seq],
                "knowledge_ids": [candidate["knowledge"]["cards"][0]["id"]],
            }
        }
        result = json.loads(
            await plugin.ziwei_rectification_assess(
                feedback, sid, json.dumps(evaluation, ensure_ascii=False)
            )
        )
        assert result["status"] == "saved", result
        await answer(plugin, feedback, "已修正此前解释，继续保留两组候选。")
        after = plugin.archive.rectifications.get(scope, sid)
        assert after["assessment"] == evaluation
        assert after["knowledge"] == before["knowledge"]
        assert [turn["kind"] for turn in after["turns"]] == [
            "question",
            "answer",
            "feedback",
            "answer",
        ]
        assert not after["selected"] and not after["chart_id"]
        assert usage(plugin, "question") == 1
        assert not plugin.chart_cache.entries
        await plugin.terminate()

    asyncio.run(run())


def test_full_day_batched_context_and_summary_fit_real_archive_limits():
    plugin = ZiweiPlugin(Context(), {})
    plugin.rectifications.worker.wake = lambda: None

    async def run():
        row = await create_session(plugin, args="1990-06-15 男")
        sid = row["session_id"]
        ids = [candidate["candidate_id"] for candidate in row["candidates"]]
        assert len(ids) == 14
        question = administrator(text="请分批比较全天所有候选，然后询问区分经历")
        question.message_obj.message_id = "full-day-single-message"
        covered = []
        for start in range(0, len(ids), 2):
            context = json.loads(
                await plugin.ziwei_rectification_context(
                    question, sid, ",".join(ids[start : start + 2]), "general"
                )
            )
            assert context["status"] == "ok", context
            covered.extend(
                candidate["candidate_id"] for candidate in context["candidates"]
            )
        assert covered == ids
        assert usage(plugin, "question") == 1
        context_bytes = plugin.archive.db.execute(
            "SELECT SUM(LENGTH(CAST(context AS BLOB))) "
            "FROM rectification_contexts WHERE session_id=?",
            (sid,),
        ).fetchone()[0]
        assert context_bytes <= 2 * 1024 * 1024
        await answer(plugin, question, "已比较所有候选，资料不足，暂时全部保留。")
        feedback = administrator(text="已了解候选差异，本轮结束，不确认任何候选。")
        finished = json.loads(
            await plugin.ziwei_rectification_finish(feedback, sid, True)
        )
        assert finished["status"] == "queued", finished
        job = plugin.archive.rectifications.claim_job()
        assert job and job["id"] == sid
        prompt = json.dumps(
            {
                "chart": job["chart"],
                "scope": job["scope"],
                "topic": job["topic"],
                "knowledge": json.loads(job["knowledge"]),
                "turns": job["turns"],
            },
            ensure_ascii=False,
        )
        print(f"full_day_context_bytes={context_bytes}; summary_chars={len(prompt)}")
        assert len(prompt) <= 100000
        assert len(job["chart"]["candidates"]) == 14
        plugin.archive.rectifications.release_job(sid)
        assert usage(plugin, "question") == 1
        await plugin.terminate()

    asyncio.run(run())


def test_finish_rejection_does_not_persist_an_unanswered_question_as_feedback():
    plugin = ZiweiPlugin(Context(), {})
    plugin.rectifications.worker.wake = lambda: None
    scope = capture_group(administrator())

    async def run():
        row = await create_session(plugin)
        sid = row["session_id"]
        first = administrator(text="第一轮候选比较问题")
        assert (
            json.loads(await plugin.ziwei_rectification_context(first, sid))["status"]
            == "ok"
        )
        await answer(plugin, first, "第一轮回答已发送。")
        pending = administrator(text="尚未回答的第二轮问题")
        assert (
            json.loads(await plugin.ziwei_rectification_context(pending, sid))["status"]
            == "ok"
        )
        before = plugin.archive.rectifications.get(scope, sid)["turns"]
        result = json.loads(await plugin.ziwei_rectification_finish(pending, sid, True))
        assert result["status"] == "invalid_input", result
        rejected = plugin.archive.rectifications.get(scope, sid)
        assert rejected["turns"] == before
        assert rejected["summary_status"] == "none"
        assert pending._ziwei_rectification_delivery[1] == sid
        await answer(plugin, pending, "第二轮问题已回答。")
        final = administrator(text="两轮问题均已回答，本次结束。")
        result = json.loads(await plugin.ziwei_rectification_finish(final, sid, True))
        assert result["status"] == "queued", result
        queued = plugin.archive.rectifications.get(scope, sid)
        feedback = [turn for turn in queued["turns"] if turn["kind"] == "feedback"]
        assert len(feedback) == 1 and feedback[0]["text"] == final.message_str
        assert usage(plugin, "question") == 2
        await plugin.terminate()

    asyncio.run(run())
