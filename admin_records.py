"""Administrator record index; private snapshots remain in the archive database."""

import hashlib
import json
import uuid


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def chart_summary(chart, *, atlas=False):
    flow = chart.get("flow") or {}
    kind = (
        "atlas"
        if atlas
        else "decade"
        if flow.get("kind") == "decade"
        else "date"
        if flow.get("target")
        else "year"
        if flow
        else "natal"
    )
    return kind, {
        "civil": chart["normalized"]["civil"],
        "sex": chart["sex"],
        "bureau": chart["bureau_name"],
        "target": flow.get("target") or flow.get("year") or "",
        "decade": (flow.get("decade") or {}).get("index"),
    }


class AdminRecords:
    """Additive v1 index, sharing ArchiveStore's transactions and lifetime."""

    def __init__(self, archive):
        self.archive = archive
        self.db, self.lock = archive.db, archive.lock
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS admin_records (
                id TEXT PRIMARY KEY,
                chart_id TEXT REFERENCES charts(id),
                atlas_id TEXT REFERENCES life_atlases(id),
                created REAL NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL,
                platform_id TEXT NOT NULL DEFAULT '',
                platform_name TEXT NOT NULL DEFAULT '',
                group_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '', user_name TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL, theme TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT '', request_key TEXT,
                fingerprint TEXT, web_chart TEXT,
                UNIQUE(actor, request_key));
            CREATE INDEX IF NOT EXISTS admin_records_created
                ON admin_records(created DESC, id);
            CREATE INDEX IF NOT EXISTS admin_records_group
                ON admin_records(platform_id, group_id, created DESC);
            CREATE INDEX IF NOT EXISTS admin_records_chart ON admin_records(chart_id);
            CREATE INDEX IF NOT EXISTS admin_records_user
                ON admin_records(platform_id,user_id,created DESC);
        """)
        # Historical identities were hashes. Never guess or reverse those hashes.
        with archive.transaction() as db:
            for row in db.execute(
                "SELECT a.id,a.chart_id,a.created,a.theme,c.chart FROM life_atlases a "
                "JOIN charts c ON c.id=a.chart_id "
                "WHERE NOT EXISTS(SELECT 1 FROM admin_records r WHERE r.id=a.id)"
            ).fetchall():
                self.add(
                    db,
                    json.loads(row["chart"]),
                    row["id"],
                    chart_id=row["chart_id"],
                    atlas_id=row["id"],
                    created=row["created"],
                    theme=row["theme"],
                )
            for row in db.execute(
                "SELECT c.id,c.created,c.chart FROM charts c WHERE NOT EXISTS("
                "SELECT 1 FROM admin_records r WHERE r.chart_id=c.id)"
            ).fetchall():
                self.add(
                    db,
                    json.loads(row["chart"]),
                    row["id"],
                    chart_id=row["id"],
                    created=row["created"],
                )

    def add(
        self,
        db,
        chart,
        record_id,
        *,
        scope=None,
        chart_id=None,
        atlas_id=None,
        created=None,
        theme="night",
        actor="",
        request_key=None,
        fingerprint=None,
        subject=None,
        binding_action="",
        restored=False,
    ):
        kind, summary = chart_summary(chart, atlas=bool(atlas_id))
        source = (
            "binding_import"
            if restored
            else "web"
            if actor
            else "group"
            if scope
            else "legacy"
        )
        if binding_action:
            summary |= {"binding_action": binding_action}
        identity = [
            str(getattr(scope, name, "") or "")[:256]
            for name in (
                "platform_id",
                "platform_name",
                "group_id",
                "user_id",
                "user_name",
            )
        ]
        if subject is not None:
            if scope is not None:
                raise ValueError("不能替换真实消息的命盘归属。")
            identity = [
                subject["platform_id"],
                "",
                "",
                subject["user_id"],
                subject.get("user_name", ""),
            ]
        db.execute(
            "INSERT INTO admin_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record_id,
                chart_id,
                atlas_id,
                self.archive.clock() if created is None else created,
                source,
                kind,
                *identity,
                encode(summary),
                theme,
                actor,
                request_key,
                fingerprint,
                encode(chart) if actor or restored else None,
            ),
        )
        return record_id

    def save_web(self, chart, theme, actor, request_key, fingerprint):
        with self.archive.transaction() as db:
            return self.add(
                db,
                chart,
                uuid.uuid4().hex,
                theme=theme,
                actor=actor,
                request_key=request_key,
                fingerprint=fingerprint,
            )

    def existing_web(self, actor, request_key):
        with self.lock:
            row = self.db.execute(
                "SELECT id,fingerprint FROM admin_records "
                "WHERE actor=? AND request_key=?",
                (actor, request_key),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def public_row(row):
        return {
            key: row[key]
            for key in (
                "id",
                "created",
                "source",
                "kind",
                "platform_id",
                "platform_name",
                "group_id",
                "user_id",
                "user_name",
                "theme",
                "actor",
            )
        } | {"summary": json.loads(row["summary"])}

    def get(self, record_id):
        with self.lock:
            row = self.db.execute(
                "SELECT r.*,COALESCE(r.web_chart,c.chart) AS snapshot,a.data AS atlas "
                "FROM admin_records r LEFT JOIN charts c ON c.id=r.chart_id "
                "LEFT JOIN life_atlases a ON a.id=r.atlas_id WHERE r.id=?",
                (record_id,),
            ).fetchone()
        if not row:
            return None
        return self.public_row(row) | {
            "chart": json.loads(row["snapshot"]),
            "atlas": json.loads(row["atlas"]) if row["atlas"] else None,
        }

    def list(
        self,
        *,
        page=1,
        source="",
        kind="",
        platform="",
        group="",
        user="",
        search="",
        start=None,
        end=None,
    ):
        conditions, values = [], []
        for key, value in (
            ("source", source),
            ("kind", kind),
            ("platform_id", platform),
            ("group_id", group),
            ("user_id", user),
        ):
            if value:
                conditions.append(f"{key}=?")
                values.append(value)
        if search:
            # instr treats %, _ and SQL-looking input as literal search text.
            conditions.append(
                "instr(lower(user_id || ' ' || user_name || ' ' "
                "|| actor || ' ' || id),?)>0"
            )
            values.append(search.lower())
        for op, value in ((">=", start), ("<", end)):
            if value is not None:
                conditions.append(f"created{op}?")
                values.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.lock:
            total = self.db.execute(
                "SELECT count(*) FROM admin_records" + where, values
            ).fetchone()[0]
            pages = max(1, (total + 19) // 20)
            page = min(page, pages)
            rows = self.db.execute(
                "SELECT * FROM admin_records"
                + where
                + " ORDER BY created DESC,id DESC LIMIT 20 OFFSET ?",
                [*values, (page - 1) * 20],
            ).fetchall()
        return {
            "items": [self.public_row(row) for row in rows],
            "page": page,
            "pages": pages,
            "total": total,
        }

    def overview(self):
        with self.lock:
            counts = dict(
                self.db.execute(
                    "SELECT source,count(*) FROM admin_records GROUP BY source"
                ).fetchall()
            )
            users = self.db.execute(
                "SELECT count(*) FROM (SELECT DISTINCT platform_id,group_id,user_id "
                "FROM admin_records WHERE source='group')"
            ).fetchone()[0]
            groups = self.db.execute(
                "SELECT DISTINCT platform_id,platform_name,group_id FROM admin_records "
                "WHERE source='group' ORDER BY platform_id,group_id"
            ).fetchall()
            platforms = [
                row[0]
                for row in self.db.execute(
                    "SELECT DISTINCT platform_id FROM admin_records "
                    "WHERE platform_id<>'' ORDER BY platform_id"
                ).fetchall()
            ]
        return {
            "counts": counts,
            "users": users,
            "groups": [dict(row) for row in groups],
            "platforms": platforms,
        }


def request_fingerprint(args):
    return hashlib.sha256(args.encode("utf-8")).hexdigest()
