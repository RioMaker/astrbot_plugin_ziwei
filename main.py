"""AstrBot adapter; computation runs off the event loop and is bounded."""

import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

from .access import GroupAccess, can_manage, capture_group
from .commands import DEMO_BIRTH, HELP, parse_request
from .engine import apply_flow, build_chart
from .renderer import Renderer, text_chart
from .rules import RuleProfile


@register("astrbot_plugin_ziwei", "Rio", "紫微斗数排盘", "1.2.1")
class ZiweiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = dict(config)
        # Validate global configuration at load, rather than fail for every user.
        RuleProfile.from_config(self.config)
        parse_request(DEMO_BIRTH, self.config)
        self.gate = asyncio.Semaphore(2)
        self.pending = 0
        self.closed = False
        self.access = GroupAccess(
            self.get_kv_data, self.put_kv_data, self.delete_kv_data
        )
        try:
            self.renderer = Renderer(str(config.get("font_path", "") or ""))
        except (OSError, ValueError):
            self.renderer = None
            logger.warning("紫微斗数：中文字体不可用，将返回文字盘。可配置 font_path。")

    async def terminate(self):
        self.closed = True

    async def _group_access(self, event, text):
        """Return a management/rejection message, or None for an allowed chart."""
        scope = capture_group(event)
        if not scope.group_id:
            return "紫微排盘仅限已开启的群聊使用，请在目标群内发送 /紫微 开启。"
        if not scope.platform_id:
            return "无法识别当前平台实例，暂不能设置或使用本群紫微排盘。"
        if text == "状态":
            enabled = await self.access.enabled(scope)
            return f"本群紫微排盘：{'已开启' if enabled else '未开启'}。"
        if text in {"开启", "开", "关闭", "关"}:
            if not await can_manage(scope):
                return "仅群主、群管理员或 AstrBot 管理员可以开启或关闭本群紫微排盘。"
            enabled = text in {"开启", "开"}
            await self.access.set_enabled(scope, enabled)
            return (
                "本群紫微排盘已开启，群成员可使用 /紫微 排盘。"
                if enabled
                else "本群紫微排盘已关闭。"
            )
        if not await self.access.enabled(scope):
            return (
                "本群紫微排盘尚未开启，"
                "请群主、群管理员或 AstrBot 管理员发送 /紫微 开启。"
            )
        return None

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
        try:
            access_message = await self._group_access(event, text)
        except Exception as exc:
            logger.error(f"紫微斗数白名单读取或设置失败：{type(exc).__name__}")
            yield event.plain_result("群使用白名单暂不可用，请稍后重试。")
            return
        if access_message is not None:
            yield event.plain_result(access_message)
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
