"""Offline event/model doubles: archive isolation and feedback lifecycle."""

import asyncio
import copy
import inspect
import json
import sqlite3
from types import SimpleNamespace

import docstring_parser
import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.analysis_facts import build_facts, feature_tags
from astrbot_plugin_ziwei.archive_store import ArchiveStore
from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request
from astrbot_plugin_ziwei.engine import apply_decade, apply_flow, build_chart
from astrbot_plugin_ziwei.knowledge import KnowledgeBase
from astrbot_plugin_ziwei.main import ZiweiPlugin
from astrbot_plugin_ziwei.summary_worker import SummaryWorker, validate_summary

from .conftest import Event
from .test_llm_tools import Context, allow


def natal():
    req = parse_request(DEMO_BIRTH, {})
    return build_chart(req.birth, req.profile)


def summary_for(job, outcome="unverified"):
    return dict(
        question_summary="虚构问题",
        analysis_summary="有条件解释",
        user_feedback="谢谢",
        corrections="尚无证实",
        unverified="全部预测尚未验证",
        feedback_result=outcome,
        evidence_sequences=[t["seq"] for t in job["turns"] if t["kind"] == "feedback"],
    )


class ModelContext(Context):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.history = ["主对话保持独立"]

    async def get_current_chat_provider_id(self, *, umo):
        return "configured-provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        job = json.loads(kwargs["prompt"])
        return SimpleNamespace(completion_text=json.dumps(summary_for(job)))


async def answer(plugin, event, text="盘面有这些关系，具体情形仍待反馈。"):
    event.result = SimpleNamespace(chain=[SimpleNamespace(text=text)])
    await event.send(event.result)


def test_all_tool_docstrings_match_signatures():
    for _, fn in inspect.getmembers(ZiweiPlugin, inspect.isfunction):
        if not hasattr(fn, "tool_metadata"):
            continue
        parsed = docstring_parser.parse(fn.__doc__)
        params = inspect.signature(fn).parameters
        assert {p.arg_name for p in parsed.params} == set(params) - {"self", "event"}
        assert all(
            p.type_name in {"string", "boolean", "number"} for p in parsed.params
        )
        assert all(p.description for p in parsed.params)


def test_facts_use_real_palaces_and_remove_unrequested_time_layers():
    chart = natal()
    dated = apply_flow(chart, year=2026)
    original = copy.deepcopy(dated)
    facts = build_facts(dated, "natal", "career")
    assert facts["flow"] is None and facts["selected_life"] == 3
    assert dated == original
    life = next(p for p in facts["palaces"] if p["branch"] == 3)
    assert life["opposite"] == 9 and life["trines"] == [7, 11]
    assert life["qi_number_branch"] == 7
    assert life["layer_names"] == {"本命": "命宫"}
    # Fixed 戊寅宫干四化, independent of tested relation builder.
    flights = [f for f in facts["palace_stem_flights"] if f["source_branch"] == 3]
    assert [(f["star"], f["hua"]) for f in flights] == [
        ("贪狼", "禄"),
        ("太阴", "权"),
        ("右弼", "科"),
        ("天机", "忌"),
    ]
    assert all("流年" not in s["hua"] for p in facts["palaces"] for s in p["stars"])
    req = parse_request(DEMO_BIRTH + " 流盘=2026-10-06 流时=10:00", {})
    dated = apply_flow(chart, target=req.flow_target)
    facts = build_facts(dated, "annual", "career")
    assert set(facts["flow"]["layers"]) == {"大限", "流年"}
    assert "流月" not in json.dumps(facts, ensure_ascii=False)
    assert "流月" in dated["flow"]["layers"]
    assert (
        build_facts(apply_decade(chart, 1), "decade", "general")["flow"]["year"] is None
    )


@pytest.mark.parametrize("scope", ["decade", "annual", "monthly"])
def test_missing_target_refuses_to_invent_scope(scope):
    with pytest.raises(ValueError):
        build_facts(natal(), scope, "general")


def test_literature_scopes_sources_and_pagination():
    kb = KnowledgeBase()
    assert len(kb.cards) >= 54
    assert len({c["id"] for c in kb.cards}) == len(kb.cards)
    for card in kb.cards:
        assert card["locator"] and card["conditions"] and card["exceptions"]
        assert len(kb.sources[card["source"]]["sha256"]) == 64
    facts = build_facts(apply_flow(natal(), year=2026), "annual", "general")
    first = kb.query(facts, limit=3)
    second = kb.query(facts, limit=3, offset=first["next_offset"])
    assert not {c["id"] for c in first["cards"]} & {c["id"] for c in second["cards"]}
    assert any(c["id"] == "qs-flow-stars" for c in first["unavailable_rules"])
    assert all("annual" in c["scopes"] for c in first["cards"])
    first["cards"][0]["summary"] = "altered"
    assert kb.query(facts)["cards"][0]["summary"] != "altered"


