"""Local AstrBot adapter doubles; not a real platform integration."""

import copy
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def module(name, **attributes):
    item = ModuleType(name)
    item.__dict__.update(attributes)
    sys.modules[name] = item


def command(name, **kwargs):
    def decorate(fn):
        fn.command_metadata = {"name": name, **kwargs}
        return fn

    return decorate


def llm_tool(name):
    def decorate(fn):
        fn.tool_metadata = {"name": name}
        return fn

    return decorate


def custom_filter(filter_class, **kwargs):
    def decorate(fn):
        fn.custom_filter_class = filter_class
        return fn

    return decorate


class CustomFilter:
    def __init__(self, raise_error=True, **kwargs):
        self.raise_error = raise_error


class MessageChain:
    def __init__(self, chain):
        self.chain = chain


class Star:
    def __init__(self, context):
        self.context = context
        self.db = getattr(context, "db", {})

    async def get_kv_data(self, key, default=None):
        return copy.deepcopy(self.db.get(key, default))

    async def put_kv_data(self, key, value):
        self.db[key] = copy.deepcopy(value)

    async def delete_kv_data(self, key):
        self.db.pop(key, None)


class Image:
    @staticmethod
    def fromBytes(data):
        return SimpleNamespace(type="image", data=data)


class GreedyStr(str):
    pass


module("astrbot")
module("astrbot.api", AstrBotConfig=dict, logger=logging.getLogger("ziwei-tests"))
module(
    "astrbot.api.event",
    AstrMessageEvent=object,
    MessageChain=MessageChain,
    filter=SimpleNamespace(
        command=command,
        custom_filter=custom_filter,
        CustomFilter=CustomFilter,
        llm_tool=llm_tool,
        on_llm_request=lambda: lambda fn: fn,
        after_message_sent=lambda: lambda fn: fn,
    ),
)
module("astrbot.api.message_components", Image=Image)
module(
    "astrbot.api.star", Star=Star, Context=object, register=lambda *a: lambda cls: cls
)
module("astrbot.core")
module("astrbot.core.star")
module("astrbot.core.star.filter")
module("astrbot.core.star.filter.command", GreedyStr=GreedyStr)
module("astrbot.core.utils")
module("astrbot.core.utils.astrbot_path", get_astrbot_data_path=lambda: _data_path)
module("astrbot.core.agent")


class TextPart:
    def __init__(self, text):
        self.text = text

    def mark_as_temp(self):
        self.temporary = True
        return self


module("astrbot.core.agent.message", TextPart=TextPart)


@pytest.fixture(autouse=True)
def isolated_archive(tmp_path, monkeypatch):
    global _data_path
    _data_path = tmp_path
    from astrbot_plugin_ziwei.archive_store import ArchiveStore

    stores = []
    original = ArchiveStore.__init__

    def track(self, *args, **kwargs):
        original(self, *args, **kwargs)
        stores.append(self)

    monkeypatch.setattr(ArchiveStore, "__init__", track)
    yield
    for store in stores:
        store.close()


class Event:
    def __init__(
        self,
        *,
        platform="qq-one",
        group="20001",
        user="10001",
        role="member",
        admin=False,
        platform_name="aiocqhttp",
        bot=None,
        text="",
        wake=True,
    ):
        self.platform, self.group, self.user = platform, group, user
        self.message_str = text
        self.is_at_or_wake_command = wake
        self.call_llm = False
        self.result = None
        self.platform_name, self.admin, self.bot = platform_name, admin, bot
        self.message_obj = SimpleNamespace(raw_message={"sender": {"role": role}})
        self.unified_msg_origin = (
            f"{platform}:GroupMessage:{group}"
            if group
            else f"{platform}:FriendMessage:{user}"
        )

    def get_platform_id(self):
        return self.platform

    def get_message_str(self):
        return self.message_str

    def get_result(self):
        return self.result

    async def send(self, message):
        self.result = message

    async def send_streaming(self, generator, *args, **kwargs):
        async for chunk in generator:
            await self.send(chunk)

    def get_platform_name(self):
        return self.platform_name

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.user

    def is_admin(self):
        return self.admin

    def stop_event(self):
        self.stopped = True

    def should_call_llm(self, call_llm):
        self.call_llm = call_llm

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain
