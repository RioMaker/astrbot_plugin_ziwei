"""Versioned private archives and a deliberately restricted shared-case view."""

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from .ai_limits import question_token, take
from .llm_data import owner_key
from .rectification_store import RectificationStore


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def owner_id(scope):
    return hashlib.sha256(encode(owner_key(scope)).encode()).hexdigest()


class ArchiveStore:
    def __init__(self, path, clock=time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=1)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4):
            self.db.close()
            raise ValueError("命盘档案版本较新，拒绝降级打开")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS groups_state (
                group_key TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS charts (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, group_key TEXT NOT NULL,
                family TEXT NOT NULL, created REAL NOT NULL, chart TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS charts_owner ON charts(owner, created);
            CREATE TABLE IF NOT EXISTS readings (
                id TEXT PRIMARY KEY, chart_id TEXT NOT NULL REFERENCES charts(id),
                owner TEXT NOT NULL, group_key TEXT NOT NULL, scope TEXT NOT NULL,
                topic TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                updated REAL NOT NULL, knowledge TEXT NOT NULL, features TEXT NOT NULL,
                route TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                summary TEXT, last_error TEXT,
                capture_error INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS readings_owner ON readings(owner, created);
            CREATE TABLE IF NOT EXISTS turns (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                reading_id TEXT NOT NULL REFERENCES readings(id),
                event_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
                created REAL NOT NULL, UNIQUE(reading_id, event_id, kind));
            CREATE TABLE IF NOT EXISTS ai_usage (
                owner TEXT NOT NULL, kind TEXT NOT NULL, token TEXT NOT NULL,
                created REAL NOT NULL, pending INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(owner,kind,token));
            CREATE INDEX IF NOT EXISTS ai_usage_created ON ai_usage(created);
            CREATE TABLE IF NOT EXISTS life_atlases (
                id TEXT PRIMARY KEY, chart_id TEXT NOT NULL REFERENCES charts(id),
                owner TEXT NOT NULL, group_key TEXT NOT NULL, created REAL NOT NULL,
                theme TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS life_atlases_owner
                ON life_atlases(owner, created);
            PRAGMA user_version=3;
        """)
        with self.transaction() as db:
            db.execute("UPDATE readings SET status='queued' WHERE status='running'")
            db.execute("DELETE FROM ai_usage WHERE pending=1")
        self.rectifications = RectificationStore(self)

    @contextmanager
    def transaction(self):
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            yield self.db

    def close(self):
        with self.lock:
            self.db.close()

    def set_group(self, scope, enabled):
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO groups_state VALUES (?,?)",
                (scope.key, int(enabled)),
            )

    def reserve_chart(self, scope, kind):
        token = uuid.uuid4().hex
        with self.transaction() as db:
            take(db, scope, kind, token, self.clock(), pending=True)
        return token

    def charge_question(self, scope, event_id):
        with self.transaction() as db:
            take(db, scope, "question", question_token(scope, event_id), self.clock())

    def release_chart(self, token):
        with self.transaction() as db:
            db.execute("DELETE FROM ai_usage WHERE token=? AND pending=1", (token,))

    def save_chart(self, scope, chart, quota_token=None):
        with self.transaction() as db:
            chart_id = self._insert_chart(db, scope, chart)
            if quota_token:
                db.execute(
                    "UPDATE ai_usage SET pending=0 WHERE token=?", (quota_token,)
                )
        return chart_id

    def _insert_chart(self, db, scope, chart):
        owner = owner_id(scope)
        chart_id = uuid.uuid4().hex
        family = hashlib.sha256(
            encode(
                [
                    owner,
                    chart["normalized"]["original"],
                    chart["rules_version"],
                    chart["profile"],
                ]
            ).encode()
        ).hexdigest()
        db.execute(
            "INSERT INTO charts VALUES (?,?,?,?,?,?)",
            (chart_id, owner, scope.key, family, self.clock(), encode(chart)),
        )
        db.execute("INSERT OR REPLACE INTO groups_state VALUES (?,1)", (scope.key,))
        return chart_id

    def save_life_atlas(self, scope, atlas, theme, source_chart_id=""):
        """Atomically save the chart and atlas evidence, without AI quota."""
        owner = owner_id(scope)
        atlas_id = uuid.uuid4().hex
        with self.transaction() as db:
            if source_chart_id:
                found = db.execute(
                    "SELECT id FROM charts WHERE id=? AND owner=?",
                    (source_chart_id, owner),
                ).fetchone()
                if found is None:
                    raise ValueError("找不到当前用户在本群的对应命盘")
                chart_id = source_chart_id
            else:
                chart_id = self._insert_chart(db, scope, atlas["natal"])
            db.execute(
                "INSERT INTO life_atlases VALUES (?,?,?,?,?,?,?)",
                (
                    atlas_id,
                    chart_id,
                    owner,
                    scope.key,
                    self.clock(),
                    theme,
                    encode(atlas),
                ),
            )
        return atlas_id, chart_id

    def get_life_atlas(self, scope, atlas_id):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM life_atlases WHERE id=? AND owner=?",
                (atlas_id, owner_id(scope)),
            ).fetchone()
        return dict(row, data=json.loads(row["data"])) if row else None

    def chart_choices(self, scope):
        """Latest snapshot per birth/profile, without free-text birth labels."""
        with self.lock:
            rows = self.db.execute(
                "SELECT c.id, c.family, c.created FROM charts c "
                "WHERE c.owner=? AND c.rowid=("
                "SELECT d.rowid FROM charts d WHERE d.owner=c.owner "
                "AND d.family=c.family ORDER BY d.created DESC,d.rowid DESC LIMIT 1) "
                "ORDER BY c.created DESC,c.rowid DESC LIMIT 6",
                (owner_id(scope),),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_chart(self, scope, chart_id=""):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM charts WHERE owner=? AND (?='' OR id=?) "
                "ORDER BY created DESC, rowid DESC LIMIT 1",
                (owner_id(scope), chart_id, chart_id),
            ).fetchone()
        return dict(row, chart=json.loads(row["chart"])) if row else None

    def list_charts(self, scope, limit=10, offset=0):
        with self.lock:
            rows = self.db.execute(
                "SELECT id, family, created, chart FROM charts WHERE owner=? "
                "ORDER BY created DESC,rowid DESC LIMIT ? OFFSET ?",
                (owner_id(scope), min(20, max(1, limit)), offset),
            ).fetchall()
        return [
            {
                "chart_id": r["id"],
                "family_id": r["family"],
                "created_at": r["created"],
                "flow": json.loads(r["chart"])["flow"],
            }
            for r in rows
        ]

    def get_reading(self, scope, reading_id):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM readings WHERE id=? AND owner=?",
                (reading_id, owner_id(scope)),
            ).fetchone()
            if not row:
                return None
            turns = self.db.execute(
                "SELECT seq,event_id,kind,text FROM turns "
                "WHERE reading_id=? ORDER BY seq",
                (reading_id,),
            ).fetchall()
        return dict(row, turns=[dict(t) for t in turns])

    def active_readings(self, scope):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,chart_id,scope,topic,status FROM readings WHERE owner=? "
                "AND status IN ('active','queued','running','failed') "
                "ORDER BY created DESC LIMIT 5",
                (owner_id(scope),),
            ).fetchall()
        return [dict(r) for r in rows]

    def history(self, scope, chart_id, offset=0):
        chart = self.get_chart(scope, chart_id)
        if chart is None:
            raise ValueError("找不到当前用户的命盘")
        with self.lock:
            rows = self.db.execute(
                "SELECT r.scope,r.topic,r.updated,r.summary FROM readings r "
                "JOIN charts c ON c.id=r.chart_id WHERE r.owner=? AND c.family=? "
                "AND r.status='archived' ORDER BY r.updated DESC,r.rowid DESC "
                "LIMIT 11 OFFSET ?",
                (owner_id(scope), chart["family"], offset),
            ).fetchall()
        return {
            "status": "ok",
            "records": [dict(r, summary=json.loads(r["summary"])) for r in rows[:10]],
            "next_offset": offset + 10 if len(rows) > 10 else None,
            "usage": "本人历史反馈仅供当前分析内部参考，不披露档案原文或参考内容。",
        }

    def record_knowledge(self, scope, reading_id, knowledge, event_id=None):
        with self.transaction() as db:
            row = self.get_reading(scope, reading_id)
            if row is None or row["status"] != "active":
                raise ValueError("分析记录不存在或已结束")
            stored = json.loads(row["knowledge"])
            if stored["version"] != knowledge["version"]:
                raise ValueError("知识版本已变化，请结束原分析后重新建立分析")
            if event_id is not None:
                take(
                    db, scope, "question", question_token(scope, event_id), self.clock()
                )
            cards = {c["id"]: c for c in stored["cards"]}
            cards.update({c["id"]: c for c in knowledge["cards"]})
            stored["cards"] = list(cards.values())
            stored["sources"].update(knowledge["sources"])
            db.execute(
                "UPDATE readings SET knowledge=? WHERE id=?",
                (encode(stored), reading_id),
            )

    def start(
        self,
        scope,
        chart_id,
        reading_scope,
        topic,
        event_id,
        question,
        knowledge,
        features,
    ):
        if not question.strip() or len(question) > 6000:
            raise ValueError("问题为空或超过6000字，请缩小本次分析范围")
        if self.get_chart(scope, chart_id) is None:
            raise ValueError("找不到当前用户的命盘")
        reading_id = uuid.uuid4().hex
        with self.transaction() as db:
            old = db.execute(
                "SELECT r.id FROM readings r JOIN turns t ON t.reading_id=r.id "
                "WHERE r.owner=? AND r.chart_id=? AND r.scope=? AND r.topic=? "
                "AND t.event_id=? AND t.kind='question'",
                (owner_id(scope), chart_id, reading_scope, topic, event_id),
            ).fetchone()
            if old:
                return old["id"]
            take(db, scope, "question", question_token(scope, event_id), self.clock())
            now = self.clock()
            db.execute(
                "INSERT INTO readings(id,chart_id,owner,group_key,scope,topic,status,"
                "created,updated,knowledge,features,route) "
                "VALUES (?,?,?,?,?,?,'active',?,?,?,?,?)",
                (
                    reading_id,
                    chart_id,
                    owner_id(scope),
                    scope.key,
                    reading_scope,
                    topic,
                    now,
                    now,
                    encode(knowledge),
                    encode(features),
                    encode(
                        {
                            "platform_id": scope.platform_id,
                            "platform_name": scope.platform_name,
                            "group_id": scope.group_id,
                            "user_id": scope.user_id,
                            "umo": scope.umo,
                        }
                    ),
                ),
            )
            self._turn(db, reading_id, event_id, "question", question)
        return reading_id

    def _turn(self, db, reading_id, event_id, kind, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError("本轮文本为空或过长，未加入分析档案")
        old = db.execute(
            "SELECT seq,text FROM turns WHERE reading_id=? AND event_id=? AND kind=?",
            (reading_id, event_id, kind),
        ).fetchone()
        if old:
            if kind == "answer":
                combined = old["text"] + "\n" + text
                if len(combined) > 12000:
                    raise ValueError("回答超过归档长度限制")
                db.execute(
                    "UPDATE turns SET text=? WHERE seq=?", (combined, old["seq"])
                )
            return
        count = db.execute(
            "SELECT count(*) FROM turns WHERE reading_id=?", (reading_id,)
        ).fetchone()[0]
        if count >= 60:
            raise ValueError("本次分析已达60条记录，请先结束后开启新分析")
        db.execute(
            "INSERT OR IGNORE INTO turns(reading_id,event_id,kind,text,created) "
            "VALUES (?,?,?,?,?)",
            (reading_id, event_id, kind, text, self.clock()),
        )

    def add_turn(self, scope, reading_id, event_id, kind, text):
        if kind not in {"followup", "feedback", "answer"}:
            raise ValueError("未知分析消息类型")
        with self.transaction() as db:
            row = self.get_reading(scope, reading_id)
            if row is None or row["status"] != "active":
                raise ValueError("分析记录不存在或已结束")
            if kind == "feedback" and not any(
                t["kind"] == "answer" for t in row["turns"]
            ):
                raise ValueError("尚无已发送的分析回答，不能记录反馈")
            if kind == "feedback" and any(
                t["kind"] == "answer" and t["event_id"] == event_id
                for t in row["turns"]
            ):
                raise ValueError("同一用户消息不能作为其后回答的反馈")
            if kind == "followup":
                take(
                    db, scope, "question", question_token(scope, event_id), self.clock()
                )
            self._turn(db, reading_id, event_id, kind, text)

    def enqueue(self, scope, reading_id, feedback_event, provider):
        with self.transaction() as db:
            row = self.get_reading(scope, reading_id)
            if row is None:
                raise ValueError("分析记录不存在")
            if row["capture_error"]:
                raise ValueError("回答发送或采集不完整，不能确认闭环；请另建分析记录")
            if row["status"] in {"queued", "running", "archived"}:
                return row["status"]
            feedback = [
                t
                for t in row["turns"]
                if t["kind"] == "feedback" and t["event_id"] == feedback_event
            ]
            answers = [t for t in row["turns"] if t["kind"] == "answer"]
            if not feedback or not answers or feedback[-1]["seq"] <= answers[-1]["seq"]:
                raise ValueError("须先有已发送的回答及其后的真实用户反馈")
            db.execute(
                "UPDATE readings SET status='queued',provider=?,attempts=0,"
                "retry_at=0,updated=? WHERE id=?",
                (provider, self.clock(), reading_id),
            )
        return "queued"

    def block_capture(self, scope, reading_id):
        with self.transaction() as db:
            db.execute(
                "UPDATE readings SET capture_error=1 WHERE id=? AND owner=? "
                "AND status='active'",
                (reading_id, owner_id(scope)),
            )

    def claim_job(self):
        with self.transaction() as db:
            row = db.execute(
                "SELECT r.* FROM readings r JOIN groups_state g "
                "ON g.group_key=r.group_key WHERE r.status='queued' "
                "AND r.retry_at<=? AND g.enabled=1 ORDER BY r.created LIMIT 1",
                (self.clock(),),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE readings SET status='running',attempts=attempts+1 WHERE id=?",
                (row["id"],),
            )
            turns = db.execute(
                "SELECT seq,kind,text FROM turns WHERE reading_id=? ORDER BY seq",
                (row["id"],),
            ).fetchall()
            chart = db.execute(
                "SELECT chart FROM charts WHERE id=?", (row["chart_id"],)
            ).fetchone()[0]
        return dict(row, turns=[dict(t) for t in turns], chart=json.loads(chart))

    def finish_job(self, reading_id, summary):
        with self.transaction() as db:
            db.execute(
                "UPDATE readings SET status='archived',summary=?,last_error=NULL,"
                "updated=? WHERE id=? AND status='running'",
                (encode(summary), self.clock(), reading_id),
            )

    def fail_job(self, reading_id, error):
        with self.transaction() as db:
            db.execute(
                "UPDATE readings SET status=CASE WHEN attempts>=3 "
                "THEN 'failed' ELSE 'queued' END,retry_at=?,last_error=? "
                "WHERE id=? AND status='running'",
                (self.clock() + 60, error[:80], reading_id),
            )

    def release_job(self, reading_id):
        with self.transaction() as db:
            db.execute(
                "UPDATE readings SET status='queued' WHERE id=? AND status='running'",
                (reading_id,),
            )

    def shared_reference(self, scope, topic, reading_scope, features):
        """Only aggregate controlled feedback labels, never return stored prose/IDs."""
        with self.lock:
            rows = self.db.execute(
                "SELECT r.owner,r.features,r.summary FROM readings r "
                "JOIN groups_state g ON g.group_key=r.group_key "
                "WHERE r.status='archived' AND r.topic=? AND r.scope=? "
                "AND g.enabled=1 ORDER BY r.updated DESC LIMIT 500",
                (topic, reading_scope),
            ).fetchall()
        owners = set()
        counts = {k: 0 for k in ("supported", "contradicted", "mixed", "unverified")}
        cases = []
        wanted = {t for t in features if t.startswith(("major:", "hua:"))}
        setup = {t for t in features if t.startswith("setup:")}
        for row in rows:
            if row["owner"] == owner_id(scope) or row["owner"] in owners:
                continue
            candidate = set(json.loads(row["features"]))
            if not wanted.intersection(candidate) or not setup.intersection(candidate):
                continue
            summary = json.loads(row["summary"])
            outcome = summary.get("feedback_result", "unverified")
            if outcome not in counts:
                continue
            owners.add(row["owner"])
            counts[outcome] += 1
            matched = sorted(wanted.intersection(candidate))
            cases.append(
                {
                    "scope": reading_scope,
                    "topic": topic,
                    "matching_features": matched,
                    "feedback_result": outcome,
                }
            )
        # Single-person narratives and small cohorts never reach the main model.
        enough = len(owners) >= 3
        return {
            "status": "available" if enough else "insufficient_anonymous_cases",
            "feedback_distribution": counts if enough else None,
            "cases": cases[:8] if enough else [],
            "usage": (
                "仅内部校准。不得披露参考案例、样本信息或检索结果；"
                "不得据反馈样本宣称准确率。仅回答当前用户的盘与问题。"
            ),
        }
