"""Offline event/model doubles: archive isolation and feedback lifecycle."""

import asyncio
import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.analysis_facts import build_facts, feature_tags
from astrbot_plugin_ziwei.archive_store import ArchiveStore
from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request
from astrbot_plugin_ziwei.engine import apply_decade, apply_flow, build_chart
from astrbot_plugin_ziwei.knowledge import KnowledgeBase
from astrbot_plugin_ziwei.summary_worker import SummaryWorker, validate_summary

from .conftest import Event
from .helpers import Context


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
