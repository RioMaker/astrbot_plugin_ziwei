"""Local AstrBot adapter doubles; not a real platform integration."""

import copy
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

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
    filter=SimpleNamespace(command=command, llm_tool=llm_tool),
)
module("astrbot.api.message_components", Image=Image)
module(
    "astrbot.api.star", Star=Star, Context=object, register=lambda *a: lambda cls: cls
)
module("astrbot.core")
module("astrbot.core.star")
module("astrbot.core.star.filter")
module("astrbot.core.star.filter.command", GreedyStr=GreedyStr)


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
    ):
        self.platform, self.group, self.user = platform, group, user
        self.platform_name, self.admin, self.bot = platform_name, admin, bot
        self.message_obj = SimpleNamespace(raw_message={"sender": {"role": role}})
        self.unified_msg_origin = (
            f"{platform}:GroupMessage:{group}"
            if group
            else f"{platform}:FriendMessage:{user}"
        )

    def get_platform_id(self):
        return self.platform

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

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain
