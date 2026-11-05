"""AstrBot adapter; computation runs off the event loop and is bounded."""

import asyncio
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .access import GroupAccess, can_manage, capture_group
from .archive_store import ArchiveStore
from .commands import DEMO_BIRTH, HELP, parse_request
from .compact_commands import normalize_compact_arguments, parse_compact_command
from .compact_filter import CompactCommandFilter, claim_command
from .engine import apply_decade, apply_flow, build_chart
from .help_renderer import HelpRenderer
from .life_atlas_service import LifeAtlasService
from .llm_data import ChartCache
from .renderer import Renderer, text_chart
from .rules import RuleProfile


@register("astrbot_plugin_ziwei", "Rio", "紫微斗数排盘", "2.0.1")
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
        data_dir = (
            Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_ziwei"
        )
        # Preserve historical tables without resuming any AI jobs.
        self.archive = ArchiveStore(data_dir / "archives.sqlite3", resume_ai_jobs=False)
        self.life_atlases = LifeAtlasService(self)
        self.help_renderer = None
        try:
            self.renderer = Renderer(
                str(config.get("font_path", "") or ""),
                self.config.get("image_theme", "night"),
            )
            self.help_renderer = HelpRenderer(self.renderer.font_path)
        except (OSError, ValueError):
            self.renderer = None
            logger.warning("紫微斗数：中文字体不可用，将返回文字盘。可配置 font_path。")

    async def terminate(self):
        self.closed = True
        self.chart_cache.clear()
        self.archive.close()

    async def initialize(self):
        """Command-only mode deliberately leaves historical AI jobs idle."""

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
            self.archive.set_group(scope, enabled)
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

    @filter.command("紫微", alias={"紫薇", "紫微排盘", "紫薇排盘", "ziwei"})
    async def ziwei(self, event: AstrMessageEvent, args: GreedyStr):
        """按明确的出生资料生成紫微斗数十二宫命盘。"""
        async for result in self._ziwei_command(event, args):
            yield result

    async def _ziwei_command(self, event, args, *, quiet=False):
        text = str(args).strip()
        if text == "定盘" or text.startswith("定盘 "):
            claim_command(event, quiet)
            yield event.plain_result(
                "当前仅支持指令排盘，暂不提供 AI 辅助定盘。"
                "请用 /紫微 帮助 查看排盘格式。"
            )
            return
        if text.startswith("图谱"):
            tail = text[2:]
            quiet = quiet or bool(tail and not tail[0].isspace())
            async for result in self.life_atlases.run(event, tail.strip(), quiet=quiet):
                yield result
            return
        normalized = normalize_compact_arguments(text)
        text, quiet = normalized.args, quiet or normalized.changed
        claim_command(event, quiet)
        if not text or text.lower() in {
            "帮助",
            "help",
            "?",
            "？",
            "帮助 文字",
            "help text",
        }:
            async for result in self._help_reply(
                event, text.lower().endswith(("文字", "text"))
            ):
                yield result
            return
        if self.closed:
            if not quiet:
                yield event.plain_result("插件正在重载，请稍后重试。")
            return
        try:
            scope = capture_group(event)
            access_message = await self._group_access(scope, text)
        except Exception as exc:
            logger.error(f"紫微斗数白名单读取或设置失败：{type(exc).__name__}")
            if not quiet:
                yield event.plain_result("群使用白名单暂不可用，请稍后重试。")
            return
        if access_message is not None:
            if not quiet or text in {"开启", "开", "关闭", "关", "状态"}:
                yield event.plain_result(access_message)
            return
        if not scope.user_id:
            if not quiet:
                yield event.plain_result("无法确认当前发送者身份，不能建立命盘档案。")
            return
        if self.pending >= 4:
            if not quiet:
                yield event.plain_result("排盘请求较多，请稍后重试。")
            return
        self.pending += 1
        try:
            request = parse_request(text, self.config)
            async with self.gate:
                # A queued request must recheck authorization before computing.
                access_message = await self._chart_access(scope)
                if access_message:
                    if not quiet:
                        yield event.plain_result(access_message)
                    return
                chart = await self._compute_chart(request)
                if self.closed:
                    if not quiet:
                        yield event.plain_result("插件正在重载，请稍后重试。")
                    return
                access_message = await self._chart_access(scope)
                if access_message:
                    if not quiet:
                        yield event.plain_result(access_message)
                    return
                if scope.user_id:
                    chart_id = self.archive.save_chart(scope, chart)
                    self.chart_cache.put(scope, chart, chart_id=chart_id)
                if request.output == "image" and self.renderer is not None:
                    try:
                        png = await asyncio.to_thread(
                            self.renderer.render, chart, request.image_theme
                        )
                    except Exception as exc:
                        logger.warning(
                            f"紫微斗数图片生成失败，返回文字盘：{type(exc).__name__}"
                        )
                        png = None
                    if png:
                        access_message = await self._chart_access(scope)
                        if access_message:
                            if not quiet:
                                yield event.plain_result(access_message)
                            return
                        yield event.chain_result([Image.fromBytes(png)])
                        return
                report = text_chart(chart)
                if request.output == "image":
                    report = "图片暂不可用，以下为文字盘。\n\n" + report
                # Keep parallel requests bounded, including message delivery.
                for offset in range(0, len(report), 2800):
                    access_message = await self._chart_access(scope)
                    if access_message:
                        if not quiet:
                            yield event.plain_result(access_message)
                        return
                    yield event.plain_result(report[offset : offset + 2800])
        except ValueError as exc:
            if not quiet:
                yield event.plain_result(
                    f"排盘参数有误：{exc}\n发送 /紫微 帮助 查看格式。"
                )
        except Exception as exc:
            # Birth data and message contents must not appear in error logs.
            logger.error(f"紫微斗数排盘失败：{type(exc).__name__}")
            if not quiet:
                yield event.plain_result("排盘暂时失败，请稍后重试。")
        finally:
            self.pending -= 1

    @filter.command("人生")
    async def life_atlas(self, event: AstrMessageEvent, args: GreedyStr):
        """按出生资料或本人命盘生成从出生至60岁所在大运末年的人生图谱。"""
        # GreedyStr joins even an empty remainder; a default would hide its type
        # from AstrBot's CommandFilter and consume only the first input token.
        async for result in self.life_atlases.run(event, args):
            yield result

    @filter.custom_filter(CompactCommandFilter)
    async def compact_command(self, event: AstrMessageEvent):
        """Handle only awakened commands missing the name/argument separator."""
        if not CompactCommandFilter().filter(event, self.config):
            return
        command = parse_compact_command(event.get_message_str().strip())
        if command.target == "atlas":
            results = self.life_atlases.run(event, command.args, quiet=True)
        else:
            results = self._ziwei_command(event, command.args, quiet=True)
        async for result in results:
            yield result

    async def _help_reply(self, event, text_only=False):
        if (
            not text_only
            and self.help_renderer is not None
            and not self.closed
            and self.pending < 4
        ):
            self.pending += 1
            try:
                async with self.gate:
                    if not self.closed:
                        png = await asyncio.to_thread(
                            self.help_renderer.render,
                            self.config.get("image_theme", "night"),
                        )
                        if not png:
                            raise ValueError("empty help image")
                        yield event.chain_result([Image.fromBytes(png)])
                        return
            except Exception as exc:
                # Never log event contents or supplied birth data.
                logger.warning(f"紫微帮助图片生成失败：{type(exc).__name__}")
            finally:
                self.pending -= 1
        yield event.plain_result(HELP)