def test_plain_chart_never_creates_analysis_or_calls_model():
    context = ModelContext()
    plugin = ZiweiPlugin(context, {})

    async def run():
        await allow(plugin)
        event = Event(text="请排盘")
        result = json.loads(await plugin.ziwei_paipan(event, DEMO_BIRTH))
        assert result["archive_status"] == "saved"
        await answer(plugin, event, "排盘已完成")
        assert not plugin.archive.active_readings(capture_group(event))
        assert not context.calls
        assert len(plugin.archive.list_charts(capture_group(event))) == 1
        await plugin.terminate()

    asyncio.run(run())


def test_closed_loop_uses_only_its_chart_turns_and_separate_model_context():
    context = ModelContext()
    plugin = ZiweiPlugin(context, {})

    async def run():
        await allow(plugin)
        event = Event(text="请分析事业")
        chart = json.loads(await plugin.ziwei_paipan(event, DEMO_BIRTH))
        result = json.loads(await plugin.ziwei_analysis_context(event, topic="career"))
        assert result["status"] == "ok", result
        reading_id = result["reading_id"]
        scope = capture_group(event)
        # Model cannot finish its own question as if it were a user's feedback.
        early = json.loads(await plugin.ziwei_finish_reading(event, reading_id, True))
        assert early["status"] == "invalid_input"
        await answer(plugin, event, "第一段")
        await answer(plugin, event, "第二段")
        row = plugin.archive.get_reading(scope, reading_id)
        assert row["turns"][-1]["text"] == "第一段\n第二段"
        same_event = json.loads(
            await plugin.ziwei_finish_reading(event, reading_id, True)
        )
        assert same_event["status"] == "invalid_input"
        await answer(plugin, Event(user="other", text="其他人的私事"), "无关回答")
        feedback = Event(text="实际并没有升职，这部分不符合，目前问题回答完了")
        finish = json.loads(
            await plugin.ziwei_finish_reading(feedback, reading_id, True)
        )
        assert finish["status"] == "queued" and not context.calls
        for _ in range(100):
            if plugin.archive.get_reading(scope, reading_id)["status"] == "archived":
                break
            await asyncio.sleep(0.01)
        row = plugin.archive.get_reading(scope, reading_id)
        assert row["status"] == "archived"
        call = context.calls[0]
        assert call["contexts"] == [] and "tools" not in call
        assert call["chat_provider_id"] == "configured-provider"
        assert "其他人的私事" not in call["prompt"]
        assert "实际并没有升职" in call["prompt"]
        assert json.loads(call["prompt"])["knowledge"]["version"]
        assert context.history == ["主对话保持独立"] and not context.sent
        history = json.loads(
            await plugin.ziwei_reading_history(Event(), chart["chart_id"])
        )
        assert len(history["records"]) == 1
        assert (
            json.loads(await plugin.ziwei_finish_reading(feedback, reading_id, True))[
                "status"
            ]
            == "archived"
        )
        assert len(context.calls) == 1
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize(
    "outsider",
    [
        Event(user="other"),
        Event(group="other"),
        Event(platform="other"),
        Event(group="", admin=True),
    ],
)
def test_reading_tools_never_accept_another_owners_id(outsider):
    plugin = ZiweiPlugin(ModelContext(), {})

    async def run():
        await allow(plugin)
        event = Event(text="分析本命")
        chart = json.loads(await plugin.ziwei_paipan(event, DEMO_BIRTH))
        reading = json.loads(await plugin.ziwei_analysis_context(event))["reading_id"]
        if outsider.group:
            await allow(plugin, outsider)
        assert (
            json.loads(
                await plugin.ziwei_analysis_context(outsider, chart["chart_id"])
            )["status"]
            != "ok"
        )
        assert (
            json.loads(await plugin.ziwei_continue_reading(outsider, reading))["status"]
            != "ok"
        )
        assert (
            json.loads(await plugin.ziwei_query_knowledge(outsider, reading))["status"]
            != "ok"
        )
        assert (
            json.loads(await plugin.ziwei_reading_history(outsider, chart["chart_id"]))[
                "status"
            ]
            != "ok"
        )
        assert (
            json.loads(await plugin.ziwei_reference_cases(outsider, reading))["status"]
            != "available"
        )
        assert json.loads(await plugin.ziwei_finish_reading(outsider, reading, True))[
            "status"
        ] not in {"queued", "archived"}
        await plugin.terminate()

    asyncio.run(run())


def queued(store, event, chart=None):
    scope = capture_group(event)
    chart = chart or natal()
    cid = store.save_chart(scope, chart)
    facts = build_facts(chart, "natal", "career")
    rid = store.start(
        scope,
        cid,
        "natal",
        "career",
        "q",
        "虚构问题",
        KnowledgeBase().query(facts),
        feature_tags(facts),
    )
    store.add_turn(scope, rid, "q", "answer", "虚构回答")
    store.add_turn(scope, rid, "f", "feedback", "虚构反馈")
    store.enqueue(scope, rid, "f", "")
    return scope, rid, facts


def test_archive_recovery_version_guard_and_no_raw_shared_prose(tmp_path):
    path = tmp_path / "cases.sqlite3"
    store = ArchiveStore(path)
    chart = natal()
    for i, outcome in enumerate(["supported", "contradicted", "unverified"]):
        scope, rid, facts = queued(store, Event(user=f"fiction-{i}"), chart)
        job = store.claim_job()
        assert job["id"] == rid
        data = summary_for(job, outcome)
        data["analysis_summary"] = "NEVER_RETURN_PRIVATE_TEXT phone 13800138000"
        store.finish_job(rid, data)
    requester = capture_group(Event(user="requester"))
    result = store.shared_reference(requester, "career", "natal", feature_tags(facts))
    assert result["status"] == "available" and len(result["cases"]) == 3
    encoded = json.dumps(result)
    assert all(
        s not in encoded
        for s in ("NEVER_RETURN", "13800138000", "fiction", "chart_id", "owner", "1990")
    )
    assert result["feedback_distribution"]["supported"] == 1
    assert (
        store.shared_reference(scope, "career", "natal", feature_tags(facts))["cases"]
        == []
    )
    scope, rid, _ = queued(store, Event())
    assert store.claim_job()["id"] == rid
    store.close()
    restored = ArchiveStore(path)
    assert restored.get_reading(scope, rid)["status"] == "queued"
    restored.set_group(scope, False)
    assert restored.claim_job() is None
    assert (
        restored.shared_reference(requester, "career", "natal", feature_tags(facts))[
            "cases"
        ]
        == []
    )
    restored.close()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="拒绝降级"):
        ArchiveStore(path)


def test_summary_validation_and_failures_are_bounded_without_losing_turns(tmp_path):
    now = [0]
    store = ArchiveStore(tmp_path / "queue.sqlite3", clock=lambda: now[0])
    scope, rid, _ = queued(store, Event())

    class Broken(ModelContext):
        async def llm_generate(self, **kwargs):
            return SimpleNamespace(completion_text='{"secret":"do not log"}')

    async def allowed(scope):
        return None

    async def run():
        worker = SummaryWorker(store, Broken(), allowed)
        for _ in range(3):
            job = store.claim_job()
            bad = summary_for(job)
            bad["evidence_sequences"] = [job["turns"][0]["seq"]]
            with pytest.raises(ValueError, match="evidence"):
                validate_summary(json.dumps(bad), job)
            await worker.process(job)
            now[0] += 61
        row = store.get_reading(scope, rid)
        assert row["status"] == "failed" and row["attempts"] == 3
        assert row["last_error"] == "ValueError" and len(row["turns"]) == 3
        assert store.claim_job() is None

    asyncio.run(run())


def test_summary_permission_revoked_while_model_running(tmp_path):
    store = ArchiveStore(tmp_path / "queue.sqlite3")
    scope, rid, _ = queued(store, Event())
    allowed = [True]

    async def access(scope):
        return None if allowed[0] else "closed"

    class Closing(ModelContext):
        async def llm_generate(self, **kwargs):
            allowed[0] = False
            return await super().llm_generate(**kwargs)

    asyncio.run(SummaryWorker(store, Closing(), access).process(store.claim_job()))
    row = store.get_reading(scope, rid)
    assert row["status"] == "queued" and row["summary"] is None
    assert store.claim_job() is None


