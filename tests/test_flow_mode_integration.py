"""Local command, LLM, cache and image integration for selectable chart modes."""

import asyncio
import json
from copy import deepcopy

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.main import ZiweiPlugin
from astrbot_plugin_ziwei.modern_renderer import star_marks

from .conftest import Event
from .test_llm_tools import Context, allow, invoke_command


class RecordingRenderer:
    def __init__(self):
        self.calls = []

    def render(self, chart, theme="day"):
        self.calls.append((deepcopy(chart), theme))
        return b"mode-preview"


def exported_stars(data):
    return [star for palace in data["palaces"] for star in palace["stars"]]


@pytest.mark.parametrize("index,label", [(0, "童限"), (1, "第1大运")])
def test_manual_decade_text_is_cached_and_readable_by_llm(index, label):
    plugin = ZiweiPlugin(Context(), {"output_mode": "text"})
    plugin.renderer = RecordingRenderer()

    async def run():
        await allow(plugin)
        replies = await invoke_command(plugin, f"{DEMO_BIRTH} 运限={index}")
        text = "".join(replies)
        assert f"运限：{label}" in text
        assert "大限命宫：" in text and "生年四化：" in text
        assert "目标：" not in text and "流年命宫：" not in text
        result = json.loads(await plugin.ziwei_get_chart(Event()))
        assert result["status"] == "ok"
        data = result["data"]
        assert data["flow"]["kind"] == "decade"
        assert data["flow"]["year"] is None and data["flow"]["target"] is None
        assert data["flow"]["decade"]["index"] == index
        assert set(data["flow"]["layers"]) == {"大限"}
        assert all(set(p["flow_names"]) == {"大限"} for p in data["palaces"])
        assert all(
            set(s["hua"]) <= {"生年", "命宫", "日干", "大限"}
            for s in exported_stars(data)
        )
        assert "flow.kind=decade" in result["reading_notes"]["mode"]
        assert len(plugin.chart_cache.entries) == 1

    asyncio.run(run())
    assert plugin.renderer.calls == [] and plugin.context.sent == []
    assert plugin.pending == 0


@pytest.mark.parametrize(
    "option,kind,expected_layers,visible_layers",
    [
        ("运限=1", "decade", {"大限"}, {"生年", "大限"}),
        ("流年=2026", "annual", {"大限", "小限", "流年"}, {"生年", "大限", "流年"}),
    ],
)
@pytest.mark.parametrize("send_image", [False, True])
def test_llm_chart_modes_reach_renderer_and_preserve_separate_hua_layers(
    option, kind, expected_layers, visible_layers, send_image
):
    plugin = ZiweiPlugin(Context(), {"image_theme": "day"})
    plugin.renderer = RecordingRenderer()
    event = Event()

    async def run():
        await allow(plugin)
        result = json.loads(
            await plugin.ziwei_paipan(
                event, f"{DEMO_BIRTH} {option} 主题=夜间", send_image
            )
        )
        assert result["status"] == "ok"
        assert result["image_status"] == ("sent" if send_image else "not_requested")
        data = result["data"]
        assert data["flow"]["kind"] == kind
        assert set(data["flow"]["layers"]) == expected_layers
        assert all(set(p["flow_names"]) == expected_layers for p in data["palaces"])
        stars = exported_stars(data)
        marks = [mark for star in stars for mark in star_marks(star)]
        assert {mark["source"] for mark in marks} == visible_layers
        for layer in visible_layers:
            assert sum(mark["source"] == layer for mark in marks) == 4
        assert all(mark["label"] in {"禄", "权", "科", "忌"} for mark in marks)
        assert {mark["row"] for mark in marks} == (
            {0, 1} if kind == "decade" else {0, 1, 2}
        )
        if kind == "decade":
            assert data["flow"]["year"] is None
            assert data["flow"]["decade"]["index"] == 1
        else:
            assert data["flow"]["year"] == 2026
        cached = json.loads(await plugin.ziwei_get_chart(event, result["chart_id"]))
        assert cached["status"] == "ok" and cached["data"] == data
        if send_image:
            chart, theme = plugin.renderer.calls[0]
            assert len(plugin.renderer.calls) == 1 and theme == "night"
            assert chart["flow"] == data["flow"]
            assert len(plugin.context.sent) == 1
            origin, message = plugin.context.sent[0]
            assert origin == event.unified_msg_origin
            assert message.chain[0].data == b"mode-preview"
        else:
            assert plugin.renderer.calls == [] and plugin.context.sent == []

    asyncio.run(run())
    assert not getattr(event, "stopped", False)
    assert plugin.pending == 0


@pytest.mark.parametrize("option", ["运限=0", "运限=1", "流年=2026"])
def test_selectable_modes_do_not_bypass_default_group_whitelist(option):
    plugin = ZiweiPlugin(Context(), {})
    plugin.renderer = RecordingRenderer()

    async def forbidden(_request):
        raise AssertionError("unauthorized chart must not be computed")

    plugin._compute_chart = forbidden

    async def run():
        text = f"{DEMO_BIRTH} {option}"
        assert "尚未开启" in (await invoke_command(plugin, text))[0]
        result = json.loads(await plugin.ziwei_paipan(Event(), text, True))
        assert result["status"] == "denied" and "data" not in result
        assert not plugin.chart_cache.entries

    asyncio.run(run())
    assert plugin.renderer.calls == [] and plugin.context.sent == []
    assert plugin.pending == 0


@pytest.mark.parametrize(
    "options",
    [
        "运限=1 流年=2026",
        "运限=0 流盘=2026-10-06",
        "大运=1 流时=10:00",
        "运限=1 大限=2",
    ],
)
def test_conflicting_mode_input_cannot_write_or_replace_cached_chart(options):
    plugin = ZiweiPlugin(Context(), {"output_mode": "text"})
    plugin.renderer = RecordingRenderer()

    async def run():
        await allow(plugin)
        valid = json.loads(
            await plugin.ziwei_paipan(Event(), f"{DEMO_BIRTH} 流年=2026")
        )
        assert valid["status"] == "ok"
        snapshot = deepcopy(plugin.chart_cache.entries)

        def forbidden_put(*_args):
            raise AssertionError("invalid input must not touch the cache")

        plugin.chart_cache.put = forbidden_put
        replies = await invoke_command(plugin, f"{DEMO_BIRTH} {options}")
        assert "请选择其中一种" in replies[0] or "重复" in replies[0]
        rejected = json.loads(
            await plugin.ziwei_paipan(Event(), f"{DEMO_BIRTH} {options}", True)
        )
        assert rejected["status"] == "invalid_input"
        assert plugin.chart_cache.entries == snapshot
        assert (
            plugin.chart_cache.get(capture_group(Event())).chart_id == valid["chart_id"]
        )

    asyncio.run(run())
    assert plugin.renderer.calls == [] and plugin.context.sent == []
    assert plugin.pending == 0
