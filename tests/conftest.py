"""Local AstrBot adapter doubles; not a real platform integration."""

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


class Star:
    def __init__(self, context):
        self.context = context


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
    filter=SimpleNamespace(command=command),
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
    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain
