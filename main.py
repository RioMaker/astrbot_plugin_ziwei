"""AstrBot adapter; computation runs off the event loop and is bounded."""

import asyncio
import json

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

from .access import GroupAccess, can_manage, capture_group
from .commands import DEMO_BIRTH, HELP, parse_request
from .engine import apply_decade, apply_flow, build_chart
from .llm_data import ChartCache, chart_payload, tool_error
from .renderer import Renderer, text_chart
from .rules import RuleProfile


@register("astrbot_plugin_ziwei", "Rio", "紫微斗数排盘", "1.5.0")
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
        self.chart_cache = ChartCache()
        self.access = GroupAccess(
            self.get_kv_data, self.put_kv_data, self.delete_kv_data
        )
        try:
            self.renderer = Renderer(
                str(config.get("font_path", "") or ""),
                self.config.get("image_theme", "day"),
            )
        except (OSError, ValueError):
            self.renderer = None
            logger.warning("紫微斗数：中文字体不可用，将返回文字盘。可配置 font_path。")

    async def terminate(self):
        self.closed = True
        self.chart_cache.clear()

    @staticmethod
    def _scope_rejection(scope):
        if not scope.group_id:
            return "紫微排盘仅限已开启的群聊使用，请在目标群内发送 /紫微 开启。"
        if not scope.platform_id:
            return "无法识别当前平台实例，暂不能设置或使用本群紫微排盘。"
        return None

    async def _chart_access(self, scope):
        if self.closed:
            return "插件正在重载，请稍后重试。"
        rejection = self._scope_rejection(scope)
        if rejection:
            return rejection
        enabled = await self.access.enabled(scope)
        if self.closed:
            return "插件正在重载，请稍后重试。"
        if not enabled:
            return (
                "本群紫微排盘尚未开启，"
                "请群主、群管理员或 AstrBot 管理员发送 /紫微 开启。"
            )
        return None

    async def _group_access(self, scope, text):
        """Return a management/rejection message, or None for an allowed chart."""
        rejection = self._scope_rejection(scope)
        if rejection:
            return rejection
        if text == "状态":
            enabled = await self.access.enabled(scope)
            return f"本群紫微排盘：{'已开启' if enabled else '未开启'}。"
        if text in {"开启", "开", "关闭", "关"}:
            if not await can_manage(scope):
                return "仅群主、群管理员或 AstrBot 管理员可以开启或关闭本群紫微排盘。"
            enabled = text in {"开启", "开"}
            await self.access.set_enabled(scope, enabled)
            if not enabled:
                self.chart_cache.clear_group(scope)
            return (
                "本群紫微排盘已开启，群成员可使用 /紫微 排盘。"
                if enabled
                else "本群紫微排盘已关闭。"
            )
        return await self._chart_access(scope)

    async def _compute_chart(self, request):
        chart = await asyncio.to_thread(build_chart, request.birth, request.profile)
        if request.decade_index is not None:
            chart = await asyncio.to_thread(apply_decade, chart, request.decade_index)
        elif request.flow_target is not None or request.flow_year is not None:
            chart = await asyncio.to_thread(
                apply_flow, chart, year=request.flow_year, target=request.flow_target
            )
        return chart

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
            scope = capture_group(event)
            access_message = await self._group_access(scope, text)
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
                chart = await self._compute_chart(request)
                if self.closed:
                    yield event.plain_result("插件正在重载，请稍后重试。")
                    return
                access_message = await self._chart_access(scope)
                if access_message:
                    yield event.plain_result(access_message)
                    return
                if scope.user_id:
                    self.chart_cache.put(scope, chart)
                if request.output == "image" and self.renderer is not None:
                    try:
                        png = await asyncio.to_thread(
                            self.renderer.render, chart, request.image_theme
                        )
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

    @filter.llm_tool(name="ziwei_paipan")
    async def ziwei_paipan(
        self, event: AstrMessageEvent, birth_info: str, send_image: bool = False
    ) -> str:
        """根据用户明确提供的出生资料排紫微盘，返回JSON计算事实供解读。仅已开启的群可用，不可猜测未知出生时刻或把虚构示例当用户资料。工具不会开启群功能。可按用户要求同时发送图片。

        Args:
            birth_info(string): 用户提供的出生日期、具体时刻、男或女。
                格式如1990-06-15 08:30 男（仅虚构示例）；农历加农历前缀。
                也支持199006150830 男或19900615083045 男的日期时间连写格式。
                可附经度、时区、真太阳时、流年或流盘及流时选项，使用名称=值。
                运限=0生成童限盘，运限=1至12选择第几个大运，与流年和流盘互斥。
                流年=2026会返回生年、大限与流年三层四化及各层宫名。
                图片可附主题=白天或主题=夜间；不填使用插件默认主题。
                未知的出生资料先询问用户，不得代用示例。
            send_image(boolean): 用户需要图片时设true，默认false。
                结构化数据始终返回给模型。
        """
        if self.closed:
            return tool_error("unavailable", "插件正在重载，请稍后重试。")
        try:
            scope = capture_group(event)
            rejection = await self._chart_access(scope)
            if rejection:
                return tool_error("denied", rejection)
            if not scope.user_id:
                return tool_error("denied", "无法确认当前发送者身份。")
            if not isinstance(birth_info, str) or type(send_image) is not bool:
                return tool_error(
                    "invalid_input", "出生资料须为文本，send_image须为布尔值。"
                )
            if self.pending >= 4:
                return tool_error("busy", "排盘请求较多，请稍后重试。")
        except Exception as exc:
            logger.error(f"紫微斗数工具授权检查失败：{type(exc).__name__}")
            return tool_error("unavailable", "群使用白名单暂不可用。")
        self.pending += 1
        try:
            request = parse_request(birth_info, self.config)
            async with self.gate:
                chart = await self._compute_chart(request)
                if self.closed:
                    return tool_error("unavailable", "插件正在重载，请稍后重试。")
                rejection = await self._chart_access(scope)
                if rejection:
                    return tool_error("denied", rejection)
                row = self.chart_cache.put(scope, chart)
                image_status = "not_requested"
                if send_image:
                    image_status = "unavailable"
                    if self.renderer is not None and scope.umo:
                        try:
                            png = await asyncio.to_thread(
                                self.renderer.render, chart, request.image_theme
                            )
                            rejection = await self._chart_access(scope)
                            if rejection or self.closed:
                                return tool_error(
                                    "denied", rejection or "插件正在重载。"
                                )
                            sent = await asyncio.wait_for(
                                self.context.send_message(
                                    scope.umo, MessageChain([Image.fromBytes(png)])
                                ),
                                timeout=30,
                            )
                            image_status = "sent" if sent is not False else "failed"
                        except Exception as exc:
                            logger.warning(
                                f"紫微斗数工具图片发送失败：{type(exc).__name__}"
                            )
                            image_status = "failed"
                rejection = await self._chart_access(scope)
                if rejection:
                    return tool_error("denied", rejection)
                payload = chart_payload(
                    row, remaining_seconds=row.expires_at - self.chart_cache.clock()
                )
                # Add delivery status without mixing message text into JSON.
                result = json.loads(payload)
                result["image_status"] = image_status
                return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except ValueError as exc:
            return tool_error(
                "invalid_input", str(exc) + "；请确认用户资料，不得代用虚构示例。"
            )
        except Exception as exc:
            logger.error(f"紫微斗数工具排盘失败：{type(exc).__name__}")
            return tool_error("unavailable", "排盘暂时失败，不要自行猜测计算结果。")
        finally:
            self.pending -= 1

    @filter.llm_tool(name="ziwei_get_chart")
    async def ziwei_get_chart(
        self, event: AstrMessageEvent, chart_id: str = "", palace: str = ""
    ) -> str:
        """读取当前用户在本群最近一次命盘的JSON数据，用于解释刚才的排盘或查询某宫。仅已开启的群可用，不能读取其他成员或其他群的命盘。数据只在内存保留15分钟，关闭或重载后清除。未找到时须向用户确认出生资料，不可猜测。

        Args:
            chart_id(string): ziwei_paipan返回的命盘ID，留空读取最近一张。
                仅限当前用户在本群的盘；旧盘被新盘替换后其ID失效。
            palace(string): 留空取十二宫，或填命宫、夫妻、财帛、官禄等本命宫名。
                返回对应宫位、整体摘要及运限数据。
        """
        if self.closed:
            return tool_error("unavailable", "插件正在重载，请稍后重试。")
        try:
            scope = capture_group(event)
            rejection = await self._chart_access(scope)
            if rejection:
                return tool_error("denied", rejection)
            if not scope.user_id:
                return tool_error("denied", "无法确认当前发送者身份。")
            if (
                not isinstance(chart_id, str)
                or not isinstance(palace, str)
                or len(chart_id) > 64
                or len(palace) > 16
            ):
                return tool_error("invalid_input", "命盘ID和宫名须为有效文本。")
            row = self.chart_cache.get(scope, chart_id.strip())
            if row is None:
                return tool_error(
                    "not_found",
                    "当前用户在本群没有可用命盘或命盘已过期，请提供明确资料重新排盘。",
                )
            return chart_payload(
                row,
                remaining_seconds=row.expires_at - self.chart_cache.clock(),
                palace=palace,
            )
        except ValueError as exc:
            return tool_error("invalid_input", str(exc))
        except Exception as exc:
            logger.error(f"紫微斗数工具读取失败：{type(exc).__name__}")
            return tool_error("unavailable", "命盘数据暂不可用，不要自行猜测。")
