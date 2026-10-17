"""Offline private candidate archive, quota and feedback lifecycle regression."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.ai_limits import QuotaExceeded
from astrbot_plugin_ziwei.archive_store import ArchiveStore
from astrbot_plugin_ziwei.commands import parse_request
from astrbot_plugin_ziwei.engine import build_chart

from .conftest import Event


@pytest.fixture(scope="module")
def candidates():
    result = []
    for index, hour in enumerate((8, 10), 1):
        request = parse_request(f"1990-06-15 {hour:02d}:30 男")
        result.append(
            {
                "id": f"c{index:02d}",
                "label": f"虚构候选{index}",
                "intervals": [
                    {"start": f"{hour - 1:02d}:00", "end": f"{hour + 1:02d}:00"}
                ],
                "chart": build_chart(request.birth, request.profile),
            }
        )
    return result


def create(archive, candidates, *, event=None, event_id="create"):
    scope = capture_group(event or Event())
    sid = archive.rectifications.create(scope, event_id, {"demo": True}, candidates)
    return scope, sid


def closed_loop(store, scope, sid):
    store.add_turn(
        scope, sid, "q1", "question", "比较两个虚构候选，需要哪些已发生事件？"
    )
    store.add_turn(scope, sid, "q1", "answer", "目前证据不足，请说明相关已发生事件。")
    store.add_turn(scope, sid, "f1", "feedback", "谢谢，目前没有补充事件。")


def test_candidates_never_replace_current_chart_and_selection_is_atomic(
    tmp_path, candidates
):
    path = tmp_path / "archives.sqlite3"
    archive = ArchiveStore(path)
    scope = capture_group(Event())
    original = archive.save_chart(scope, candidates[0]["chart"])
    _, sid = create(archive, candidates)
    assert archive.get_chart(scope)["id"] == original
    assert len(archive.list_charts(scope)) == 1
    assert archive.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 0
    archive.close()
    archive = ArchiveStore(path)
    assert archive.get_chart(scope)["id"] == original
    stored = archive.rectifications.get(scope, sid)
    assert stored["status"] == "active" and not stored["selected"]
    assert stored["candidates"] == candidates
    selected = archive.rectifications.select(scope, sid, "c02")
    assert archive.rectifications.select(scope, sid, "c02") == selected
    assert len(archive.list_charts(scope)) == 2
    chart = archive.get_chart(scope)["chart"]
    assert chart["rectification"] == {
        "session_id": sid,
        "candidate_id": "c02",
        "intervals": candidates[1]["intervals"],
        "representative_only": True,
    }
    assert (
        "rectification"
        not in archive.rectifications.get(scope, sid)["candidates"][1]["chart"]
    )
    with pytest.raises(ValueError, match="已确认其他"):
        archive.rectifications.select(scope, sid, "c01")
    with pytest.raises(ValueError, match="不能取消"):
        archive.rectifications.cancel(scope, sid)


def test_creation_event_replays_and_parallel_confirmation_do_not_duplicate(
    tmp_path, candidates
):
    archive = ArchiveStore(tmp_path / "archives.sqlite3")
    scope, sid = create(archive, candidates)
    assert create(archive, candidates)[1] == sid
    assert archive.rectifications.find_event(scope, "create")["id"] == sid
    assert archive.rectifications.find_event(scope, "missing") is None
    with ThreadPoolExecutor(max_workers=3) as pool:
        selected = list(
            pool.map(
                lambda _: archive.rectifications.select(scope, sid, "c01"), range(3)
            )
        )
    assert len(set(selected)) == 1
    assert len(archive.list_charts(scope)) == 1


@pytest.mark.parametrize(
    "outsider", [Event(user="other"), Event(group="other"), Event(platform="other")]
)
def test_every_session_operation_enforces_owner_isolation(
    tmp_path, candidates, outsider
):
    archive = ArchiveStore(tmp_path / "archives.sqlite3")
    scope, sid = create(archive, candidates)
    other = capture_group(outsider)
    store = archive.rectifications
    assert store.get(other, sid) is None
    assert store.get(other) is None and store.active(other) == []
    assert store.find_event(other, "create") is None
    actions = [
        lambda: store.add_turn(other, sid, "q", "question", "问题"),
        lambda: store.record_context(other, sid, "q", {}),
        lambda: store.save_assessment(other, sid, "q", {}),
        lambda: store.select(other, sid, "c01"),
        lambda: store.cancel(other, sid),
        lambda: store.enqueue(other, sid, "f"),
        lambda: store.retry(other, sid),
    ]
    for action in actions:
        with pytest.raises(ValueError, match="找不到"):
            action()
    store.block_capture(other, sid)
    assert store.get(scope, sid)["capture_error"] == 0
    assert store.get(scope, sid)["turns"] == []


def test_storage_failures_roll_back_selection_and_question_charge(tmp_path, candidates):
    archive = ArchiveStore(tmp_path / "archives.sqlite3")
    scope, sid = create(archive, candidates)
    archive.db.execute(
        "CREATE TRIGGER reject_select BEFORE UPDATE OF selected ON rectifications "
        "BEGIN SELECT RAISE(ABORT,'failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        archive.rectifications.select(scope, sid, "c01")
    assert archive.get_chart(scope) is None
    assert archive.rectifications.get(scope, sid)["selected"] == ""
    archive.db.execute(
        "CREATE TRIGGER reject_turn BEFORE INSERT ON rectification_turns "
        "BEGIN SELECT RAISE(ABORT,'failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        archive.rectifications.add_turn(scope, sid, "q1", "question", "虚构问题")
    assert archive.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 0
    assert archive.rectifications.get(scope, sid)["turns"] == []


def test_shared_question_limit_deduplicates_contexts_and_survives_reload(
    tmp_path, candidates
):
    path = tmp_path / "archives.sqlite3"
    archive = ArchiveStore(path, clock=lambda: 1234)
    scope, sid = create(archive, candidates)
    store = archive.rectifications
    context = {
        "context_key": "c01/general/natal/0",
        "candidates": [
            {
                "candidate_id": "c01",
                "facts": {"life": 3},
                "knowledge": {
                    "cards": [{"id": "demo", "condition": "仅虚构用例"}],
                    "sources": {"demo": {"version": "v1"}},
                },
            }
        ],
    }
    store.add_turn(scope, sid, "q1", "question", "虚构问题")
    for _ in range(2):
        store.add_turn(scope, sid, "q1", "question", "虚构问题")
        store.record_context(scope, sid, "q1", context)
    row = store.get(scope, sid)
    assert len(row["turns"]) == len(row["contexts"]) == 1
    assert row["contexts"][0] == context
    assert archive.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 1
    other_scope, other_sid = create(archive, candidates, event=Event(group="another"))
    for index in range(5):
        store.add_turn(
            other_scope, other_sid, f"q{index}", "question", "另一群虚构问题"
        )
    archive.close()
    archive = ArchiveStore(path, clock=lambda: 1240)
    store = archive.rectifications
    with pytest.raises(QuotaExceeded):
        store.add_turn(scope, sid, "q7", "question", "超过额度的问题")
    store.add_turn(scope, sid, "q1", "answer", "已发送的虚构答复")
    store.add_turn(scope, sid, "f1", "feedback", "谢谢")
    assert store.enqueue(scope, sid, "f1") == "queued"
    assert archive.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 6
    job = store.claim_job()
    assert json.loads(job["knowledge"]) == {
        "cards": [{"id": "demo", "condition": "仅虚构用例"}],
        "sources": [{"id": "demo", "source": {"version": "v1"}}],
    }
    assert job["scope"] == "rectification" and job["chart"]["selected"] == ""
    assert "normalized" not in json.dumps(job["chart"])
    assert store.get(scope, sid)["contexts"] == [context]


def test_feedback_cannot_precede_answer_or_close_an_unanswered_question(
    tmp_path, candidates
):
    archive = ArchiveStore(tmp_path / "archives.sqlite3")
    scope, sid = create(archive, candidates)
    store = archive.rectifications
    with pytest.raises(ValueError, match="尚无"):
        store.add_turn(scope, sid, "f0", "feedback", "谢谢")
    with pytest.raises(ValueError, match="已登记"):
        store.add_turn(scope, sid, "q1", "answer", "未关联问题的回答")
    with pytest.raises(ValueError, match="先登记"):
        store.record_context(scope, sid, "q1", {})
    closed_loop(store, scope, sid)
    with pytest.raises(ValueError, match="同一用户消息"):
        store.add_turn(scope, sid, "q1", "feedback", "伪装后续反馈")
    store.add_turn(scope, sid, "q2", "question", "仍有一个相关追问")
    with pytest.raises(ValueError, match="无未答问题"):
        store.enqueue(scope, sid, "f1")
    store.add_turn(scope, sid, "q2", "answer", "追问回答")
    with pytest.raises(ValueError, match="真实用户反馈"):
        store.enqueue(scope, sid, "f1")
    store.add_turn(scope, sid, "f2", "feedback", "新的反馈")
    store.block_capture(scope, sid)
    with pytest.raises(ValueError, match="采集不完整"):
        store.enqueue(scope, sid, "f2")


def test_queue_restores_after_reload_respects_group_and_preserves_failed_records(
    tmp_path, candidates
):
    path = tmp_path / "archives.sqlite3"
    now = [1000]
    archive = ArchiveStore(path, clock=lambda: now[0])
    scope, sid = create(archive, candidates)
    store = archive.rectifications
    closed_loop(store, scope, sid)
    assert store.enqueue(scope, sid, "f1") == "queued"
    assert store.enqueue(scope, sid, "f1") == "queued"
    store.set_group(scope, False)
    assert store.claim_job() is None
    store.set_group(scope, True)
    assert store.claim_job()["id"] == sid
    archive.close()
    archive = ArchiveStore(path, clock=lambda: now[0])
    store = archive.rectifications
    assert store.get(scope, sid)["summary_status"] == "queued"
    job = store.claim_job()
    store.fail_job(job["id"], "exception containing private prompt data")
    assert store.get(scope, sid)["last_error"] == "SummaryError"
    assert store.claim_job() is None
    now[0] += 60
    job = store.claim_job()
    store.fail_job(job["id"], "TimeoutError")
    assert store.get(scope, sid)["summary_status"] == "failed"
    assert store.get(scope, sid)["candidates"] == candidates
    assert len(store.get(scope, sid)["turns"]) == 3
    assert store.retry(scope, sid) == "queued"
    job = store.claim_job()
    store.release_job(job["id"])
    assert store.get(scope, sid)["summary_status"] == "queued"
    store.claim_job()
    store.finish_job(sid, {"feedback_result": "unverified"})
    assert store.get(scope, sid)["summary_status"] == "archived"
    assert store.get(scope, sid)["summary"] == {"feedback_result": "unverified"}


def test_cancel_during_summary_does_not_publish_late_result(tmp_path, candidates):
    archive = ArchiveStore(tmp_path / "archives.sqlite3")
    scope, sid = create(archive, candidates)
    store = archive.rectifications
    closed_loop(store, scope, sid)
    store.enqueue(scope, sid, "f1")
    store.claim_job()
    assert store.cancel(scope, sid) == "cancelled"
    store.finish_job(sid, {"feedback_result": "unverified"})
    assert store.get(scope, sid)["summary"] is None
    assert store.get(scope, sid)["candidates"] == candidates
    assert store.active(scope) == []
    with pytest.raises(ValueError, match="已取消"):
        store.select(scope, sid, "c01")


def test_v3_upgrade_keeps_existing_chart_quota_and_archive(tmp_path, candidates):
    path = tmp_path / "archives.sqlite3"
    archive = ArchiveStore(path)
    scope = capture_group(Event())
    quota = archive.reserve_chart(scope, "natal")
    chart_id = archive.save_chart(scope, candidates[0]["chart"], quota)
    archive.close()
    with sqlite3.connect(path) as db:
        for table in (
            "rectification_assessments",
            "rectification_contexts",
            "rectification_turns",
            "rectifications",
        ):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=3")
    archive = ArchiveStore(path)
    assert archive.db.execute("PRAGMA user_version").fetchone()[0] == 4
    assert archive.get_chart(scope, chart_id)["chart"] == candidates[0]["chart"]
    assert tuple(
        archive.db.execute("SELECT token,pending FROM ai_usage").fetchone()
    ) == (quota, 0)
    assert archive.rectifications.get(scope) is None
    create(archive, candidates)
    assert archive.get_chart(scope)["id"] == chart_id


def test_invalid_candidates_and_context_overflow_have_no_partial_writes(
    tmp_path, candidates, monkeypatch
):
    from astrbot_plugin_ziwei import rectification_store

    archive = ArchiveStore(tmp_path / "archives.sqlite3")
    scope = capture_group(Event())
    with pytest.raises(ValueError, match="重复"):
        archive.rectifications.create(scope, "bad", {}, [candidates[0], candidates[0]])
    assert archive.rectifications.get(scope) is None
    _, sid = create(archive, candidates)
    store = archive.rectifications
    store.add_turn(scope, sid, "q1", "question", "虚构问题")
    store.record_context(scope, sid, "q1", {"version": "old"})
    with monkeypatch.context() as limited:
        limited.setattr(rectification_store, "MAX_CONTEXT_BYTES", 30)
        with pytest.raises(ValueError, match="归档上限"):
            store.record_context(scope, sid, "q1", {"version": "new"})
    assert store.get(scope, sid)["contexts"] == [{"version": "old"}]
    assessment = {"c01": {"support": [], "conflict": [], "unknown": ["证据不足"]}}
    store.save_assessment(scope, sid, "q1", assessment)
    copied = deepcopy(store.get(scope, sid))
    copied["assessment"]["c01"]["unknown"].append("不能影响原档案")
    assert store.get(scope, sid)["assessment"] == assessment


def test_atomic_context_failure_refunds_question_and_uses_updated_archive_clock(
    tmp_path, candidates
):
    archive = ArchiveStore(tmp_path / "archives.sqlite3", clock=lambda: 10)
    scope, sid = create(archive, candidates)
    store = archive.rectifications
    archive.clock = lambda: 42
    archive.db.execute(
        "CREATE TRIGGER reject_context BEFORE INSERT ON rectification_contexts "
        "BEGIN SELECT RAISE(ABORT,'failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.record_context(scope, sid, "q1", {"facts": "demo"}, question="虚构问题")
    assert store.get(scope, sid)["turns"] == []
    assert archive.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 0
    archive.db.execute("DROP TRIGGER reject_context")
    store.record_context(scope, sid, "q1", {"facts": "demo"}, question="虚构问题")
    assert store.get(scope, sid)["updated"] == 42
    assert archive.db.execute("SELECT created FROM ai_usage").fetchone()[0] == 42
    archive.clock = lambda: 5000
    store.record_context(scope, sid, "q1", {"facts": "demo2"}, question="虚构问题")
    assert archive.db.execute("SELECT created FROM ai_usage").fetchone()[0] == 42
    assert len(store.get(scope, sid)["turns"]) == 1


def test_feedback_assessment_keeps_other_candidates_and_immutable_history(
    tmp_path, candidates
):
    archive = ArchiveStore(tmp_path / "archives.sqlite3")
    scope, sid = create(archive, candidates)
    store = archive.rectifications
    closed_loop(store, scope, sid)
    original = {
        "c01": {"support": ["仅虚构旧判断"], "conflict": [], "unknown": []},
        "c02": {"support": [], "conflict": [], "unknown": ["证据不足"]},
    }
    corrected = {"c01": {"support": [], "conflict": ["反馈指出反例"], "unknown": []}}
    store.save_assessment(scope, sid, "q1", original)
    store.save_assessment(scope, sid, "f1", corrected)
    store.save_assessment(scope, sid, "f1", corrected)
    row = store.get(scope, sid)
    assert row["assessment"] == dict(original, **corrected)
    assert [item["assessment"] for item in row["assessments"]] == [original, corrected]
    assert [item["event_id"] for item in row["assessments"]] == ["q1", "f1"]
    assert archive.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 1
    store.add_turn(scope, sid, "f1", "answer", "已根据实际反馈修正虚构判断")
    store.add_turn(scope, sid, "f1", "feedback", "重复投递原反馈")
    with pytest.raises(ValueError, match="真实用户反馈"):
        store.enqueue(scope, sid, "f1")
    store.add_turn(scope, sid, "f2", "feedback", "新的最终反馈")
    assert store.enqueue(scope, sid, "f2") == "queued"
