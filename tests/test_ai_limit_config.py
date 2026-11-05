"""Configurable AI quotas with persistent stores and local AstrBot doubles."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.ai_limits import QuotaExceeded, QuotaPolicy
from astrbot_plugin_ziwei.archive_store import ArchiveStore

from .conftest import Event

CONFIG_FIELDS = {
    "ai_quota_window_seconds": "window_seconds",
    "ai_question_limit": "question_limit",
    "ai_natal_limit": "natal_limit",
    "ai_decade_limit": "decade_limit",
}


def test_schema_and_policy_defaults_preserve_existing_limits():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )
    policy = QuotaPolicy.from_config({})
    assert policy == QuotaPolicy(3600, 6, 2, 3)
    assert not set(CONFIG_FIELDS) & set(schema)
    assert "summary_provider_id" not in schema
    with pytest.raises(FrozenInstanceError):
        policy.question_limit = 99


@pytest.mark.parametrize("key", CONFIG_FIELDS)
@pytest.mark.parametrize("value", [True, False, 1.0, "1", None, 0, -1])
def test_config_rejects_values_that_are_not_positive_integers(key, value):
    with pytest.raises(ValueError):
        QuotaPolicy.from_config({key: value})


def test_window_has_explicit_one_day_maximum():
    assert QuotaPolicy.from_config({"ai_quota_window_seconds": 1}).window_seconds == 1
    assert (
        QuotaPolicy.from_config({"ai_quota_window_seconds": 86400}).window_seconds
        == 86400
    )
    with pytest.raises(ValueError):
        QuotaPolicy.from_config({"ai_quota_window_seconds": 86401})


def test_store_policies_do_not_change_another_instance_or_defaults(tmp_path):
    scope = capture_group(Event())
    strict = ArchiveStore(
        tmp_path / "strict.sqlite3",
        clock=lambda: 1000,
        ai_policy=QuotaPolicy(window_seconds=60, question_limit=1),
    )
    relaxed = ArchiveStore(
        tmp_path / "relaxed.sqlite3",
        clock=lambda: 1000,
        ai_policy=QuotaPolicy(window_seconds=300, question_limit=2),
    )
    default = ArchiveStore(tmp_path / "default.sqlite3", clock=lambda: 1000)
    strict.charge_question(scope, "strict-first")
    with pytest.raises(QuotaExceeded) as error:
        strict.charge_question(scope, "strict-second")
    relaxed.charge_question(scope, "relaxed-first")
    relaxed.charge_question(scope, "relaxed-second")
    for index in range(6):
        default.charge_question(scope, f"default-{index}")
    assert json.loads(error.value.payload())["window_seconds"] == 60
    assert json.loads(error.value.payload())["limit"] == 1
    assert strict.ai_policy.question_limit == 1
    assert relaxed.ai_policy.question_limit == 2
    assert default.ai_policy == QuotaPolicy()


def test_configured_window_releases_at_exact_boundary_and_failure_does_not_extend_it(
    tmp_path,
):
    now = [1000.0]
    store = ArchiveStore(
        tmp_path / "archive.sqlite3",
        clock=lambda: now[0],
        ai_policy=QuotaPolicy(window_seconds=60, question_limit=1),
    )
    scope = capture_group(Event())
    store.charge_question(scope, "first")
    store.charge_question(scope, "first")
    now[0] = 1059.25
    with pytest.raises(QuotaExceeded) as error:
        store.charge_question(scope, "blocked")
    result = json.loads(error.value.payload())
    assert result["status"] == "rate_limited"
    assert result["quota"] == "question"
    assert result["window_seconds"] == 60
    assert result["limit"] == 1
    assert result["retry_after_seconds"] == 1
    assert "60" in result["message"] and "一小时" not in result["message"]
    assert store.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 1
    now[0] = 1060
    store.charge_question(scope, "second")
    with pytest.raises(QuotaExceeded) as error:
        store.charge_question(scope, "third")
    assert json.loads(error.value.payload())["retry_after_seconds"] == 60


def test_reload_shorter_then_longer_window_retains_previous_usage(tmp_path):
    path = tmp_path / "archive.sqlite3"
    now = [1000.0]
    scope = capture_group(Event())
    long = QuotaPolicy(window_seconds=600, question_limit=2)
    store = ArchiveStore(path, clock=lambda: now[0], ai_policy=long)
    store.charge_question(scope, "first")
    now[0] = 1050
    store.charge_question(scope, "second")
    store.close()

    now[0] = 1100
    shorter = ArchiveStore(
        path,
        clock=lambda: now[0],
        ai_policy=QuotaPolicy(window_seconds=60, question_limit=2),
    )
    shorter.charge_question(scope, "third")
    assert shorter.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 3
    shorter.close()

    restored = ArchiveStore(path, clock=lambda: now[0], ai_policy=long)
    with pytest.raises(QuotaExceeded) as error:
        restored.charge_question(scope, "fourth")
    assert json.loads(error.value.payload())["retry_after_seconds"] == 550
    now[0] = 1649.5
    with pytest.raises(QuotaExceeded) as error:
        restored.charge_question(scope, "fourth")
    assert json.loads(error.value.payload())["retry_after_seconds"] == 1
    now[0] = 1650
    restored.charge_question(scope, "fourth")


def test_same_message_after_short_window_expires_preserves_original_charge(tmp_path):
    now = [1000.0]
    store = ArchiveStore(
        tmp_path / "archive.sqlite3",
        clock=lambda: now[0],
        ai_policy=QuotaPolicy(window_seconds=1, question_limit=1),
    )
    scope = capture_group(Event())
    store.charge_question(scope, "message-one")
    now[0] = 1001
    store.charge_question(scope, "message-one")
    assert store.db.execute("SELECT created FROM ai_usage").fetchone()[0] == 1000
    now[0] = 1001.1
    store.charge_question(scope, "message-two")
    assert store.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 2


def test_reducing_limit_waits_for_enough_existing_usage_to_expire(tmp_path):
    path = tmp_path / "archive.sqlite3"
    now = [1000.0]
    scope = capture_group(Event())
    store = ArchiveStore(
        path,
        clock=lambda: now[0],
        ai_policy=QuotaPolicy(window_seconds=100, question_limit=4),
    )
    for index in range(4):
        now[0] = 1000 + index * 10
        store.charge_question(scope, f"old-{index}")
    store.close()
    now[0] = 1040
    restored = ArchiveStore(
        path,
        clock=lambda: now[0],
        ai_policy=QuotaPolicy(window_seconds=100, question_limit=2),
    )
    with pytest.raises(QuotaExceeded) as error:
        restored.charge_question(scope, "new")
    assert json.loads(error.value.payload())["retry_after_seconds"] == 80
    now[0] = 1120
    restored.charge_question(scope, "new")


def test_old_records_are_removed_only_after_maximum_supported_window(tmp_path):
    now = [1000.0]
    store = ArchiveStore(
        tmp_path / "archive.sqlite3",
        clock=lambda: now[0],
        ai_policy=QuotaPolicy(window_seconds=1, question_limit=1),
    )
    scope = capture_group(Event())
    store.charge_question(scope, "first")
    now[0] = 1001
    store.charge_question(scope, "second")
    assert store.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 2
    now[0] = 87400
    store.charge_question(scope, "third")
    assert store.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 2
    assert store.db.execute("SELECT min(created) FROM ai_usage").fetchone()[0] == 1001
