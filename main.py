"""AstrBot adapter; computation runs off the event loop and is bounded."""

import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

from .commands import HELP, parse_request
from .engine import apply_flow, build_chart
from .renderer import Renderer, text_chart
from .rules import RuleProfile


@register("astrbot_plugin_ziwei", "Rio", "紫微斗数排盘", "1.0.0")
class ZiweiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = dict(config)
        # Validate global configuration at load, rather than fail for every user.
        RuleProfile.from_config(self.config)
        parse_request("2001-03-19 10:00 男", self.config)
        self.gate = asyncio.Semaphore(2)
        self.pending = 0
        self.closed = False
        try:
            self.renderer = Renderer(str(config.get("font_path", "") or ""))
        except (OSError, ValueError):
            self.renderer = None
            logger.warning("紫微斗数：中文字体不可用，将返回文字盘。可配置 font_path。")

    async def terminate(self):
        self.closed = True

    @filter.command("紫微", alias={"紫微排盘", "ziwei"})
    async def ziwei(self, event: AstrMessageEvent, args: GreedyStr):
        """按明确的出生资料生成紫微斗数十二宫命盘。"""
        event.stop_event()
        text = str(args).strip()
        if not text or text.lower() in {"帮助", "help", "?", "？"}:
            yield event.plain_result(HELP)
            return
        if self.closed:
            yield event.plain_result("插件正在重载，请稍后重试。")
            return
        if self.pending >= 4:
            yield event.plain_result("排盘请求较多，请稍后重试。")
            return
        self.pending += 1
        try:
            request = parse_request(text, self.config)
            async with self.gate:
                chart = await asyncio.to_thread(
                    build_chart, request.birth, request.profile
                )
                if request.flow_target is not None or request.flow_year is not None:
                    chart = await asyncio.to_thread(
                        apply_flow,
                        chart,
                        year=request.flow_year,
                        target=request.flow_target,
                    )
                if request.output == "image" and self.renderer is not None:
                    try:
                        png = await asyncio.to_thread(self.renderer.render, chart)
                    except (OSError, ValueError):
                        logger.warning("紫微斗数：图片生成失败，返回文字盘。")
                        png = None
                    if png is not None:
                        yield event.chain_result([Image.fromBytes(png)])
                        return
                report = text_chart(chart)
                if request.output == "image":
                    report = "图片暂不可用，以下为文字盘。\n\n" + report
                # Keep parallel requests bounded, including message delivery.
                for offset in range(0, len(report), 2800):
                    yield event.plain_result(report[offset : offset + 2800])
        except ValueError as exc:
            yield event.plain_result(f"排盘参数有误：{exc}\n发送 /紫微 帮助 查看格式。")
        except Exception as exc:
            # Birth data and message contents must not appear in error logs.
            logger.error(f"紫微斗数排盘失败：{type(exc).__name__}")
            yield event.plain_result("排盘暂时失败，请稍后重试。")
        finally:
            self.pending -= 1
