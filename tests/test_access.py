import asyncio
from types import SimpleNamespace

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import HELP
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event

BIRTH = "2001-03-19 10:00 男 文字"


async def invoke(plugin, text, event=None):
    return [reply async for reply in plugin.ziwei(event or Event(), text)]


def test_unopened_group_stops_before_parsing_computing_or_rendering(monkeypatch):
    from astrbot_plugin_ziwei import main

    def forbidden(*args):
        raise AssertionError("unopened group must not access chart services")

    p = ZiweiPlugin(object(), {})
    monkeypatch.setattr(main, "parse_request", forbidden)
    monkeypatch.setattr(main, "build_chart", forbidden)
    assert "尚未开启" in asyncio.run(invoke(p, BIRTH))[0]
    assert "尚未开启" in asyncio.run(invoke(p, "malformed birth"))[0]
    assert p.pending == 0 and p.db == {}


@pytest.mark.parametrize(
    "role,admin", [("owner", False), ("admin", False), ("member", True)]
)
def test_open_then_member_use_and_close(role, admin):
    p = ZiweiPlugin(object(), {})
    operator = Event(role=role, admin=admin)

    async def run():
        assert "未开启" in (await invoke(p, "状态"))[0]
        assert "已开启" in (await invoke(p, "开启", operator))[0]
        assert "已开启" in (await invoke(p, "状态"))[0]
        assert "木三局" in "".join(await invoke(p, BIRTH))
        assert "已关闭" in (await invoke(p, "关闭", operator))[0]
        assert "尚未开启" in (await invoke(p, BIRTH))[0]

    asyncio.run(run())
    assert p.db == {}


def test_ordinary_member_cannot_open_or_close_and_admin_cannot_bypass_gate():
    p = ZiweiPlugin(object(), {})

    async def run():
        assert "仅群主" in (await invoke(p, "开启"))[0]
        assert p.db == {}
        assert "尚未开启" in (await invoke(p, BIRTH, Event(admin=True)))[0]
        await invoke(p, "开", Event(role="owner"))
        assert "仅群主" in (await invoke(p, "关闭"))[0]
        assert "已开启" in (await invoke(p, "状态"))[0]
        await invoke(p, "关", Event(role="admin"))
        assert p.db == {}

    asyncio.run(run())


@pytest.mark.parametrize("text", [BIRTH, "开启", "关闭", "状态"])
def test_private_chat_cannot_use_or_manage_whitelist(text):
    p = ZiweiPlugin(object(), {})
    reply = asyncio.run(invoke(p, text, Event(group="", admin=True)))
    assert "群聊" in reply[0] and p.db == {}


def test_help_available_without_group_authorization():
    p = ZiweiPlugin(object(), {})
    assert asyncio.run(invoke(p, "帮助", Event(group=""))) == [HELP]
    assert asyncio.run(invoke(p, "")) == [HELP]
    assert p.db == {}


def test_platform_and_group_isolation_with_all_members_sharing_one_authorization():
    p = ZiweiPlugin(object(), {})

    async def run():
        await invoke(p, "开启", Event(role="owner", platform="qq-one", group="20001"))
        assert "木三局" in "".join(await invoke(p, BIRTH, Event(user="other-member")))
        assert "尚未开启" in (await invoke(p, BIRTH, Event(group="20002")))[0]
        assert "尚未开启" in (await invoke(p, BIRTH, Event(platform="qq-two")))[0]
        assert len(p.db) == 1

    asyncio.run(run())


def test_persisted_open_and_close_survive_plugin_reload():
    context = SimpleNamespace(db={})
    p = ZiweiPlugin(context, {})
    asyncio.run(invoke(p, "开启", Event(role="owner")))
    asyncio.run(p.terminate())
    replacement = ZiweiPlugin(context, {})
    assert "木三局" in "".join(asyncio.run(invoke(replacement, BIRTH)))
    asyncio.run(invoke(replacement, "关闭", Event(role="admin")))
    final = ZiweiPlugin(context, {})
    assert "尚未开启" in asyncio.run(invoke(final, BIRTH))[0]


