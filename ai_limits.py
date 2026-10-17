"""Persistent configurable rolling quotas, enforced only by AI entry points."""

import hashlib
import json
import math
from dataclasses import dataclass

MAX_WINDOW = 86400
LABELS = {"question": "解盘提问", "natal": "生年排盘", "decade": "大运排盘"}


@dataclass(frozen=True)
class QuotaPolicy:
    window_seconds: int = 3600
    question_limit: int = 6
    natal_limit: int = 2
    decade_limit: int = 3

    def __post_init__(self):
        for field, key in (
            ("window_seconds", "ai_quota_window_seconds"),
            ("question_limit", "ai_question_limit"),
            ("natal_limit", "ai_natal_limit"),
            ("decade_limit", "ai_decade_limit"),
        ):
            value = getattr(self, field)
            if type(value) is not int or value < 1:
                raise ValueError(f"{key} 须为正整数")
        if self.window_seconds > MAX_WINDOW:
            raise ValueError(f"ai_quota_window_seconds 须在1至{MAX_WINDOW}秒之间")

    @classmethod
    def from_config(cls, config):
        return cls(
            window_seconds=config.get("ai_quota_window_seconds", 3600),
            question_limit=config.get("ai_question_limit", 6),
            natal_limit=config.get("ai_natal_limit", 2),
            decade_limit=config.get("ai_decade_limit", 3),
        )

    def limit(self, kind):
        return getattr(self, f"{kind}_limit")

    def description(self):
        return (
            f"AI限额：每人滚动{self.window_seconds}秒内"
            f"解盘提问{self.question_limit}次、生年排盘{self.natal_limit}张、"
            f"大运排盘（含童限）{self.decade_limit}张。"
        )


DEFAULT_POLICY = QuotaPolicy()


class QuotaExceeded(Exception):
    def __init__(self, kind, retry_after, policy=DEFAULT_POLICY):
        self.kind = kind
        self.limit = policy.limit(kind)
        self.window_seconds = policy.window_seconds
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(
            f"AI {LABELS[kind]}滚动{self.window_seconds}秒内最多{self.limit}次"
        )

    def payload(self):
        return json.dumps(
            {
                "status": "rate_limited",
                "quota": self.kind,
                "limit": self.limit,
                "window_seconds": self.window_seconds,
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


def take(db, scope, kind, token, now, pending=False, *, policy=DEFAULT_POLICY):
    owner = identity(scope)
    # Retain one maximum window so shortening and then lengthening the configured
    # window cannot reset recent usage. Only the active window counts below.
    db.execute("DELETE FROM ai_usage WHERE created<=?", (now - MAX_WINDOW,))
    cutoff = now - policy.window_seconds
    limit = policy.limit(kind)
    # One message can span several tool calls beyond a short active window.
    # Deduplicate across the retained history without moving its first charge.
    if db.execute(
        "SELECT 1 FROM ai_usage WHERE owner=? AND kind=? AND token=?",
        (owner, kind, token),
    ).fetchone():
        return
    rows = db.execute(
        "SELECT created FROM ai_usage WHERE owner=? AND kind=? AND created>? "
        "ORDER BY created",
        (owner, kind, cutoff),
    ).fetchall()
    if len(rows) >= limit:
        raise QuotaExceeded(
            kind, rows[len(rows) - limit][0] + policy.window_seconds - now, policy
        )
    db.execute(
        "INSERT INTO ai_usage VALUES (?,?,?,?,?)",
        (owner, kind, token, now, int(pending)),
    )
