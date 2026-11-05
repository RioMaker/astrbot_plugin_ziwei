import sqlite3

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.archive_store import ArchiveStore

from .conftest import Event
from .test_readings import natal


def usage(plugin, kind):
    return plugin.archive.db.execute(
        "SELECT count(*) FROM ai_usage WHERE kind=?", (kind,)
    ).fetchone()[0]


def test_v1_database_upgrades_without_changing_charts_and_discards_pending(tmp_path):
    path = tmp_path / "archive.sqlite3"
    store = ArchiveStore(path)
    scope = capture_group(Event())
    chart_id = store.save_chart(scope, natal())
    token = store.reserve_chart(scope, "natal")
    assert token
    store.close()
    resumed = ArchiveStore(path)
    assert resumed.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 0
    resumed.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE ai_usage")
        db.execute("PRAGMA user_version=1")
        db.execute("INSERT INTO groups_state VALUES ('unchanged',1)")
    upgraded = ArchiveStore(path)
    assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 5
    assert (
        upgraded.db.execute(
            "SELECT enabled FROM groups_state WHERE group_key='unchanged'"
        ).fetchone()[0]
        == 1
    )
    assert upgraded.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 0
    assert upgraded.get_chart(scope, chart_id) is not None
    upgraded.close()