def test_stream_capture_installed_before_tool_and_no_plain_callback_needed():
    plugin = ZiweiPlugin(ModelContext(), {})

    async def run():
        await allow(plugin)
        event = Event(text="分析我的事业")
        await plugin.ziwei_paipan(event, DEMO_BIRTH)
        request = SimpleNamespace(system_prompt="existing", extra_user_content_parts=[])
        await plugin.reading_request(event, request)
        assert "默认不分析" in request.system_prompt
        ids = []

        async def stream():
            yield SimpleNamespace(chain=[SimpleNamespace(text="工具前的无关开场")])
            context = json.loads(
                await plugin.ziwei_analysis_context(event, topic="career")
            )
            ids.append(context["reading_id"])
            yield SimpleNamespace(chain=[SimpleNamespace(text="实际分析第一段")])
            yield SimpleNamespace(chain=[SimpleNamespace(text="第二段")])

        await event.send_streaming(stream(), True)
        row = plugin.archive.get_reading(capture_group(event), ids[0])
        assert row["turns"][-1]["text"] == "实际分析第一段第二段"
        assert len(row["turns"]) == 2
        request = SimpleNamespace(system_prompt="existing", extra_user_content_parts=[])
        await plugin.reading_request(Event(text="继续"), request)
        assert ids[0] not in request.system_prompt
        assert request.extra_user_content_parts[0].temporary
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("streaming", [False, True])
def test_send_failure_does_not_close_an_incomplete_answer(streaming):
    plugin = ZiweiPlugin(ModelContext(), {})

    class BrokenEvent(Event):
        async def send(self, message):
            raise OSError("fictional platform failed")

    async def run():
        await allow(plugin)
        event = BrokenEvent(text="分析事业")
        await plugin.ziwei_paipan(event, DEMO_BIRTH)
        rid = json.loads(await plugin.ziwei_analysis_context(event))["reading_id"]

        async def stream():
            yield SimpleNamespace(chain=[SimpleNamespace(text="未发出的分析")])

        with pytest.raises(OSError):
            if streaming:
                await event.send_streaming(stream())
            else:
                await answer(plugin, event)
        row = plugin.archive.get_reading(capture_group(event), rid)
        assert row["capture_error"] and len(row["turns"]) == 1
        result = json.loads(
            await plugin.ziwei_finish_reading(Event(text="谢谢"), rid, True)
        )
        assert result["status"] == "invalid_input"
        assert not plugin.context.calls
        await plugin.terminate()

    asyncio.run(run())


def test_one_reply_cannot_mix_two_chart_scopes_and_query_is_paginated():
    plugin = ZiweiPlugin(ModelContext(), {})

    async def run():
        await allow(plugin)
        event = Event(text="分析命盘")
        await plugin.ziwei_paipan(event, DEMO_BIRTH)
        first = json.loads(await plugin.ziwei_analysis_context(event))
        second = json.loads(await plugin.ziwei_analysis_context(event, topic="career"))
        assert second["status"] == "invalid_input"
        assert len(plugin.archive.active_readings(capture_group(event))) == 1
        result = json.loads(
            await plugin.ziwei_query_knowledge(event, first["reading_id"], 0)
        )
        assert result["status"] == "ok" and result["knowledge"]["cards"]
        assert (
            json.loads(
                await plugin.ziwei_query_knowledge(event, first["reading_id"], -1)
            )["status"]
            == "invalid_input"
        )
        for _ in range(11):
            async for _reply in plugin.ziwei(Event(), DEMO_BIRTH + " 文字"):
                pass
        page1 = json.loads(await plugin.ziwei_list_archives(Event()))
        page2 = json.loads(
            await plugin.ziwei_list_archives(Event(), page1["next_offset"])
        )
        assert len(page1["charts"]) == 10 and len(page2["charts"]) == 2
        assert not {c["chart_id"] for c in page1["charts"]} & {
            c["chart_id"] for c in page2["charts"]
        }
        await plugin.terminate()

    asyncio.run(run())


def test_summary_cancellation_returns_job_to_durable_queue(tmp_path):
    store = ArchiveStore(tmp_path / "cancel.sqlite3")
    scope, rid, _ = queued(store, Event())

    async def run():
        entered = asyncio.Event()

        class Slow(ModelContext):
            async def llm_generate(self, **kwargs):
                entered.set()
                await asyncio.Event().wait()

        async def access(scope):
            return None

        worker = SummaryWorker(store, Slow(), access)
        worker.wake()
        await asyncio.wait_for(entered.wait(), 1)
        await worker.stop()
        assert store.get_reading(scope, rid)["status"] == "queued"
        assert len(store.get_reading(scope, rid)["turns"]) == 3

    asyncio.run(run())
