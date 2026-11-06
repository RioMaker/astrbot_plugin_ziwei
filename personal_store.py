"""Persistent personal natal charts, scoped to platform instance and user."""

import hashlib
import json
import re
import uuid
from copy import deepcopy

from .admin_records import chart_summary


class BindingError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def identity(platform, user):
    for value in (platform, user):
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 256
            or any(ord(c) < 32 for c in value)
        ):
            raise BindingError("机器人实例和用户账号须明确填写。")
    return platform.strip(), user.strip()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def natal_snapshot(chart):
    """Remove flow layers without recalculating the saved natal placements/rules."""
    result = deepcopy(chart)
    result["flow"] = None
    stars = {}
    for star in result["stars"]:
        star["hua"] = {
            k: v for k, v in star["hua"].items() if k in {"生年", "命宫", "日干"}
        }
        stars[star["id"], star["branch"], star["secondary"]] = star
    for palace in result["palaces"]:
        palace["flow_names"] = {}
        # JSON archives duplicate objects. Restore the engine's shared star graph.
        palace["stars"] = [
            stars[s["id"], s["branch"], s["secondary"]] for s in palace["stars"]
        ]
    return result


class PersonalStore:
    def __init__(self, archive):
        self.archive = archive
        self.db, self.lock = archive.db, archive.lock
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS personal_bindings (
                platform_id TEXT NOT NULL, user_id TEXT NOT NULL,
                user_name TEXT NOT NULL, revision TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL,
                chart TEXT, source_record TEXT NOT NULL, source_group TEXT NOT NULL,
                actor TEXT NOT NULL, via TEXT NOT NULL,
                PRIMARY KEY(platform_id,user_id));
            CREATE TABLE IF NOT EXISTS binding_changes (
                actor_key TEXT NOT NULL, request_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, platform_id TEXT NOT NULL,
                user_id TEXT NOT NULL, action TEXT NOT NULL,
                revision TEXT NOT NULL, created REAL NOT NULL,
                actor TEXT NOT NULL, via TEXT NOT NULL, source_record TEXT NOT NULL,
                PRIMARY KEY(actor_key,request_id));
        """)

    def state(self, platform, user):
        platform, user = identity(platform, user)
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM personal_bindings WHERE platform_id=? AND user_id=?",
                (platform, user),
            ).fetchone()
        if row:
            data = dict(row)
            data["chart"] = (
                natal_snapshot(json.loads(data["chart"])) if data["chart"] else None
            )
            return data | {"active": data["chart"] is not None}
        return {
            "platform_id": platform,
            "user_id": user,
            "user_name": "",
            "revision": "",
            "active": False,
            "chart": None,
            "created": None,
            "updated": None,
            "source_record": "",
            "source_group": "",
            "actor": "",
            "via": "",
        }

    @staticmethod
    def public(state):
        return {key: value for key, value in state.items() if key != "chart"} | {
            "summary": chart_summary(state["chart"])[1] if state["chart"] else None,
        }

    def current(self, scope):
        state = self.state(scope.platform_id, scope.user_id)
        return state if state["active"] else None

    def assert_current(self, platform, user, expected):
        state = self.state(platform, user)
        if state["revision"] != expected:
            raise BindingError("绑定已变更，请重新查看后再操作。", 409)
        return state

    def replay(self, actor_key, request_id, fingerprint):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM binding_changes WHERE actor_key=? AND request_id=?",
                (actor_key, request_id),
            ).fetchone()
        if row:
            if row["fingerprint"] != fingerprint:
                raise BindingError("同一请求编号的内容不同，请重新操作。", 409)
            return self.state(row["platform_id"], row["user_id"])
        return None

    def mutate(
        self,
        platform,
        user,
        *,
        action,
        expected,
        actor,
        via,
        request_id,
        fingerprint,
        chart=None,
        source_record="",
        source_group="",
        user_name="",
        new_record=False,
        scope=None,
        theme="night",
    ):
        platform, user = identity(platform, user)
        actor_key = encode([via, actor])
        if action not in {"bind", "replace", "unbind"}:
            raise BindingError("绑定操作无效。")
        with self.archive.transaction() as db:
            replay = self.replay(actor_key, request_id, fingerprint)
            if replay is not None:
                return replay
            old = self.assert_current(platform, user, expected)
            if action == "bind" and old["active"]:
                raise BindingError("已有绑定命盘，请使用 /紫微 换绑 明确替换。", 409)
            if action in {"replace", "unbind"} and not old["active"]:
                raise BindingError("尚未绑定命盘，请先使用 /紫微 绑定。", 409)
            if action != "unbind":
                if chart is None:
                    raise BindingError("请选择命盘或填写出生资料。")
                chart = natal_snapshot(chart)
                if new_record:
                    if scope is not None:
                        if (scope.platform_id, scope.user_id) != (platform, user):
                            raise BindingError("命盘归属与真实发送者不一致。")
                        source_record = self.archive._insert_chart(db, scope, chart)
                        self.archive.records.add(
                            db,
                            chart,
                            source_record,
                            scope=scope,
                            chart_id=source_record,
                            theme=theme,
                        )
                    else:
                        source_record = uuid.uuid4().hex
                        self.archive.records.add(
                            db, chart, source_record, actor=actor, theme=theme
                        )
            else:
                chart, source_record, source_group = None, "", ""
            version = uuid.uuid4().hex
            now = self.archive.clock()
            db.execute(
                "INSERT INTO personal_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(platform_id,user_id) DO UPDATE SET "
                "user_name=excluded.user_name,"
                "revision=excluded.revision,updated=excluded.updated,chart=excluded.chart,"
                "source_record=excluded.source_record,source_group=excluded.source_group,"
                "actor=excluded.actor,via=excluded.via",
                (
                    platform,
                    user,
                    user_name or old["user_name"],
                    version,
                    old["created"] or now,
                    now,
                    encode(chart) if chart else None,
                    source_record,
                    source_group,
                    actor,
                    via,
                ),
            )
            db.execute(
                "INSERT INTO binding_changes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    actor_key,
                    request_id,
                    fingerprint,
                    platform,
                    user,
                    action,
                    version,
                    now,
                    actor,
                    via,
                    source_record,
                ),
            )
        return self.state(platform, user)

    def own_choices(self, scope, prefix="", page=1):
        # Known identities permit same-user cross-group selection; old hashes only
        # permit reading the requester's own current-group archive.
        owner = hashlib.sha256(
            json.dumps(
                [scope.platform_id, scope.group_id, scope.user_id],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if prefix and not re.fullmatch(r"[a-f0-9]{8,32}", prefix):
            raise BindingError("命盘编号须为列表中的短编号或完整编号。")
        where = "((r.source='group' AND r.platform_id=? AND r.user_id=?) OR c.owner=?)"
        values = [scope.platform_id, scope.user_id, owner]
        if prefix:
            where += " AND substr(r.id,1,?)=?"
            values += [len(prefix), prefix]
        with self.lock:
            total = self.db.execute(
                "SELECT count(*) FROM admin_records r "
                "LEFT JOIN charts c ON c.id=r.chart_id WHERE " + where,
                values,
            ).fetchone()[0]
            pages = max(1, (total + 9) // 10)
            page = min(max(1, page), pages)
            rows = self.db.execute(
                "SELECT r.id FROM admin_records r "
                "LEFT JOIN charts c ON c.id=r.chart_id WHERE "
                + where
                + " ORDER BY r.created DESC,r.id DESC LIMIT 10 OFFSET ?",
                [*values, (page - 1) * 10],
            ).fetchall()
            items = [self.archive.records.get(row["id"]) for row in rows]
        return {"items": items, "page": page, "pages": pages, "total": total}

    def users(self, search="", platform="", page=1):
        # A user may have records in many groups, but has one binding per instance.
        cte = """WITH people AS (
            SELECT platform_id,user_id FROM admin_records WHERE source='group'
            UNION SELECT platform_id,user_id FROM personal_bindings
        ), latest AS (
            SELECT p.platform_id,p.user_id,COALESCE(NULLIF(b.user_name,''),(
                SELECT r.user_name FROM admin_records r WHERE r.source='group'
                AND r.platform_id=p.platform_id AND r.user_id=p.user_id
                AND r.user_name<>'' ORDER BY r.created DESC,r.id DESC LIMIT 1),'')
                AS user_name,
                b.chart IS NOT NULL AS active,b.updated,b.revision,b.actor,b.via
            FROM people p LEFT JOIN personal_bindings b
            ON p.platform_id=b.platform_id AND p.user_id=b.user_id
        ) """
        where = (
            "WHERE (?='' OR platform_id=?) "
            "AND instr(lower(user_id || ' ' || user_name),?)>0"
        )
        values = (platform, platform, search.lower())
        with self.lock:
            total = self.db.execute(
                cte + "SELECT count(*) FROM latest " + where, values
            ).fetchone()[0]
            pages = max(1, (total + 19) // 20)
            page = min(max(1, page), pages)
            rows = self.db.execute(
                cte
                + "SELECT * FROM latest "
                + where
                + " ORDER BY active DESC,updated DESC,platform_id,user_id "
                "LIMIT 20 OFFSET ?",
                (*values, (page - 1) * 20),
            ).fetchall()
            platforms = [
                row[0]
                for row in self.db.execute(
                    "SELECT platform_id FROM personal_bindings UNION "
                    "SELECT platform_id FROM admin_records "
                    "WHERE source='group' ORDER BY platform_id"
                ).fetchall()
            ]
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "pages": pages,
            "platforms": platforms,
        }
