"""Private candidate archives and a durable rectification feedback queue."""

import hashlib
import json
import re
import uuid
from copy import deepcopy

from .ai_limits import question_token, take
from .llm_data import owner_key

MAX_CANDIDATES = 32
MAX_CONTEXT_BYTES = 2 * 1024 * 1024


def encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def owner_id(scope):
    return hashlib.sha256(encode(owner_key(scope)).encode()).hexdigest()


def bounded_json(value, maximum, label):
    try:
        text = encode(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label}格式无效") from exc
    if len(text.encode("utf-8")) > maximum:
        raise ValueError(f"{label}超过归档长度限制")
    return text


def identifier(value, *, optional=False):
    if not isinstance(value, str) or len(value) > 128 or (not value and not optional):
        raise ValueError("定盘记录标识无效")
    return value


class RectificationStore:
    """Share ArchiveStore's connection and transaction; never own another cache."""

    def __init__(self, archive):
        self.archive = archive
        self.db, self.lock = archive.db, archive.lock
        self.transaction = archive.transaction
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS rectifications (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, group_key TEXT NOT NULL,
                event_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                created REAL NOT NULL, updated REAL NOT NULL, metadata TEXT NOT NULL,
                candidates TEXT NOT NULL, selected TEXT NOT NULL DEFAULT '',
                chart_id TEXT NOT NULL DEFAULT '', assessment TEXT,
                assessment_event TEXT NOT NULL DEFAULT '', route TEXT NOT NULL,
                summary_status TEXT NOT NULL DEFAULT 'none', summary TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                last_error TEXT, capture_error INTEGER NOT NULL DEFAULT 0,
                UNIQUE(owner,event_id));
            CREATE INDEX IF NOT EXISTS rectifications_owner
                ON rectifications(owner,created);
            CREATE TABLE IF NOT EXISTS rectification_turns (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES rectifications(id),
                event_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
                created REAL NOT NULL, UNIQUE(session_id,event_id,kind));
            CREATE TABLE IF NOT EXISTS rectification_contexts (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES rectifications(id),
                event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                context TEXT NOT NULL, created REAL NOT NULL,
                UNIQUE(session_id,fingerprint));
            CREATE TABLE IF NOT EXISTS rectification_assessments (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES rectifications(id),
                event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                assessment TEXT NOT NULL, created REAL NOT NULL,
                UNIQUE(session_id,event_id,fingerprint));
        """)
        with self.transaction() as db:
            db.execute(
                "UPDATE rectifications SET summary_status='queued' "
                "WHERE summary_status='running'"
            )
            db.execute("PRAGMA user_version=4")

    @property
    def clock(self):
        return self.archive.clock

    def create(self, scope, event_id, request_metadata, candidates):
        identifier(event_id)
        owner = owner_id(scope)
        if not isinstance(request_metadata, dict):
            raise ValueError("定盘请求资料须为结构化对象")
        metadata = bounded_json(request_metadata, 32768, "定盘请求资料")
        if (
            not isinstance(candidates, list)
            or not 1 <= len(candidates) <= MAX_CANDIDATES
        ):
            raise ValueError(f"定盘候选须为1至{MAX_CANDIDATES}项")
        ids = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("定盘候选格式无效")
            cid = identifier(candidate.get("id"))
            if cid in ids:
                raise ValueError("定盘候选标识重复")
            ids.add(cid)
            if (
                not isinstance(candidate.get("label"), str)
                or not candidate["label"].strip()
                or len(candidate["label"]) > 512
                or not isinstance(candidate.get("intervals"), list)
                or not isinstance(candidate.get("chart"), dict)
            ):
                raise ValueError("定盘候选须含标签、时间区间及真实命盘")
        encoded = bounded_json(candidates, 8 * 1024 * 1024, "定盘候选")
        route = encode(
            {
                "platform_id": scope.platform_id,
                "platform_name": scope.platform_name,
                "group_id": scope.group_id,
                "user_id": scope.user_id,
                "umo": scope.umo,
            }
        )
        with self.transaction() as db:
            previous = db.execute(
                "SELECT id FROM rectifications WHERE owner=? AND event_id=?",
                (owner, event_id),
            ).fetchone()
            if previous:
                return previous["id"]
            sid, now = uuid.uuid4().hex, self.clock()
            db.execute(
                "INSERT INTO rectifications "
                "(id,owner,group_key,event_id,created,updated,metadata,candidates,route)"
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, owner, scope.key, event_id, now, now, metadata, encoded, route),
            )
            # Authorization is checked by the event-bound service before this call.
            db.execute("INSERT OR REPLACE INTO groups_state VALUES (?,1)", (scope.key,))
        return sid

    def get(self, scope, session_id=""):
        identifier(session_id, optional=True)
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM rectifications WHERE owner=? AND (?='' OR id=?) "
                "ORDER BY created DESC,rowid DESC LIMIT 1",
                (owner_id(scope), session_id, session_id),
            ).fetchone()
            if row is None:
                return None
            turns = self.db.execute(
                "SELECT seq,event_id,kind,text FROM rectification_turns "
                "WHERE session_id=? ORDER BY seq",
                (row["id"],),
            ).fetchall()
            contexts = self.db.execute(
                "SELECT seq,event_id,context FROM rectification_contexts "
                "WHERE session_id=? ORDER BY seq",
                (row["id"],),
            ).fetchall()
            assessments = self.db.execute(
                "SELECT seq,event_id,assessment FROM rectification_assessments "
                "WHERE session_id=? ORDER BY seq",
                (row["id"],),
            ).fetchall()
        result = dict(row)
        for key in ("metadata", "candidates", "assessment", "summary"):
            result[key] = json.loads(result[key]) if result[key] else None
        result["turns"] = [dict(turn) for turn in turns]
        result["knowledge"] = [
            dict(item, context=json.loads(item["context"])) for item in contexts
        ]
        result["contexts"] = [item["context"] for item in result["knowledge"]]
        result["assessments"] = [
            dict(item, assessment=json.loads(item["assessment"]))
            for item in assessments
        ]
        return result

    def find_event(self, scope, event_id):
        identifier(event_id)
        with self.lock:
            row = self.db.execute(
                "SELECT id FROM rectifications WHERE owner=? AND event_id=?",
                (owner_id(scope), event_id),
            ).fetchone()
            return self.get(scope, row["id"]) if row else None

    def _required(self, scope, session_id, *, editable=False):
        row = self.get(scope, identifier(session_id))
        if row is None:
            raise ValueError("找不到本人在本群的定盘记录")
        if editable and (
            row["status"] == "cancelled" or row["summary_status"] != "none"
        ):
            raise ValueError("定盘记录已结束或正在总结，不能继续修改")
        return row

    def active(self, scope):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,status,selected,chart_id,summary_status,created "
                "FROM rectifications WHERE owner=? AND status!='cancelled' "
                "AND summary_status!='archived' "
                "ORDER BY created DESC,rowid DESC LIMIT 5",
                (owner_id(scope),),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_turn(self, scope, session_id, event_id, kind, text):
        with self.transaction() as db:
            self._add_turn(db, scope, session_id, event_id, kind, text)

    def _add_turn(self, db, scope, session_id, event_id, kind, text):
        identifier(event_id)
        if kind not in {"question", "feedback", "answer"}:
            raise ValueError("未知定盘消息类型")
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError("本轮文本为空或超过12000字，未加入定盘档案")
        with self.lock:
            row = self._required(scope, session_id, editable=True)
            old = next(
                (
                    turn
                    for turn in row["turns"]
                    if turn["event_id"] == event_id and turn["kind"] == kind
                ),
                None,
            )
            if old and kind != "answer":
                return
            if kind == "feedback":
                answers = [turn for turn in row["turns"] if turn["kind"] == "answer"]
                if not answers:
                    raise ValueError("尚无已发送的分析回答，不能记录反馈")
                if any(turn["event_id"] == event_id for turn in answers):
                    raise ValueError("同一用户消息不能作为其后回答的反馈")
            if kind == "answer" and not any(
                turn["kind"] in {"question", "feedback"}
                and turn["event_id"] == event_id
                for turn in row["turns"]
            ):
                raise ValueError("回答须对应本会话已登记的真实问题或反馈")
            if old:
                if kind == "answer" and text != old["text"]:
                    combined = old["text"] + "\n" + text
                    if len(combined) > 12000:
                        raise ValueError("回答超过归档长度限制")
                    db.execute(
                        "UPDATE rectification_turns SET text=? WHERE seq=?",
                        (combined, old["seq"]),
                    )
                return
            if len(row["turns"]) >= 60:
                raise ValueError("本次定盘已达60条记录，请结束后开启新定盘")
            if kind == "question":
                take(
                    db, scope, "question", question_token(scope, event_id), self.clock()
                )
            db.execute(
                "INSERT INTO rectification_turns(session_id,event_id,kind,text,created)"
                "VALUES (?,?,?,?,?)",
                (session_id, event_id, kind, text, self.clock()),
            )
            db.execute(
                "UPDATE rectifications SET updated=? WHERE id=?",
                (self.clock(), session_id),
            )

    def record_context(self, scope, session_id, event_id, context, *, question=None):
        identifier(event_id)
        if not isinstance(context, dict):
            raise ValueError("定盘上下文须为结构化对象")
        text = bounded_json(context, MAX_CONTEXT_BYTES, "定盘上下文")
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        with self.transaction() as db:
            if question is not None:
                self._add_turn(db, scope, session_id, event_id, "question", question)
            row = self._required(scope, session_id, editable=True)
            if not any(
                turn["kind"] == "question" and turn["event_id"] == event_id
                for turn in row["turns"]
            ):
                raise ValueError("须先登记本轮真实问题才能查阅定盘资料")
            if db.execute(
                "SELECT 1 FROM rectification_contexts "
                "WHERE session_id=? AND fingerprint=?",
                (session_id, fingerprint),
            ).fetchone():
                return
            size = db.execute(
                "SELECT COALESCE(SUM(LENGTH(CAST(context AS BLOB))),0),COUNT(*) "
                "FROM rectification_contexts WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if size[0] + len(text.encode()) > MAX_CONTEXT_BYTES or size[1] >= 120:
                raise ValueError("本次定盘资料已达归档上限，请缩小范围或另建定盘")
            db.execute(
                "INSERT INTO rectification_contexts "
                "(session_id,event_id,fingerprint,context,created) VALUES (?,?,?,?,?)",
                (session_id, event_id, fingerprint, text, self.clock()),
            )

    def save_assessment(self, scope, session_id, event_id, assessment):
        identifier(event_id)
        if not isinstance(assessment, dict):
            raise ValueError("定盘比较结果须为结构化对象")
        encoded = bounded_json(assessment, 32768, "定盘比较结果")
        with self.transaction() as db:
            row = self._required(scope, session_id, editable=True)
            if not any(
                turn["kind"] in {"question", "feedback"}
                and turn["event_id"] == event_id
                for turn in row["turns"]
            ):
                raise ValueError("比较结果须对应本会话已登记的真实问题或反馈")
            fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
            if db.execute(
                "SELECT 1 FROM rectification_assessments "
                "WHERE session_id=? AND event_id=? AND fingerprint=?",
                (session_id, event_id, fingerprint),
            ).fetchone():
                return
            size = db.execute(
                "SELECT COALESCE(SUM(LENGTH(CAST(assessment AS BLOB))),0),COUNT(*) "
                "FROM rectification_assessments WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if size[0] + len(encoded.encode()) > MAX_CONTEXT_BYTES or size[1] >= 120:
                raise ValueError("本次定盘比较已达归档上限，请另建定盘")
            merged = dict(row["assessment"] or {})
            merged.update(assessment)
            current = bounded_json(merged, MAX_CONTEXT_BYTES, "当前定盘比较")
            db.execute(
                "INSERT INTO rectification_assessments "
                "(session_id,event_id,fingerprint,assessment,created) "
                "VALUES (?,?,?,?,?)",
                (session_id, event_id, fingerprint, encoded, self.clock()),
            )
            db.execute(
                "UPDATE rectifications SET assessment=?,assessment_event=?,updated=? "
                "WHERE id=?",
                (current, event_id, self.clock(), session_id),
            )

    def select(self, scope, session_id, candidate_id):
        identifier(candidate_id)
        with self.transaction() as db:
            row = self._required(scope, session_id)
            if row["status"] == "cancelled":
                raise ValueError("定盘记录已取消")
            if row["selected"]:
                if row["selected"] != candidate_id:
                    raise ValueError("已确认其他候选，请另建定盘，不覆盖已确认命盘")
                return row["chart_id"]
            candidate = next(
                (item for item in row["candidates"] if item["id"] == candidate_id), None
            )
            if candidate is None:
                raise ValueError("候选不属于本次定盘")
            chart = deepcopy(candidate["chart"])
            chart["rectification"] = {
                "session_id": session_id,
                "candidate_id": candidate_id,
                "intervals": deepcopy(candidate["intervals"]),
                "representative_only": True,
            }
            chart_id = self.archive._insert_chart(db, scope, chart)
            db.execute(
                "UPDATE rectifications SET selected=?,chart_id=?,status='selected',"
                "updated=? WHERE id=?",
                (candidate_id, chart_id, self.clock(), session_id),
            )
        return chart_id

    def cancel(self, scope, session_id):
        with self.transaction() as db:
            row = self._required(scope, session_id)
            if row["status"] == "cancelled":
                return "cancelled"
            if row["selected"]:
                raise ValueError("已确认的定盘不能取消，正式命盘与原始记录将保留")
            db.execute(
                "UPDATE rectifications SET status='cancelled',updated=? WHERE id=?",
                (self.clock(), session_id),
            )
        return "cancelled"

    def block_capture(self, scope, session_id):
        with self.transaction() as db:
            db.execute(
                "UPDATE rectifications SET capture_error=1 WHERE id=? AND owner=? "
                "AND summary_status='none'",
                (session_id, owner_id(scope)),
            )

    def enqueue(self, scope, session_id, feedback_event, *, feedback_text=None):
        identifier(feedback_event)
        with self.transaction() as db:
            row = self._required(scope, session_id)
            if row["status"] == "cancelled":
                raise ValueError("定盘记录已取消")
            if row["capture_error"]:
                raise ValueError("回答发送或采集不完整，不能确认闭环")
            if row["summary_status"] in {"queued", "running", "archived"}:
                return row["summary_status"]
            if row["summary_status"] == "failed":
                raise ValueError("总结失败，请重试原任务")
            if feedback_text is not None:
                self._add_turn(
                    db, scope, session_id, feedback_event, "feedback", feedback_text
                )
                row = self._required(scope, session_id)
            feedback = [
                turn
                for turn in row["turns"]
                if turn["kind"] == "feedback" and turn["event_id"] == feedback_event
            ]
            answers = [turn for turn in row["turns"] if turn["kind"] == "answer"]
            questions = [turn for turn in row["turns"] if turn["kind"] == "question"]
            if (
                not feedback
                or not answers
                or feedback[-1]["seq"] <= answers[-1]["seq"]
                or any(question["seq"] > answers[-1]["seq"] for question in questions)
            ):
                raise ValueError("须先有已发送的回答及其后的真实用户反馈，且无未答问题")
            db.execute(
                "UPDATE rectifications SET summary_status='queued',attempts=0,"
                "retry_at=0,updated=? WHERE id=?",
                (self.clock(), session_id),
            )
        return "queued"

    def retry(self, scope, session_id):
        with self.transaction() as db:
            row = self._required(scope, session_id)
            if row["status"] == "cancelled" or row["summary_status"] != "failed":
                raise ValueError("只有本人未取消且总结失败的定盘可以重试")
            db.execute(
                "UPDATE rectifications SET summary_status='queued',attempts=0,"
                "retry_at=0,updated=? WHERE id=?",
                (self.clock(), session_id),
            )
        return "queued"

    def set_group(self, scope, enabled):
        self.archive.set_group(scope, enabled)

    @staticmethod
    def _summary_candidates(candidates):
        result = []
        for candidate in candidates:
            chart = candidate["chart"]
            result.append(
                {
                    "id": candidate["id"],
                    "label": candidate["label"],
                    "intervals": candidate["intervals"],
                    "life": chart.get("life"),
                    "body": chart.get("body"),
                    "bureau": chart.get("bureau"),
                    "palaces": [
                        {
                            "name": palace.get("name"),
                            "branch": palace.get("branch"),
                            "stars": [
                                {
                                    "name": star.get("name"),
                                    "hua": star.get("hua"),
                                    "secondary": star.get("secondary", False),
                                }
                                for star in palace.get("stars", [])
                                if star.get("group") == "major" or star.get("hua")
                            ],
                        }
                        for palace in chart.get("palaces", [])
                    ],
                }
            )
        return result

    @staticmethod
    def _summary_knowledge(contexts):
        # Archive full contexts separately; this derived view only deduplicates
        # exact cards and sources, retaining different versions of the same ID.
        cards, sources = {}, {}

        def visit(value):
            if isinstance(value, dict):
                found = value.get("cards")
                if isinstance(found, list):
                    for card in found:
                        if isinstance(card, dict):
                            cards[encode(card)] = card
                found = value.get("sources")
                if isinstance(found, dict):
                    for key, source in found.items():
                        sources[encode([key, source])] = {"id": key, "source": source}
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        for context in contexts:
            visit(context)
        return {"cards": list(cards.values()), "sources": list(sources.values())}

    def claim_job(self):
        with self.transaction() as db:
            row = db.execute(
                "SELECT r.* FROM rectifications r JOIN groups_state g "
                "ON g.group_key=r.group_key WHERE r.summary_status='queued' "
                "AND r.status!='cancelled' AND r.retry_at<=? AND g.enabled=1 "
                "ORDER BY r.created,r.rowid LIMIT 1",
                (self.clock(),),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE rectifications SET summary_status='running',attempts=attempts+1"
                " WHERE id=?",
                (row["id"],),
            )
            turns = db.execute(
                "SELECT seq,kind,text FROM rectification_turns "
                "WHERE session_id=? ORDER BY seq",
                (row["id"],),
            ).fetchall()
            contexts = db.execute(
                "SELECT context FROM rectification_contexts "
                "WHERE session_id=? ORDER BY seq",
                (row["id"],),
            ).fetchall()
            candidates = self._summary_candidates(json.loads(row["candidates"]))
            knowledge = self._summary_knowledge(
                [json.loads(context["context"]) for context in contexts]
            )
        return dict(
            row,
            scope="rectification",
            topic="general",
            provider="",
            turns=[dict(turn) for turn in turns],
            knowledge=encode(knowledge),
            chart={
                "candidates": candidates,
                "selected": row["selected"],
                "assessment": json.loads(row["assessment"])
                if row["assessment"]
                else None,
            },
        )

    def release_job(self, session_id):
        with self.transaction() as db:
            db.execute(
                "UPDATE rectifications SET summary_status='queued' "
                "WHERE id=? AND summary_status='running' AND status!='cancelled'",
                (session_id,),
            )

    def finish_job(self, session_id, summary):
        encoded = bounded_json(summary, 32768, "定盘总结")
        with self.transaction() as db:
            db.execute(
                "UPDATE rectifications SET summary_status='archived',summary=?,"
                "last_error=NULL,updated=? WHERE id=? "
                "AND summary_status='running' AND status!='cancelled'",
                (encoded, self.clock(), session_id),
            )

    def fail_job(self, session_id, error):
        # Only an exception class may cross this persistence boundary.
        safe_error = (
            error
            if isinstance(error, str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,79}", error)
            else "SummaryError"
        )
        with self.transaction() as db:
            db.execute(
                "UPDATE rectifications SET summary_status=CASE WHEN attempts>=3 "
                "THEN 'failed' ELSE 'queued' END,retry_at=?,last_error=?,updated=? "
                "WHERE id=? AND summary_status='running' AND status!='cancelled'",
                (self.clock() + 60, safe_error, self.clock(), session_id),
            )
