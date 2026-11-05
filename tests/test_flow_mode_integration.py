"""Command, archive and image integration for all selectable chart modes."""

import asyncio
from copy import deepcopy

import pytest

from astrbot_plugin_ziwei.access import capture_group
from astrbot_plugin_ziwei.commands import DEMO_BIRTH
from astrbot_plugin_ziwei.main import ZiweiPlugin
from astrbot_plugin_ziwei.modern_renderer import star_marks

from .conftest import Event
from .helpers import Context, allow, invoke_command


class RecordingRenderer:
    def __init__(self):
        self.calls = []

    def render(self, chart, theme="night"):
        self.calls.append((deepcopy(chart), theme))
        return b"mode-preview"


@pytest.mark.parametrize("index,label", [(0, "童限"), (1, "第1大运"), (12, "第12大运")])
def test_manual_decade_text_and_archive_have_no_fabricated_year(index, label):
    plugin = ZiweiPlugin(Context(), {"output_mode": "text"})
    plugin.renderer = RecordingRenderer()

    async def run():
        await allow(plugin)
        replies = await invoke_command(plugin, f"{DEMO_BIRTH} 运限={index}")
        text = "".join(replies)
        assert f"运限：{label}" in text
        assert "大限命宫：" in text and "生年四化：" in text
        assert "目标：" not in text and "流年命宫：" not in text
        cached = plugin.chart_cache.get(capture_group(Event()))
        data = cached.chart
        assert data["flow"]["kind"] == "decade"
        assert data["flow"]["year"] is None and data["flow"]["target"] is None
        assert data["flow"]["decade"]["index"] == index
        assert set(data["flow"]["layers"]) == {"大限"}
        assert all(set(p["flow_names"]) == {"大限"} for p in data["palaces"])
        assert (
            plugin.archive.get_chart(capture_group(Event()), cached.chart_id)["chart"]
            == data
        )

    asyncio.run(run())
    assert plugin.renderer.calls == [] and plugin.pending == 0


@pytest.mark.parametrize(
    "option,kind,expected_layers,visible_layers",
    [
        ("运限=1", "decade", {"大限"}, {"生年", "大限"}),
        ("流年=2026", "annual", {"大限", "小限", "流年"}, {"生年", "大限", "流年"}),
        (
            "流盘=2026-10-06 流时=10:00",
            "annual",
            {"大限", "小限", "流年", "流月", "流日", "流时"},
            {"生年", "大限", "流年"},
        ),
    ],
)
@pytest.mark.parametrize("output", ["image", "text"])
def test_command_modes_reach_renderer_and_preserve_separate_hua_layers(
    option, kind, expected_layers, visible_layers, output
):
    plugin = ZiweiPlugin(Context(), {"image_theme": "day", "output_mode": output})
    plugin.renderer = RecordingRenderer()
    event = Event()

    async def run():
        await allow(plugin)
        replies = await invoke_command(
            plugin, f"{DEMO_BIRTH} {option} 主题=夜间", event
        )
        cached = plugin.chart_cache.get(capture_group(event))
        data = cached.chart
        assert data["flow"]["kind"] == kind
        assert set(data["flow"]["layers"]) == expected_layers
        assert all(set(p["flow_names"]) == expected_layers for p in data["palaces"])
        marks = [
            mark for p in data["palaces"] for s in p["stars"] for mark in star_marks(s)
        ]
        assert {mark["source"] for mark in marks} == visible_layers
        for layer in visible_layers:
            assert sum(mark["source"] == layer for mark in marks) == 4
        assert all(mark["label"] in {"禄", "权", "科", "忌"} for mark in marks)
        assert (
            plugin.archive.get_chart(capture_group(event), cached.chart_id)["chart"]
            == data
        )
        if output == "image":
            assert len(plugin.renderer.calls) == 1
            chart, theme = plugin.renderer.calls[0]
            assert theme == "night" and chart == data
            assert replies[0][0].data == b"mode-preview"
        else:
            assert plugin.renderer.calls == [] and "紫微斗数排盘" in replies[0]
        assert (
            plugin.archive.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0]
            == 0
        )

    asyncio.run(run())
    assert event.stopped and event.call_llm is True
    assert plugin.pending == 0


@pytest.mark.parametrize("option", ["运限=0", "运限=1", "流年=2026"])
def test_selectable_modes_do_not_bypass_default_group_whitelist(option):
    plugin = ZiweiPlugin(Context(), {})
    plugin.renderer = RecordingRenderer()

    async def forbidden(_request):
        raise AssertionError("unauthorized chart must not be computed")

    plugin._compute_chart = forbidden
    assert (
        "尚未开启" in asyncio.run(invoke_command(plugin, f"{DEMO_BIRTH} {option}"))[0]
    )
    assert not plugin.chart_cache.entries and plugin.renderer.calls == []
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
        await invoke_command(plugin, f"{DEMO_BIRTH} 流年=2026")
        snapshot = deepcopy(plugin.chart_cache.entries)
        count = len(plugin.archive.list_charts(capture_group(Event())))
        replies = await invoke_command(plugin, f"{DEMO_BIRTH} {options}")
        assert "请选择其中一种" in replies[0] or "重复" in replies[0]
        assert plugin.chart_cache.entries == snapshot
        assert len(plugin.archive.list_charts(capture_group(Event()))) == count

    asyncio.run(run())
    assert plugin.renderer.calls == [] and plugin.pending == 0
