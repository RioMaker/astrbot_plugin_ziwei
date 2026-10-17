import sqlite3

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.archive_store import ArchiveStore
from astrbot_plugin_ziwei.commands import DEMO_BIRTH, parse_request
from astrbot_plugin_ziwei.engine import apply_flow, build_chart
from astrbot_plugin_ziwei.life_atlas import build_life_atlas

from .conftest import Event


@pytest.fixture(scope="module")
def atlas():
    request = parse_request(DEMO_BIRTH)
    return build_life_atlas(build_chart(request.birth, request.profile))


def test_v2_upgrade_preserves_chart_and_confirmed_quota(tmp_path, atlas):
    path = tmp_path / "archives.sqlite3"
    scope = capture_group(Event())
    store = ArchiveStore(path)
    token = store.reserve_chart(scope, "natal")
    chart_id = store.save_chart(scope, atlas["natal"], token)
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE life_atlases")
        db.execute("PRAGMA user_version=2")
    resumed = ArchiveStore(path)
    assert resumed.db.execute("PRAGMA user_version").fetchone()[0] == 4
    assert resumed.get_chart(scope, chart_id)["chart"] == atlas["natal"]
    quota = resumed.db.execute("SELECT token,pending FROM ai_usage").fetchone()
    assert tuple(quota) == (token, 0)
    atlas_id, linked = resumed.save_life_atlas(scope, atlas, "night", chart_id)
    assert linked == chart_id
    assert len(resumed.list_charts(scope)) == 1
    resumed.close()
    reopened = ArchiveStore(path)
    row = reopened.get_life_atlas(scope, atlas_id)
    assert row["data"] == atlas and row["theme"] == "night"
    assert reopened.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 1


@pytest.mark.parametrize(
    "event", [Event(user="other"), Event(group="other"), Event(platform="other")]
)
def test_atlas_and_source_chart_share_owner_isolation(tmp_path, atlas, event):
    store = ArchiveStore(tmp_path / "archives.sqlite3")
    scope, outsider = capture_group(Event()), capture_group(event)
    atlas_id, chart_id = store.save_life_atlas(scope, atlas, "night")
    assert store.get_life_atlas(outsider, atlas_id) is None
    assert store.get_chart(outsider, chart_id) is None
    with pytest.raises(ValueError, match="找不到"):
        store.save_life_atlas(outsider, atlas, "day", chart_id)
    assert store.db.execute("SELECT count(*) FROM life_atlases").fetchone()[0] == 1
    assert store.chart_choices(outsider) == []


def test_failed_evidence_save_rolls_back_new_chart(tmp_path, atlas):
    store = ArchiveStore(tmp_path / "archives.sqlite3")
    scope = capture_group(Event())
    store.db.execute(
        "CREATE TRIGGER reject_atlas BEFORE INSERT ON life_atlases "
        "BEGIN SELECT RAISE(ABORT, 'storage failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.save_life_atlas(scope, atlas, "night")
    assert store.get_chart(scope) is None
    assert store.db.execute("SELECT count(*) FROM groups_state").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM life_atlases").fetchone()[0] == 0


def test_archive_choices_group_flow_snapshots_with_tied_timestamps(tmp_path, atlas):
    store = ArchiveStore(tmp_path / "archives.sqlite3", clock=lambda: 1234)
    scope = capture_group(Event())
    first = store.save_chart(scope, atlas["natal"])
    flow = store.save_chart(scope, apply_flow(atlas["natal"], year=2026))
    assert first != flow
    assert [row["id"] for row in store.chart_choices(scope)] == [flow]
    request = parse_request("1990-06-15 08:30 女")
    second = store.save_chart(scope, build_chart(request.birth, request.profile))
    assert [row["id"] for row in store.chart_choices(scope)] == [second, flow]
