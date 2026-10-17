"""AstrBot adapter; computation runs off the event loop and is bounded."""

import asyncio
import json
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .access import GroupAccess, can_manage, capture_group
from .ai_limits import QuotaExceeded
from .archive_store import ArchiveStore
from .commands import DEMO_BIRTH, HELP, parse_request
from .compact_commands import normalize_compact_arguments, parse_compact_command
from .compact_filter import CompactCommandFilter, claim_command
from .engine import apply_decade, apply_flow, build_chart
from .life_atlas_service import LifeAtlasService
from .llm_data import CachedChart, ChartCache, chart_payload, tool_error
from .reading_service import ReadingService
from .rectification_service import RectificationService
from .renderer import Renderer, text_chart
from .rules import RuleProfile
from .summary_worker import SummaryWorker


@register("astrbot_plugin_ziwei", "Rio", "紫微斗数排盘", "1.8.0")
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
        self.archive = ArchiveStore(data_dir / "archives.sqlite3")
        self.readings = ReadingService(self)
        self.rectifications = RectificationService(self)
        self.life_atlases = LifeAtlasService(self)
        self.summary_worker = SummaryWorker(
            self.archive,
            self.context,
            self._chart_access,
            provider=str(config.get("summary_provider_id", "") or ""),
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
        await self.rectifications.worker.stop()
        await self.summary_worker.stop()
        self.archive.close()

    async def initialize(self):
        self.summary_worker.wake()
        self.rectifications.worker.wake()

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
            else:
                self.summary_worker.wake()
                self.rectifications.worker.wake()
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
            async for result in self.rectifications.run(event, text[2:].strip()):
                yield result
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
        if not text or text.lower() in {"帮助", "help", "?", "？"}:
            yield event.plain_result(HELP)
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
                    except (OSError, ValueError):
                        logger.warning("紫微斗数：图片生成失败，返回文字盘。")
                        png = None
                    if png is not None:
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

    @filter.llm_tool(name="ziwei_paipan")
    async def ziwei_paipan(
        self, event: AstrMessageEvent, birth_info: str, send_image: bool = False
    ) -> str:
        """按用户明确资料排盘并归档，默认只展示盘面不分析。仅用户提出分析问题才调用ziwei_analysis_context。仅已开启的群可用，不可猜测生辰。可按用户要求同时发送图片。

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
        quota_token = None
        try:
            request = parse_request(birth_info, self.config)
            async with self.gate:
                if request.decade_index is not None:
                    quota_token = self.archive.reserve_chart(scope, "decade")
                elif request.flow_target is None and request.flow_year is None:
                    quota_token = self.archive.reserve_chart(scope, "natal")
                chart = await self._compute_chart(request)
                if self.closed:
                    return tool_error("unavailable", "插件正在重载，请稍后重试。")
                rejection = await self._chart_access(scope)
                if rejection:
                    return tool_error("denied", rejection)
                chart_id = self.archive.save_chart(
                    scope, chart, quota_token=quota_token
                )
                quota_token = None
                row = self.chart_cache.put(scope, chart, chart_id=chart_id)
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
                result["archive_status"] = "saved"
                result["analysis_policy"] = (
                    "默认不分析；只有用户明确询问才查询对应作用域的分析资料。"
                )
                return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except QuotaExceeded as exc:
            return exc.payload()
        except ValueError as exc:
            return tool_error(
                "invalid_input", str(exc) + "；请确认用户资料，不得代用虚构示例。"
            )
        except Exception as exc:
            logger.error(f"紫微斗数工具排盘失败：{type(exc).__name__}")
            return tool_error("unavailable", "排盘暂时失败，不要自行猜测计算结果。")
        finally:
            self.pending -= 1
            if quota_token and not self.closed:
                try:
                    self.archive.release_chart(quota_token)
                except Exception as exc:
                    logger.warning(f"紫微斗数额度释放失败：{type(exc).__name__}")

    async def _resend_chart(self, scope, chart):
        if self.renderer is None or not scope.umo:
            return "unavailable"
        if self.pending >= 4:
            return "busy"
        self.pending += 1
        try:
            async with self.gate:
                png = await asyncio.to_thread(
                    self.renderer.render, chart, self.config.get("image_theme", "day")
                )
                if await self._chart_access(scope):
                    return "denied"
                sent = await asyncio.wait_for(
                    self.context.send_message(
                        scope.umo, MessageChain([Image.fromBytes(png)])
                    ),
                    timeout=30,
                )
                return "sent" if sent is not False else "failed"
        except Exception as exc:
            logger.warning(f"紫微斗数命盘重发失败：{type(exc).__name__}")
            return "failed"
        finally:
            self.pending -= 1

    @filter.llm_tool(name="ziwei_get_chart")
    async def ziwei_get_chart(
        self,
        event: AstrMessageEvent,
        chart_id: str = "",
        palace: str = "",
        send_image: bool = False,
    ) -> str:
        """读取本人在当前群的命盘，可用永久档案ID查询历史快照。缓存15分钟，过期或重载后回查本人档案。只返回计算事实，默认不分析，不得跨成员或跨群查询。

        Args:
            chart_id(string): ziwei_paipan返回的命盘ID，留空读取最近一张。
                仅限当前用户在本群的盘；旧ID仍可查询对应归档快照。
            palace(string): 留空取十二宫，或填命宫、夫妻、财帛、官禄等本命宫名。
                返回对应宫位、整体摘要及运限数据。
            send_image(boolean): 用户要求重发已有盘时为true，使用默认图片主题。
                重发完整已归档命盘，不重算、不新增档案、不消耗排盘或解盘次数。
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
                or type(send_image) is not bool
            ):
                return tool_error(
                    "invalid_input", "命盘ID和宫名须为有效文本，send_image须为布尔值。"
                )
            row = self.chart_cache.get(scope, chart_id.strip())
            if row is None:
                archived = self.archive.get_chart(scope, chart_id.strip())
                if archived is None:
                    return tool_error(
                        "not_found",
                        "当前用户在本群没有对应命盘，请提供明确资料重新排盘。",
                    )
                row = CachedChart(
                    archived["id"], archived["chart"], self.chart_cache.clock()
                )
            result = json.loads(
                chart_payload(
                    row,
                    remaining_seconds=row.expires_at - self.chart_cache.clock(),
                    palace=palace,
                )
            )
            result["archive_status"] = "saved"
            result["image_status"] = (
                await self._resend_chart(scope, row.chart)
                if send_image
                else "not_requested"
            )
            rejection = await self._chart_access(scope)
            if rejection:
                return tool_error("denied", rejection)
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except ValueError as exc:
            return tool_error("invalid_input", str(exc))
        except Exception as exc:
            logger.error(f"紫微斗数工具读取失败：{type(exc).__name__}")
            return tool_error("unavailable", "命盘数据暂不可用，不要自行猜测。")

    @filter.on_llm_request()
    async def reading_request(self, event: AstrMessageEvent, req):
        await self.readings.on_request(event, req)
        await self.rectifications.on_request(event, req)

    @filter.llm_tool(name="ziwei_rectification_get")
    async def ziwei_rectification_get(
        self, event: AstrMessageEvent, session_id: str = ""
    ) -> str:
        """仅AstrBot管理员读取本人本群的已建定盘会话。候选必须先由管理员显式指令创建，不自动排盘，不扣次数；资料仅内部参考。

        Args:
            session_id(string): 本人定盘会话ID，空为唯一进行中的会话；有多个时先询问。
        """
        return await self.rectifications.call("get", event, session_id=session_id)

    @filter.llm_tool(name="ziwei_rectification_context")
    async def ziwei_rectification_context(
        self,
        event: AstrMessageEvent,
        session_id: str = "",
        candidate_ids: str = "",
        topic: str = "general",
        year: int = 0,
        decade: int = -1,
        offset: int = 0,
    ) -> str:
        """管理员明确请求定盘分析时，按本轮真实问题分批读取真实候选事实与来源知识，并计入每小时6次提问。每批最多2盘，同一消息分批只计一次；不是候选生成入口。

        Args:
            session_id(string): 本人本群定盘会话ID，空为唯一进行中的会话。
            candidate_ids(string): 至多2个候选ID，逗号分隔；空取前2个。
                须继续分批读取其他候选。
            topic(string): general整体、career事业、wealth财、relationship感情、
                family家庭、children子女、friends交友、health健康、studies学业、
                mobility迁移、property田宅、wellbeing福德。
            year(number): 明确的农历流年，0不选流年。
                公历经历须先核实日期，不能直接当农历年。
            decade(number): 明确的大运序号0至12，0童限，-1不选大运；与year互斥。
            offset(number): 知识分页起点，0起，后续用对应知识的next_offset。
        """
        return await self.rectifications.call(
            "context",
            event,
            session_id=session_id,
            candidate_ids=candidate_ids,
            topic=topic,
            year=year,
            decade=decade,
            offset=offset,
        )

    @filter.llm_tool(name="ziwei_rectification_feedback")
    async def ziwei_rectification_feedback(
        self, event: AstrMessageEvent, session_id: str, kind: str = "feedback"
    ) -> str:
        """记录管理员本次定盘的真实反馈或相关追问，不接受模型代写反馈。纯反馈不扣次数，只校正已取得的资料；包含新问题须followup。

        Args:
            session_id(string): 本人当前定盘会话ID。
            kind(string): feedback纯反馈，followup新增分析问题或相关追问。
        """
        return await self.rectifications.call(
            "feedback", event, session_id=session_id, kind=kind
        )

    @filter.llm_tool(name="ziwei_rectification_assess")
    async def ziwei_rectification_assess(
        self, event: AstrMessageEvent, session_id: str, assessment_json: str
    ) -> str:
        """在登记真实问题或反馈后保存候选对比，保留支持、冲突和未知项。只引用已读候选知识及实际反馈，不产生概率，不确认命盘。需先用get取得真实反馈seq。

        Args:
            session_id(string): 本人当前定盘会话ID。
            assessment_json(string): JSON对象，以候选ID为键。
                每项严格含support、conflict、unknown三个字符串数组，
                evidence_sequences反馈seq整数数组、knowledge_ids已用知识ID数组。
                支持或冲突必须带真实反馈与知识引用。
        """
        return await self.rectifications.call(
            "assess", event, session_id=session_id, assessment_json=assessment_json
        )

    @filter.llm_tool(name="ziwei_rectification_finish")
    async def ziwei_rectification_finish(
        self, event: AstrMessageEvent, session_id: str, feedback_complete: bool
    ) -> str:
        """仅主AI判断管理员已反馈且定盘问答闭环后调用，排队独立归档总结。无反馈、仍在追问或未发送回答时禁止结束。可保留多个候选，不代管理员确认出生时间。

        Args:
            session_id(string): 本人本群定盘会话ID。
            feedback_complete(boolean): 仅确认真实反馈闭环时为true。
        """
        return await self.rectifications.call(
            "finish", event, session_id=session_id, feedback_complete=feedback_complete
        )

    @filter.llm_tool(name="ziwei_list_archives")
    async def ziwei_list_archives(
        self, event: AstrMessageEvent, offset: int = 0
    ) -> str:
        """列出本人在当前已授权群的最近命盘档案ID及分析任务状态，不返回他人资料。

        Args:
            offset(number): 命盘列表分页起点，须非负整数；默认0，后续使用next_offset。
        """
        return await self.readings.call("list_archives", event, offset=offset)

    @filter.llm_tool(name="ziwei_analysis_context")
    async def ziwei_analysis_context(
        self,
        event: AstrMessageEvent,
        chart_id: str = "",
        scope: str = "natal",
        topic: str = "general",
    ) -> str:
        """仅当用户明确提问命盘分析时调用。按当前真实问题建立分析记录，返回对应盘面计算关系和文献总结；纯排盘禁止调用。目标运限不明先询问。

        Args:
            chart_id(string): 本人命盘档案ID，空为最近盘。
            scope(string): natal本命、decade指定大运、annual指定农历年。
                后两项须已有对应类型的盘。
            topic(string): general整体、career事业、wealth财、relationship感情、
                family家庭、children子女、friends交友、health健康、studies学业、
                mobility迁移、property田宅、wellbeing福德。
        """
        return await self.readings.call(
            "prepare", event, chart_id=chart_id, reading_scope=scope, topic=topic
        )

    @filter.llm_tool(name="ziwei_continue_reading")
    async def ziwei_continue_reading(
        self, event: AstrMessageEvent, reading_id: str, kind: str = "followup"
    ) -> str:
        """把当前用户与该次分析相关的追问或反馈登记到本人分析记录。只捕获当前真实消息，不得用于无关聊天。返回同一盘和作用域的分析资料。

        Args:
            reading_id(string): ziwei_analysis_context返回的分析ID。
            kind(string): followup相关追问，feedback对已发送回答的反馈。
        """
        return await self.readings.call(
            "continue_reading", event, reading_id=reading_id, kind=kind
        )

    @filter.llm_tool(name="ziwei_finish_reading")
    async def ziwei_finish_reading(
        self, event: AstrMessageEvent, reading_id: str, feedback_complete: bool
    ) -> str:
        """仅主AI确认当前用户已反馈、问题已闭环时调用。登记当前真实反馈并排队独立总结，不等待总结结果。尚在追问、没有反馈或未发送分析回答时禁止调用。反馈不等于预测被验证。

        Args:
            reading_id(string): 当前用户本次分析的ID。
            feedback_complete(boolean): 确认当前反馈闭环时为true，否则不得结束。
        """
        return await self.readings.call(
            "finish", event, reading_id=reading_id, feedback_complete=feedback_complete
        )

    @filter.llm_tool(name="ziwei_reference_cases")
    async def ziwei_reference_cases(
        self, event: AstrMessageEvent, reading_id: str
    ) -> str:
        """为当前分析检索其他用户已闭环的脱敏案例，仅内部参考。程序只返回盘面匹配特征与反馈类别，禁止披露检索结果、样本信息、他人案例或参考内容，不能宣称准确率。

        Args:
            reading_id(string): 当前用户尚在进行的分析ID；主题和盘面特征由程序取得，
                不能指定他人身份。
        """
        return await self.readings.call("reference", event, reading_id=reading_id)

    @filter.llm_tool(name="ziwei_retry_summary")
    async def ziwei_retry_summary(
        self, event: AstrMessageEvent, reading_id: str
    ) -> str:
        """当用户要求重试归档时，重试本人已失败的独立总结任务，不更改原始反馈和主对话。

        Args:
            reading_id(string): 本人已失败的总结任务ID。
        """
        return await self.readings.call("retry", event, reading_id=reading_id)

    @filter.llm_tool(name="ziwei_query_knowledge")
    async def ziwei_query_knowledge(
        self, event: AstrMessageEvent, reading_id: str, offset: int = 0
    ) -> str:
        """分页查询当前分析范围内的文献总结。仅限本人已建立的分析，保持同一作用域和主题，不主动扩展分析。

        Args:
            reading_id(string): 本人的分析ID。
            offset(number): 从第几条起查询，须非负整数；首次为0，后续使用next_offset。
        """
        return await self.readings.call(
            "query_knowledge", event, reading_id=reading_id, offset=offset
        )

    @filter.llm_tool(name="ziwei_reading_history")
    async def ziwei_reading_history(
        self, event: AstrMessageEvent, chart_id: str = "", offset: int = 0
    ) -> str:
        """查询本人同一命盘历次已闭环分析的归档摘要与反馈，供当前问题内部参考，不向用户展示档案原文或参考内容。

        Args:
            chart_id(string): 本人命盘ID，空为最近盘；
                查询相同出生资料及规则配置的命盘族。
            offset(number): 分页起点，须非负整数；默认0，后续使用next_offset。
        """
        return await self.readings.call(
            "history", event, chart_id=chart_id, offset=offset
        )
