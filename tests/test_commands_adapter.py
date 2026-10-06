import asyncio

import pytest

from astrbot_plugin_ziwei.commands import HELP, parse_request
from astrbot_plugin_ziwei.main import ZiweiPlugin

from .conftest import Event


def test_explicit_clock_and_options():
    r = parse_request("农历 2023-闰02-16 巳时 女 真太阳时=关 闰月=本月 文字")
    assert r.birth.calendar == "lunar" and r.birth.leap
    assert (r.birth.hour, r.birth.minute) == (10, 0)
    assert r.birth.true_solar is False
    assert r.profile.leap_month_rule == "current" and r.output == "text"
    a = parse_request("20010319 10:00:30 male 经度=116.4 流年=2026")
    assert a.birth.sex == "男" and a.birth.second == 30
    assert a.flow_year == 2026 and a.birth.longitude == 116.4


@pytest.mark.parametrize(
    "suffix",
    [
        "时区=8 时区=0",
        "未知=1",
        "真太阳时=maybe",
        "闰月=0",
        "子时=0",
        "文字 图片",
        "输出=json",
        "流时=10:00",
        "流年=2026 流盘=2026-10-06",
        "流年=2",
        "流盘=2026-02-30",
        "经度=abc",
        "测试",
        '经度="120',
    ],
)
def test_bad_options(suffix):
    with pytest.raises(ValueError):
        parse_request("2001-03-19 10:00 男 " + suffix)


def test_flow_clock_uses_same_correction_policy():
    raw = parse_request(
        "2001-03-19 10:00 男 流盘=2026-10-06 流时=00:05 经度=80 真太阳时=关"
    )
    solar = parse_request("2001-03-19 10:00 男 流盘=2026-10-06 流时=00:05 经度=80")
    assert raw.flow_target.day == 6 and solar.flow_target.day == 5


@pytest.mark.parametrize(
    "config",
    [
        {"kuiyue": 1.0},
        {"ren": True},
        {"minor_stars": "true"},
        {"true_solar": "true"},
        {"output_mode": "broken"},
    ],
)
def test_bad_configuration_fails_at_load(config):
    with pytest.raises(ValueError):
        ZiweiPlugin(object(), config)


async def collect(plugin, args):
    return [result async for result in plugin.ziwei(Event(), args)]


def test_help_text_error_and_fallback():
    p = ZiweiPlugin(object(), {"font_path": "/missing/ziwei-font.ttf"})
    assert asyncio.run(collect(p, "")) == [HELP]
    error = asyncio.run(collect(p, "2001-02-29 10:00 男"))
    assert "日期不存在" in error[0]
    results = asyncio.run(collect(p, "2001-03-19 10:00 男"))
    assert results[0].startswith("图片暂不可用")
    assert "木三局" in "".join(results) and "巨门禄" in "".join(results)
    assert all(len(part) <= 2800 for part in results)
    assert p.pending == 0


def test_render_failure_and_unexpected_error_are_recoverable(caplog):
    class BrokenRenderer:
        def render(self, chart):
            raise OSError("cannot render")

    p = ZiweiPlugin(object(), {"output_mode": "image"})
    p.renderer = BrokenRenderer()
    assert "文字盘" in asyncio.run(collect(p, "2001-03-19 10:00 男"))[0]
    assert "2001-03-19" not in caplog.text
    assert p.pending == 0


def test_image_output_and_reload():
    class FakeRenderer:
        def render(self, chart):
            return b"png"

    p = ZiweiPlugin(object(), {})
    p.renderer = FakeRenderer()
    result = asyncio.run(collect(p, "2001-03-19 10:00 男"))
    assert result[0][0].type == "image" and result[0][0].data == b"png"
    asyncio.run(p.terminate())
    assert "重载" in asyncio.run(collect(p, "2001-03-19 10:00 男"))[0]


def test_queue_bound_and_concurrent_isolation():
    p = ZiweiPlugin(object(), {"output_mode": "text"})
    p.pending = 4
    assert "请求较多" in asyncio.run(collect(p, "2001-03-19 10:00 男"))[0]
    p.pending = 0

    async def run():
        return await asyncio.gather(
            collect(p, "2001-03-19 10:00 男"), collect(p, "2001-03-19 10:00 女")
        )

    a, b = asyncio.run(run())
    assert "逆行" in "".join(a) and "顺行" in "".join(b)
    assert p.pending == 0


def test_real_concurrent_queue_and_worker_bound(monkeypatch):
    import threading
    import time

    from astrbot_plugin_ziwei import main

    original = main.build_chart
    counts = {"active": 0, "peak": 0}
    lock = threading.Lock()

    def delayed(*args):
        with lock:
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
        try:
            time.sleep(0.03)
            return original(*args)
        finally:
            with lock:
                counts["active"] -= 1

    monkeypatch.setattr(main, "build_chart", delayed)
    p = ZiweiPlugin(object(), {"output_mode": "text"})

    async def run():
        return await asyncio.gather(
            *(collect(p, "2001-03-19 10:00 男") for _ in range(6))
        )

    results = asyncio.run(run())
    assert sum("请求较多" in result[0] for result in results) == 2
    assert counts == {"active": 0, "peak": 2}
    assert p.pending == 0
