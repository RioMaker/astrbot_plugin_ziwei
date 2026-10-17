"""Configurable AI quotas with persistent stores and local AstrBot doubles."""

import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.ai_limits import QuotaExceeded, QuotaPolicy
from astrbot_plugin_ziwei.archive_store import ArchiveStore
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .test_ai_limits import usage
from .test_llm_tools import Context, allow, invoke_command
from .test_readings import answer, natal
from .test_rectification_service import DEMO, administrator, create_session

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
    for key, field in CONFIG_FIELDS.items():
        assert schema[key]["type"] == "int"
        assert schema[key]["default"] == getattr(policy, field)
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


@pytest.mark.parametrize("ordinary_first", [True, False])
def test_plugin_custom_natal_limit_is_shared_with_ai_rectification_batches(
    ordinary_first,
):
    plugin = ZiweiPlugin(
        Context(), {"ai_quota_window_seconds": 75, "ai_natal_limit": 1}
    )
    now = [1000.0]
    plugin.archive.clock = lambda: now[0]

    async def ordinary():
        return json.loads(await plugin.ziwei_paipan(administrator(), DEMO_BIRTH))

    async def candidates():
        return json.loads(
            await plugin.ziwei_rectification_create(
                administrator(text="请按虚构资料生成定盘候选"), DEMO
            )
        )

    async def run():
        await allow(plugin)
        first, second = (
            (ordinary, candidates) if ordinary_first else (candidates, ordinary)
        )
        assert (await first())["status"] == "ok"
        blocked = await second()
        assert blocked["status"] == "rate_limited"
        assert blocked["quota"] == "natal" and blocked["limit"] == 1
        assert blocked["window_seconds"] == blocked["retry_after_seconds"] == 75
        assert usage(plugin, "natal") == 1
        replies = await invoke_command(plugin, DEMO_BIRTH + " 文字")
        assert any("命宫" in str(reply) for reply in replies)
        assert usage(plugin, "natal") == 1
        now[0] = 1075
        assert (await second())["status"] == "ok"
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("suffix", [" 运限=0", " 运限=1"])
def test_plugin_custom_decade_limit_includes_childhood(suffix):
    plugin = ZiweiPlugin(
        Context(), {"ai_quota_window_seconds": 90, "ai_decade_limit": 1}
    )
    plugin.archive.clock = lambda: 1000

    async def run():
        await allow(plugin)
        first = json.loads(await plugin.ziwei_paipan(Event(), DEMO_BIRTH + suffix))
        assert first["status"] == "ok"
        blocked = json.loads(await plugin.ziwei_paipan(Event(), DEMO_BIRTH + suffix))
        assert blocked["status"] == "rate_limited" and blocked["quota"] == "decade"
        assert blocked["limit"] == 1 and blocked["window_seconds"] == 90
        assert blocked["retry_after_seconds"] == 90
        await plugin.terminate()

    asyncio.run(run())


def test_custom_limits_are_shown_in_help_and_llm_requests():
    plugin = ZiweiPlugin(
        Context(),
        {
            "ai_quota_window_seconds": 300,
            "ai_question_limit": 4,
            "ai_natal_limit": 5,
            "ai_decade_limit": 7,
        },
    )
    expected = "滚动300秒内解盘提问4次、生年排盘5张、大运排盘（含童限）7张"

    async def run():
        for args in ("帮助", "定盘 帮助"):
            replies = await invoke_command(plugin, args)
            assert len(replies) == 1 and expected in replies[0]
        await allow(plugin)
        for service in (plugin.readings, plugin.rectifications):
            request = SimpleNamespace(
                system_prompt="existing", extra_user_content_parts=[]
            )
            await service.on_request(administrator(), request)
            assert request.system_prompt.startswith("existing")
            assert expected in request.system_prompt
            assert "每小时6次" not in request.system_prompt
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("ordinary_first", [True, False])
def test_plugin_custom_questions_are_shared_across_reading_and_rectification(
    ordinary_first,
):
    config = {"ai_quota_window_seconds": 120, "ai_question_limit": 1}
    plugin = ZiweiPlugin(Context(), config)
    plugin.archive.clock = lambda: 1000
    description = QuotaPolicy.from_config(config).description()
    assert description in plugin.readings.policy
    assert description in plugin.rectifications.policy

    async def run():
        row = await create_session(plugin)
        sid = row["session_id"]
        plugin.archive.save_chart(capture_group(administrator()), natal())
        first_event = administrator(text="请比较虚构资料的事业情况")
        first_event.message_obj.message_id = "first-question"
        if ordinary_first:
            result = json.loads(
                await plugin.ziwei_analysis_context(first_event, topic="career")
            )
            blocked = json.loads(
                await plugin.ziwei_rectification_context(
                    administrator(text="请比较候选"), sid
                )
            )
            repeated = json.loads(
                await plugin.ziwei_analysis_context(first_event, topic="career")
            )
        else:
            result = json.loads(
                await plugin.ziwei_rectification_context(first_event, sid)
            )
            blocked = json.loads(
                await plugin.ziwei_analysis_context(
                    administrator(text="请分析当前普通命盘"), topic="career"
                )
            )
            repeated = json.loads(
                await plugin.ziwei_rectification_context(first_event, sid)
            )
        assert result["status"] == repeated["status"] == "ok"
        assert description in result["response_guidance"]
        assert blocked["status"] == "rate_limited" and blocked["quota"] == "question"
        assert blocked["limit"] == 1 and blocked["window_seconds"] == 120
        assert blocked["retry_after_seconds"] == 120
        assert usage(plugin, "question") == 1
        await answer(plugin, first_event, "虚构演示：两组资料仍需更多反馈。")
        feedback = administrator(text="虚构反馈：与上述情况不符。")
        if ordinary_first:
            feedback_result = json.loads(
                await plugin.ziwei_continue_reading(
                    feedback, result["reading_id"], "feedback"
                )
            )
            assert feedback_result["status"] == "ok"
            for response in (
                await plugin.ziwei_query_knowledge(Event(), result["reading_id"]),
                await plugin.ziwei_reference_cases(Event(), result["reading_id"]),
                await plugin.ziwei_reading_history(Event()),
            ):
                assert json.loads(response)["status"] == "rate_limited"
        else:
            feedback_result = json.loads(
                await plugin.ziwei_rectification_feedback(feedback, sid)
            )
            assert feedback_result["status"] == "recorded"
        assert usage(plugin, "question") == 1
        assert (
            json.loads(await plugin.ziwei_rectification_get(administrator(), sid))[
                "status"
            ]
            == "ok"
        )
        assert json.loads(await plugin.ziwei_get_chart(Event()))["status"] == "ok"
        await plugin.terminate()

    asyncio.run(run())
