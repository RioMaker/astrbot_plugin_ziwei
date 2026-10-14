"""Persistent rolling-hour quotas, enforced only by AI entry points."""

import hashlib
import json
import math

WINDOW = 3600
LIMITS = {"question": 6, "natal": 2, "decade": 3}
LABELS = {"question": "解盘提问", "natal": "生年排盘", "decade": "大运排盘"}


class QuotaExceeded(Exception):
    def __init__(self, kind, retry_after):
        self.kind = kind
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(f"AI {LABELS[kind]}一小时最多{LIMITS[kind]}次")

    def payload(self):
        return json.dumps(
            {
                "status": "rate_limited",
                "quota": self.kind,
                "limit": LIMITS[self.kind],
                "window_seconds": WINDOW,
                "retry_after_seconds": self.retry_after,
                "message": f"{self}，请在{self.retry_after}秒后再试。"
                "可读取或重发已有命盘；直接指令排盘不受此限。",
            },
            ensure_ascii=False,
        )


def identity(scope):
    # Same account across this platform instance's groups shares one quota.
    value = json.dumps([scope.platform_id, scope.user_id], ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


def question_token(scope, event_id):
    return hashlib.sha256(f"{scope.key}\0{event_id}".encode()).hexdigest()


def take(db, scope, kind, token, now, pending=False):
    owner = identity(scope)
    db.execute("DELETE FROM ai_usage WHERE created<=?", (now - WINDOW,))
    if db.execute(
        "SELECT 1 FROM ai_usage WHERE owner=? AND kind=? AND token=?",
        (owner, kind, token),
    ).fetchone():
        return
    rows = db.execute(
        "SELECT created FROM ai_usage WHERE owner=? AND kind=? ORDER BY created",
        (owner, kind),
    ).fetchall()
    if len(rows) >= LIMITS[kind]:
        raise QuotaExceeded(kind, rows[len(rows) - LIMITS[kind]][0] + WINDOW - now)
    db.execute(
        "INSERT INTO ai_usage VALUES (?,?,?,?,?)",
        (owner, kind, token, now, int(pending)),
    )