def test_two_group_concurrent_updates_are_not_lost():
    p = ZiweiPlugin(object(), {})

    async def run():
        replies = await asyncio.gather(
            invoke(p, "开启", Event(group="20001", role="owner")),
            invoke(p, "开启", Event(group="20002", role="owner")),
        )
        assert all("已开启" in reply[0] for reply in replies)
        assert await p.access.enabled(capture_group(Event(group="20001")))
        assert await p.access.enabled(capture_group(Event(group="20002")))

    asyncio.run(run())
    assert len(p.db) == 2


@pytest.mark.parametrize("value", ["true", 1, "false", False, None, {}, []])
def test_corrupted_or_nonboolean_storage_never_authorizes(value):
    p = ZiweiPlugin(object(), {})
    p.db[capture_group(Event()).key] = value
    assert "尚未开启" in asyncio.run(invoke(p, BIRTH))[0]


@pytest.mark.parametrize("operation", ["get", "put", "delete"])
def test_storage_failures_deny_and_do_not_report_success(operation, caplog):
    p = ZiweiPlugin(object(), {})

    async def broken(*args):
        raise OSError("database unavailable")

    setattr(p.access, operation, broken)
    command = {"get": BIRTH, "put": "开启", "delete": "关闭"}[operation]
    reply = asyncio.run(invoke(p, command, Event(role="owner")))
    assert "白名单暂不可用" in reply[0]
    assert "已开启" not in reply[0] and "已关闭" not in reply[0]
    assert BIRTH not in caplog.text and p.pending == 0


def test_missing_platform_id_denies_even_astrbot_admin():
    p = ZiweiPlugin(object(), {})
    assert (
        "无法识别" in asyncio.run(invoke(p, "开启", Event(platform="", admin=True)))[0]
    )
    assert p.db == {}


class LookupBot:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def call_action(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


@pytest.mark.parametrize(
    "response", [{"role": "owner"}, {"retcode": 0, "data": {"role": "admin"}}]
)
def test_missing_onebot_role_can_be_verified_by_member_api(response):
    p = ZiweiPlugin(object(), {})
    bot = LookupBot(response)
    assert "已开启" in asyncio.run(invoke(p, "开启", Event(role="", bot=bot)))[0]
    assert bot.calls == [
        {
            "action": "get_group_member_info",
            "group_id": 20001,
            "user_id": 10001,
            "no_cache": True,
        }
    ]


@pytest.mark.parametrize(
    "response,error",
    [
        ({"data": {"role": "member"}}, None),
        ({"data": {"role": "owner"}, "retcode": 1}, None),
        ({"data": {"role": "owner"}, "status": "failed"}, None),
        ({}, None),
        (None, TimeoutError()),
        (None, OSError()),
    ],
)
def test_failed_member_api_does_not_grant_access(response, error):
    p = ZiweiPlugin(object(), {})
    bot = LookupBot(response, error)
    assert "仅群主" in asyncio.run(invoke(p, "开启", Event(role="", bot=bot)))[0]
    assert p.db == {}


def test_known_member_does_not_fall_back_to_lookup():
    p = ZiweiPlugin(object(), {})
    bot = LookupBot({"role": "owner"})
    assert "仅群主" in asyncio.run(invoke(p, "开启", Event(bot=bot)))[0]
    assert bot.calls == []


def test_other_platform_needs_astrbot_admin_for_management():
    p = ZiweiPlugin(object(), {})
    event = Event(platform_name="telegram", role="owner")
    assert "仅群主" in asyncio.run(invoke(p, "开启", event))[0]
    event.admin = True
    assert "已开启" in asyncio.run(invoke(p, "开启", event))[0]
    event.admin = False
    assert "木三局" in "".join(asyncio.run(invoke(p, BIRTH, event)))


def test_management_command_targets_only_current_group():
    p = ZiweiPlugin(object(), {})
    assert "尚未开启" in asyncio.run(invoke(p, "开启 群=20002", Event(role="owner")))[0]
    assert p.db == {}
