import asyncio
import json

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event
from .test_llm_tools import Context, allow, invoke_command


def test_reload_during_authorization_read_never_represents_data():
    p = ZiweiPlugin(Context(), {})
    original = p.access.get

    async def run():
        await allow(p)

        async def terminating(*args):
            enabled = await original(*args)
            await p.terminate()
            return enabled

        p.access.get = terminating
        result = json.loads(await p.ziwei_paipan(Event(), DEMO_BIRTH))
        assert result["status"] == "denied" and "data" not in result
        assert not p.chart_cache.entries and p.pending == 0

    asyncio.run(run())


def test_group_close_during_image_delivery_does_not_return_cached_birth():
    class ClosingContext(Context):
        async def send_message(self, umo, chain):
            await invoke_command(self.plugin, "关闭", Event(role="owner"))
            return True

    context = ClosingContext()
    p = context.plugin = ZiweiPlugin(context, {})

    class FakeRenderer:
        def render(self, chart):
            return b"png"

    p.renderer = FakeRenderer()

    async def run():
        await allow(p)
        result = json.loads(await p.ziwei_paipan(Event(), DEMO_BIRTH, True))
        assert result["status"] == "denied" and "data" not in result
        assert not p.chart_cache.entries

    asyncio.run(run())


def test_event_mutation_does_not_change_cache_owner_or_delivery_route():
    event = Event()
    original_umo = event.unified_msg_origin
    original_scope = capture_group(event)
    context = Context()
    p = ZiweiPlugin(context, {})
    original = p.access.get

    class FakeRenderer:
        def render(self, chart):
            return b"png"

    p.renderer = FakeRenderer()

    async def mutate(*args):
        event.platform = "other-platform"
        event.group = "other-group"
        event.user = "other-user"
        event.unified_msg_origin = "other-platform:GroupMessage:other-group"
        return await original(*args)

    async def run():
        await allow(p)
        p.access.get = mutate
        result = json.loads(await p.ziwei_paipan(event, DEMO_BIRTH, True))
        assert result["status"] == "ok"
        assert p.chart_cache.get(original_scope, result["chart_id"]) is not None
        assert context.sent[0][0] == original_umo

    asyncio.run(run())
