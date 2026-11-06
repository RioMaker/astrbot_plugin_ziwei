"""Permanent links from binding operations to each user's archived natal chart."""

import json
import uuid


class BindingHistory:
    def __init__(self, archive, natal_snapshot):
        self.archive, self.natal_snapshot = archive, natal_snapshot
        self.db, self.lock = archive.db, archive.lock
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS binding_history_records (
                revision TEXT PRIMARY KEY, record_id TEXT NOT NULL,
                previous_record TEXT NOT NULL, restored INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS binding_changes_user
                ON binding_changes(platform_id,user_id);
        """)
        self._migrate()

    @staticmethod
    def record(db, revision, record_id, previous_record, *, restored=False):
        db.execute(
            "INSERT INTO binding_history_records VALUES (?,?,?,?)",
            (revision, record_id, previous_record, int(restored)),
        )

    def _migrate(self):
        # Older releases kept an audit but did not attach every snapshot to the
        # target user. Preserve source records and only add verifiable links/copies.
        with self.archive.transaction() as db:
            missing = db.execute(
                "SELECT 1 FROM binding_changes b LEFT JOIN binding_history_records h "
                "ON h.revision=b.revision WHERE h.revision IS NULL LIMIT 1"
            ).fetchone()
            if not missing:
                return
            previous, used = {}, set()
            for change in db.execute(
                "SELECT * FROM binding_changes ORDER BY rowid"
            ).fetchall():
                owner = change["platform_id"], change["user_id"]
                prior = previous.get(owner, "")
                link = db.execute(
                    "SELECT * FROM binding_history_records WHERE revision=?",
                    (change["revision"],),
                ).fetchone()
                if link is not None:
                    record_id = link["record_id"]
                elif change["action"] == "unbind":
                    record_id = prior
                    self.record(db, change["revision"], record_id, prior, restored=True)
                else:
                    row = None
                    try:
                        row = self.archive.records.get(change["source_record"])
                        chart = self.natal_snapshot(row["chart"]) if row else None
                    except (ValueError, TypeError, KeyError):
                        row, chart = None, None
                    if chart is None:
                        current = db.execute(
                            "SELECT chart FROM personal_bindings WHERE platform_id=? "
                            "AND user_id=? AND revision=?",
                            (*owner, change["revision"]),
                        ).fetchone()
                        if current and current["chart"]:
                            chart = self.natal_snapshot(json.loads(current["chart"]))
                    record_id = ""
                    if chart is not None:
                        if (
                            row
                            and row["kind"] == "natal"
                            and (row["platform_id"], row["user_id"]) == owner
                            and row["id"] not in used
                        ):
                            record_id = row["id"]
                        else:
                            record_id = uuid.uuid4().hex
                            self.archive.records.add(
                                db,
                                chart,
                                record_id,
                                actor=change["actor"],
                                subject={"platform_id": owner[0], "user_id": owner[1]},
                                binding_action=change["action"],
                                restored=True,
                                created=change["created"],
                                theme=row["theme"] if row else "night",
                            )
                    self.record(db, change["revision"], record_id, prior, restored=True)
                    if record_id:
                        db.execute(
                            "UPDATE personal_bindings SET source_record=? WHERE "
                            "platform_id=? AND user_id=? AND revision=? "
                            "AND chart IS NOT NULL",
                            (record_id, *owner, change["revision"]),
                        )
                if record_id:
                    used.add(record_id)
                previous[owner] = "" if change["action"] == "unbind" else record_id

    def list(self, platform, user, page=1):
        with self.lock:
            total = self.db.execute(
                "SELECT count(*) FROM binding_changes "
                "WHERE platform_id=? AND user_id=?",
                (platform, user),
            ).fetchone()[0]
            pages = max(1, (total + 19) // 20)
            page = min(page, pages)
            rows = self.db.execute(
                "SELECT b.*,h.record_id,h.previous_record,h.restored,"
                "r.id AS available,r.summary,p.id AS previous_available,"
                "p.summary AS previous_summary FROM binding_changes b "
                "JOIN binding_history_records h ON h.revision=b.revision "
                "LEFT JOIN admin_records r ON r.id=h.record_id "
                "LEFT JOIN admin_records p ON p.id=h.previous_record "
                "WHERE b.platform_id=? AND b.user_id=? ORDER BY b.rowid DESC "
                "LIMIT 20 OFFSET ?",
                (platform, user, (page - 1) * 20),
            ).fetchall()
        items = []
        for row in rows:
            group = ""
            if row["via"] == "chat":
                try:
                    actor = json.loads(row["actor"])
                    if (
                        isinstance(actor, list)
                        and len(actor) == 3
                        and actor[0] == platform
                        and actor[2] == user
                    ):
                        group = actor[1]
                except (TypeError, ValueError):
                    pass
            items.append(
                {
                    "id": row["revision"],
                    "action": row["action"],
                    "created": row["created"],
                    "via": row["via"],
                    "actor": row["actor"] if row["via"] == "admin" else user,
                    "group_id": group,
                    "record_id": row["record_id"],
                    "previous_record": row["previous_record"],
                    "available": bool(row["available"]),
                    "previous_available": bool(row["previous_available"]),
                    "summary": json.loads(row["summary"]) if row["summary"] else None,
                    "previous_summary": json.loads(row["previous_summary"])
                    if row["previous_summary"]
                    else None,
                    "restored": bool(row["restored"]),
                }
            )
        return {
            "platform_id": platform,
            "user_id": user,
            "items": items,
            "total": total,
            "page": page,
            "pages": pages,
        }
