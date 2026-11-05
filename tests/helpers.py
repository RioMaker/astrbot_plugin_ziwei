"""Shared command doubles; no live AstrBot or model calls."""

from astrbot_plugin_ziwei.access import capture_group

from .conftest import Event


class Context:
    def __init__(self, failure=False, accepted=True):
        self.db, self.sent = {}, []
        self.failure, self.accepted = failure, accepted

    async def send_message(self, umo, chain):
        if self.failure:
            raise OSError("send failed")
        self.sent.append((umo, chain))
        return self.accepted


async def allow(plugin, event=None):
    await plugin.access.set_enabled(capture_group(event or Event()), True)


async def invoke_command(plugin, args, event=None):
    return [reply async for reply in plugin.ziwei(event or Event(), args)]
