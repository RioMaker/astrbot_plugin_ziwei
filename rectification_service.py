"""Administrator-only, event-bound comparison of archived candidate charts."""

import asyncio
import hashlib
import json
import re
from dataclasses import asdict

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image
from astrbot.core.agent.message import TextPart

from .access import capture_group
from .ai_limits import QuotaExceeded
from .analysis_facts import build_facts
from .compact_filter import claim_command
from .delivery_capture import install_capture
from .engine import apply_decade, apply_flow
from .knowledge import KnowledgeBase
from .llm_data import tool_error
from .reading_service import event_key, event_text
from .rectification import build_candidates
from .rectification_commands import parse_rectification_request
from .rectification_renderer import render_candidate_collage
from .summary_worker import SYSTEM, SummaryWorker

HELP = """管理员 AI 辅助定盘（逐轮问答）
/紫微 定盘 1990-06-15 男
/紫微 定盘 1990-06-15 男 时辰=辰,巳,午 真太阳时=关
以上为虚构演示资料；日期可加农历前缀，支持经度、时区、真太阳时、闰月、子时和主题选项。
未填时辰则比较全天；时辰表示出生当天的钟表时间范围，子包括早子与晚子。
程序按历法和校正边界拆分候选；显示的时刻仅是候选范围代表，不是确定的出生时刻。
创建后 @机器人说“开始定盘”，再按 AI 提问提供已发生的经历及日期。
/紫微 定盘 状态 [会话ID]
/紫微 定盘 总图 [会话ID]：将全部候选纵向拼成一张图片，标题横条分隔
/紫微 定盘 查看 c01 [会话ID]
/紫微 定盘 确认 c01 [会话ID]：明确选择后才设为当前命盘
/紫微 定盘 取消 [会话ID]
/紫微 定盘 重试总结 [会话ID]
仅当前已开启群的 AstrBot 管理员可用，群主或群管理员身份不能单独授权。
也可直接告诉 AI 出生日期、性别和候选时辰，要求定盘；要图片时只发一张完整总图。
AI 创建整批按1次生年排盘计，与普通排盘共享每小时2次；直接指令创建不扣额度。
AI 新问题仍计入每小时6次，纯反馈、读取和总图重发不扣次数。
定盘为传统规则下的辅助比较，支持证据不足时保留多个候选。"""

POLICY = """管理员定盘工具规则：
定盘只对当前真实事件的 AstrBot 管理员和其本人本群会话开放。
用户明确要求定盘并给出出生日期和性别时，
可用 ziwei_rectification_create 直接生成全天候选。
用户指定几个候选时辰时传入时辰选项；缺少日期、性别或历法不明确时先询问，不推测。
整批消耗1次生年排盘额度，与普通AI排盘共享每小时2次；不能逐张调用普通工具绕过限制。
需要图片时仅用create或get的send_image=true，全部候选拼成一张总图，不逐张发送。
已创建的批次使用get读取或重发，不能为了重发而另建；同一真实消息重复调用只发一次。
仅用户明确要求定盘或回复本次定盘问题时处理，不将管理员的其他聊天自动登记。
已有会话先用 ziwei_rectification_get 读取；未指定会话且有多个进行中会话时先询问选择。
初次定盘或新的分析问题用 ziwei_rectification_context 按主题分批读取候选，每批至多2个；
同一真实消息可分批读完整候选集合，只算一次问题。保留每个候选，不凭首次两张代表全部。
优先问少量能够区分候选的既往经历（如迁居、学业、职业和关系变化及年月），避免暗示答案。
反馈使用 ziwei_rectification_feedback(kind=feedback)，
只登记当前真实反馈，不重复收取问题次数；
反馈中的新问题用kind=followup登记。纯反馈仅校正已取得的资料，不扩展新主题或年份分析。
比较只使用程序实际返回的事实和知识，不把文献命中当条件满足，不使用未实现的规则。
需要流年时先明确农历年份；公历事件在春节前后需核实具体日期，不能直接偷换为农历年。
需要大运时明确序号，0为童限。
逐轮用 ziwei_rectification_assess 保存支持、冲突、未知项及反馈序号、知识ID。
评价是待核实的传统解释，不给概率或准确率，不因“谢谢”视为验证，不编造经历，不作高风险必然断言。
候选范围代表时刻不是实际出生时刻。资料不足保留多个候选；不得自行确认或永久排除候选。
只有管理员显式 /紫微 定盘 确认 才设为当前盘；结束不必选出唯一时辰。
用户已反馈且主AI确认问答闭环后才用 ziwei_rectification_finish；仍在追问不得结束。
收到rate_limited告知等待秒数并停止本轮新增分析，不通过get/feedback/assessment绕过新问题额度。
历史原文、检索资料与内部归档仅供内部参考，不向群披露档案原文或其他人的信息。"""


def identifier(value, *, optional=False):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{0,64}", value):
        raise ValueError("定盘会话或候选ID无效")
    if not value and not optional:
        raise ValueError("请指定定盘会话或候选ID")
    return value


def overview(row):
    return [
        {
            "id": c["id"],
            "candidate_id": c["id"],
            "label": c["label"],
            "intervals": c["intervals"],
            "life": c["chart"]["life"],
            "body": c["chart"]["body"],
            "bureau": c["chart"]["bureau_name"],
        }
        for c in row["candidates"]
    ]


STATE_LABELS = {
    "active": "进行中",
    "selected": "已确认候选",
    "cancelled": "已取消",
    "none": "尚未总结",
    "queued": "等待总结",
    "running": "正在总结",
    "archived": "已归档",
    "failed": "总结失败，可重试",
}


class RectificationService:
    def __init__(self, plugin):
        self.plugin = plugin
        self.store = plugin.archive.rectifications
        self.knowledge = KnowledgeBase()
        self.creation_lock = asyncio.Lock()
        self.image_lock = asyncio.Lock()
        self.worker = SummaryWorker(
            self.store,
            plugin.context,
            plugin._chart_access,
            provider=str(plugin.config.get("summary_provider_id", "") or ""),
            system=SYSTEM + "\n本次是一个定盘会话；chart为该会话候选的计算摘要。"
            "保留多候选、冲突与未知项；管理员选定仅代表其选择，不代表出生时间已获证实。",
        )

    async def authorize(self, event):
        scope = capture_group(event)
        await self.check(scope)
        return scope

    async def check(self, scope):
        rejection = await self.plugin._chart_access(scope)
        if rejection:
            raise PermissionError(rejection)
        if not scope.user_id:
            raise PermissionError("无法确认当前发送者身份。")
        if not scope.astrbot_admin:
            raise PermissionError("定盘功能目前仅允许 AstrBot 管理员使用。")

    def row(self, scope, session_id=""):
        identifier(session_id, optional=True)
        if not session_id:
            active = self.store.active(scope)
            if len(active) > 1:
                raise ValueError("有多个定盘会话，请明确提供会话ID")
            if active:
                session_id = active[0]["id"]
        row = self.store.get(scope, session_id)
        if row is None:
            raise ValueError("找不到本人在本群的定盘会话，请提供出生日期和性别创建候选")
        return row

    @staticmethod
    def usable(row):
        if row["status"] == "cancelled" or row["summary_status"] != "none":
            raise ValueError("定盘会话已取消或已进入总结，请新建会话继续分析")

    async def call(self, action, event, **kwargs):
        try:
            # Freeze both identity and the real message before any asynchronous work.
            scope = capture_group(event)
            key = event_key(event)
            try:
                text = event_text(event)
            except ValueError:
                text = ""
            await self.check(scope)
            if not text and action != "get":
                raise ValueError("无法取得当前真实用户消息")
            result = await getattr(self, action)(scope, event, key, text, **kwargs)
            await self.check(scope)
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except PermissionError as exc:
            return tool_error("denied", str(exc))
        except QuotaExceeded as exc:
            return exc.payload()
        except (ValueError, TypeError) as exc:
            return tool_error("invalid_input", str(exc))
        except Exception as exc:
            logger.warning(f"定盘工具暂不可用：{type(exc).__name__}")
            return tool_error("unavailable", "定盘资料暂不可用，请勿猜测计算结果")

    async def create_session(self, scope, event, key, birth_info, *, ai=False):
        if not isinstance(birth_info, str):
            raise ValueError("出生资料须为文本")
        async with self.creation_lock:
            await self.check(scope)
            parsed = parse_rectification_request(birth_info, self.plugin.config)
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "birth": asdict(parsed.request.birth),
                        "profile": parsed.request.profile.snapshot(),
                        "intervals": parsed.intervals,
                        "theme": parsed.request.image_theme,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            row = self.store.find_event(scope, key)
            self.check_binding(event, row["id"] if row else "")
            if row is not None:
                if row["metadata"].get("request_fingerprint") != fingerprint:
                    raise ValueError(
                        "同一真实消息已创建不同候选，请另发消息修改出生资料"
                    )
                return row
            token = None
            try:
                if ai:
                    token = self.plugin.archive.reserve_chart(scope, "natal")
                candidates = await asyncio.to_thread(build_candidates, parsed)
                await self.check(scope)
                self.check_binding(event, "")
                metadata = dict(parsed.metadata, request_fingerprint=fingerprint)
                sid = self.store.create(
                    scope, key, metadata, candidates, quota_token=token
                )
                return self.store.get(scope, sid)
            finally:
                if token and not self.plugin.closed:
                    self.plugin.archive.release_chart(token)

    async def send_collage(self, scope, row, key):
        # One large canvas at a time, in addition to the shared bounded work queue.
        async with self.image_lock:
            await self.check(scope)
            if self.plugin.renderer is None:
                return "failed"
            if not self.store.begin_delivery(scope, row["id"], key):
                return "already_requested"
            success = False
            try:
                theme = row["metadata"].get("image_theme", "day")
                render_task = asyncio.create_task(
                    asyncio.to_thread(
                        render_candidate_collage,
                        row["candidates"],
                        self.plugin.renderer,
                        theme,
                    )
                )
                try:
                    png = await asyncio.shield(render_task)
                except asyncio.CancelledError:
                    # A running Pillow thread cannot be cancelled. Keep the large
                    # canvas lock until it exits, including repeated cancellation.
                    while not render_task.done():
                        try:
                            await asyncio.shield(render_task)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not render_task.cancelled():
                        render_task.exception()
                    raise
                await self.check(scope)
                if self.store.get(scope, row["id"])["status"] == "cancelled":
                    raise ValueError("定盘会话已取消，未发送总图")
                sent = await asyncio.wait_for(
                    self.plugin.context.send_message(
                        scope.umo, MessageChain([Image.fromBytes(png)])
                    ),
                    timeout=30,
                )
                success = sent is not False
                return "sent" if success else "failed"
            except PermissionError:
                raise
            except Exception as exc:
                logger.warning(f"定盘总图暂不可用：{type(exc).__name__}")
                return "failed"
            finally:
                if not self.plugin.closed:
                    self.store.finish_delivery(scope, row["id"], key, success)

    async def create(self, scope, event, key, text, birth_info, send_image=False):
        if type(send_image) is not bool:
            raise ValueError("send_image须为布尔值")
        if self.plugin.pending >= 4:
            return {"status": "busy", "message": "排盘请求较多，请稍后重试"}
        self.plugin.pending += 1
        try:
            async with self.plugin.gate:
                row = await self.create_session(scope, event, key, birth_info, ai=True)
                image_status = (
                    await self.send_collage(scope, row, key)
                    if send_image
                    else "not_requested"
                )
            return {
                "status": "ok",
                "session_id": row["id"],
                "session_status": row["status"],
                "candidates": overview(row),
                "archive_status": "saved",
                "image_status": image_status,
                "response_guidance": POLICY,
            }
        finally:
            self.plugin.pending -= 1

    async def get(self, scope, event, key, text, session_id, send_image=False):
        if type(send_image) is not bool:
            raise ValueError("send_image须为布尔值")
        row = self.row(scope, session_id)
        image_status = "not_requested"
        if send_image:
            if self.plugin.pending >= 4:
                return {"status": "busy", "message": "排盘请求较多，请稍后重试"}
            self.plugin.pending += 1
            try:
                async with self.plugin.gate:
                    image_status = await self.send_collage(scope, row, key)
            finally:
                self.plugin.pending -= 1
        return {
            "status": "ok",
            "session_id": row["id"],
            "image_status": image_status,
            "session_status": row["status"],
            "summary_status": row["summary_status"],
            "selected": row["selected"],
            "candidates": overview(row),
            "assessment": row["assessment"],
            "recent_turns": row["turns"][-12:],
            "turn_count": len(row["turns"]),
            "history_note": "仅返回最近12条记录，原始过程完整归档；仅内部参考。",
            "response_guidance": POLICY,
        }

    async def context(
        self,
        scope,
        event,
        key,
        text,
        session_id,
        candidate_ids,
        topic,
        year,
        decade,
        offset,
    ):
        row = self.row(scope, session_id)
        self.usable(row)
        if not isinstance(candidate_ids, str):
            raise ValueError("候选ID须为逗号分隔文本")
        ids = candidate_ids.replace("，", ",").split(",") if candidate_ids else []
        ids = [identifier(c.strip()) for c in ids]
        if len(ids) > 2 or len(ids) != len(set(ids)):
            raise ValueError("每批请选择至多2个不同候选，按批次读取全部候选")
        candidates = {c["id"]: c for c in row["candidates"]}
        ids = ids or list(candidates)[:2]
        if not set(ids) <= set(candidates):
            raise ValueError("候选ID不属于当前定盘会话")
        if type(year) is not int or year != 0 and not 1900 <= year <= 2100:
            raise ValueError("流年须为1900—2100的农历年份，0表示不选择流年")
        if type(decade) is not int or not -1 <= decade <= 12:
            raise ValueError("大运序号须为0—12，-1表示不选择大运")
        if year and decade != -1:
            raise ValueError("流年与大运序号不可同时指定")
        if type(offset) is not int or not 0 <= offset <= 1000:
            raise ValueError("知识分页起点无效")
        if getattr(event, "_ziwei_delivery", None):
            raise ValueError("本轮已关联普通读盘，请分轮进行定盘比较")
        self.check_binding(event, row["id"])
        if self.plugin.pending >= 4:
            return {"status": "busy", "message": "排盘请求较多，请稍后重试"}
        self.plugin.pending += 1
        try:
            async with self.plugin.gate:
                await self.check(scope)

                def compute():
                    result = []
                    for cid in ids:
                        chart = candidates[cid]["chart"]
                        selected_scope = "natal"
                        if year:
                            chart = apply_flow(chart, year=year)
                            selected_scope = "annual"
                        elif decade != -1:
                            chart = apply_decade(chart, decade)
                            selected_scope = "decade"
                        facts = build_facts(chart, selected_scope, topic)
                        result.append(
                            {
                                "candidate_id": cid,
                                "facts": facts,
                                "knowledge": self.knowledge.query(
                                    facts, limit=8, offset=offset
                                ),
                            }
                        )
                    return result

                data = await asyncio.to_thread(compute)
                await self.check(scope)
                self.check_binding(event, row["id"])
                context = {
                    "context_key": json.dumps([ids, topic, year, decade, offset]),
                    "topic": topic,
                    "year": year,
                    "decade": decade,
                    "offset": offset,
                    "candidates": data,
                }
                # All validation and computation precede the transactional charge.
                self.store.record_context(scope, row["id"], key, context, question=text)
                self.mark(event, scope, row["id"], key)
                return {
                    "status": "ok",
                    "session_id": row["id"],
                    "candidates": data,
                    "all_candidate_ids": list(candidates),
                    "response_guidance": POLICY,
                }
        finally:
            self.plugin.pending -= 1

    async def feedback(self, scope, event, key, text, session_id, kind):
        row = self.row(scope, session_id)
        self.usable(row)
        if kind not in {"feedback", "followup"}:
            raise ValueError("消息类型须为feedback或followup")
        self.check_binding(event, row["id"])
        self.store.add_turn(
            scope, row["id"], key, "question" if kind == "followup" else kind, text
        )
        self.mark(event, scope, row["id"], key)
        updated = self.store.get(scope, row["id"])
        return {
            "status": "recorded",
            "session_id": row["id"],
            "recent_turns": updated["turns"][-12:],
            "assessment": updated["assessment"],
            "message": "已登记真实消息。纯反馈只校正已获取的资料，"
            "不新增主题或年份分析。",
        }

    async def assess(self, scope, event, key, text, session_id, assessment_json):
        row = self.row(scope, session_id)
        self.usable(row)
        self.check_binding(event, row["id"])
        if not isinstance(assessment_json, str) or len(assessment_json) > 30000:
            raise ValueError("定盘比较须为不超过30000字的JSON对象")
        value = json.loads(assessment_json)
        allowed = {c["id"] for c in row["candidates"]}
        if not isinstance(value, dict) or not value or not set(value) <= allowed:
            raise ValueError("比较结果须以当前候选ID为键")
        current = [
            t
            for t in row["turns"]
            if t["event_id"] == key and t["kind"] in {"question", "feedback"}
        ]
        if not current:
            raise ValueError("请先登记本轮真实问题或反馈")
        evidence = {t["seq"] for t in row["turns"] if t["kind"] == "feedback"}
        known = {}
        for snapshot in row.get("contexts", row.get("knowledge", [])):
            for item in snapshot.get("candidates", []):
                known.setdefault(item["candidate_id"], set()).update(
                    card["id"] for card in item["knowledge"]["cards"]
                )
        fields = {
            "support",
            "conflict",
            "unknown",
            "evidence_sequences",
            "knowledge_ids",
        }
        for cid, evaluation in value.items():
            if (
                cid not in known
                or not isinstance(evaluation, dict)
                or set(evaluation) != fields
            ):
                raise ValueError(
                    "只可比较已读取事实的候选，须包含支持、冲突、未知、反馈序号与知识ID"
                )
            for name in ("support", "conflict", "unknown"):
                items = evaluation[name]
                if (
                    not isinstance(items, list)
                    or len(items) > 8
                    or any(
                        not isinstance(v, str) or not v.strip() or len(v) > 500
                        for v in items
                    )
                ):
                    raise ValueError("每类比较项最多8条，每条1—500字")
            seqs, cards = evaluation["evidence_sequences"], evaluation["knowledge_ids"]
            if not isinstance(seqs, list) or any(
                type(v) is not int or v not in evidence for v in seqs
            ):
                raise ValueError("比较只能引用已登记的真实反馈序号")
            if not isinstance(cards, list) or any(
                not isinstance(v, str) or v not in known[cid] for v in cards
            ):
                raise ValueError("比较只能引用该候选已查阅的知识ID")
            if (evaluation["support"] or evaluation["conflict"]) and (
                not seqs or not cards
            ):
                raise ValueError(
                    "支持或冲突判断必须引用实际反馈和所用知识；不足时记入未知"
                )
        self.store.save_assessment(scope, row["id"], key, value)
        self.mark(event, scope, row["id"], key)
        return {
            "status": "saved",
            "session_id": row["id"],
            "assessment": value,
            "message": "比较已归档，所有候选仍保留；未设为当前命盘。",
        }

    async def finish(self, scope, event, key, text, session_id, feedback_complete):
        row = self.row(scope, session_id)
        self.check_binding(event, row["id"])
        if feedback_complete is not True:
            raise ValueError("仅主AI确认用户反馈闭环后才能结束")
        if row["summary_status"] in {"queued", "running", "archived"}:
            return {"status": row["summary_status"], "session_id": row["id"]}
        self.usable(row)
        state = self.store.enqueue(scope, row["id"], key, feedback_text=text)
        self.worker.wake()
        event._ziwei_rectification_delivery = None
        return {
            "status": state,
            "session_id": row["id"],
            "message": "独立总结已排队，候选选择仍由管理员确认。",
        }

    @staticmethod
    def check_binding(event, sid):
        if getattr(event, "_ziwei_delivery", None):
            raise ValueError("一次回答不能同时关联普通读盘和定盘")
        previous = getattr(event, "_ziwei_rectification_delivery", None)
        if previous and previous[1] != sid:
            raise ValueError("一次回答只能关联一个定盘会话")

    def mark(self, event, scope, sid, key):
        self.check_binding(event, sid)
        event._ziwei_rectification_delivery = (scope, sid, key)
        install_capture(
            event,
            lambda text: self.capture(event, text),
            lambda: self.capture_failed(event),
            key="rectification",
        )

    async def capture(self, event, text):
        delivery = getattr(event, "_ziwei_rectification_delivery", None)
        if not delivery or self.plugin.closed or not text.strip():
            return
        scope, sid, key = delivery
        try:
            await self.check(scope)
            self.store.add_turn(scope, sid, key, "answer", text)
        except Exception:
            self.capture_failed(event)

    def capture_failed(self, event):
        delivery = getattr(event, "_ziwei_rectification_delivery", None)
        if delivery and not self.plugin.closed:
            try:
                self.store.block_capture(delivery[0], delivery[1])
            except Exception:
                pass

    async def on_request(self, event, request):
        try:
            scope = await self.authorize(event)
            active = self.store.active(scope)
            request.system_prompt = (request.system_prompt or "") + "\n" + POLICY
            if active:
                request.extra_user_content_parts.append(
                    TextPart(
                        text="本人本群的定盘会话（仅相关提问才使用）："
                        + json.dumps(active, ensure_ascii=False)
                    ).mark_as_temp()
                )
        except Exception:
            return

    async def run(self, event, args):
        claim_command(event, False)
        text = str(args).strip()
        if text.lower() in {"帮助", "help", "?", "？"}:
            yield event.plain_result(HELP)
            return
        plugin = self.plugin
        counted = False
        try:
            scope = capture_group(event)
            key = event_key(event)
            await self.check(scope)
            parts = text.split()
            action = parts[0] if parts else "状态"
            if action in {"状态", "总图", "查看", "确认", "取消", "重试总结"}:
                has_candidate = action in {"查看", "确认"}
                minimum = 2 if has_candidate else 1
                if parts and not minimum <= len(parts) <= minimum + 1:
                    raise ValueError("子命令参数有误，请查看 /紫微 定盘 帮助")
                cid = identifier(parts[1]) if has_candidate else ""
                sid = parts[minimum] if len(parts) > minimum else ""
                row = self.row(scope, sid)
                if action == "总图":
                    if plugin.pending >= 4:
                        raise ValueError("排盘请求较多，请稍后重试")
                    plugin.pending += 1
                    counted = True
                    async with plugin.gate:
                        state = await self.send_collage(scope, row, key)
                    if state == "failed":
                        yield event.plain_result(
                            "总图暂未发送，请稍后重试；候选档案仍保留。"
                        )
                    elif state == "already_requested":
                        yield event.plain_result("本条消息已请求过总图，无需重复发送。")
                    return
                if action == "取消":
                    self.store.cancel(scope, row["id"])
                    yield event.plain_result("定盘会话已取消，永久档案保留。")
                    return
                if action == "重试总结":
                    state = self.store.retry(scope, row["id"])
                    self.worker.wake()
                    yield event.plain_result(f"定盘总结状态：{STATE_LABELS[state]}")
                    return
                if has_candidate:
                    candidate = next(
                        (c for c in row["candidates"] if c["id"] == cid), None
                    )
                    if candidate is None:
                        raise ValueError("候选ID不属于当前会话")
                    if action == "确认":
                        chart_id = self.store.select(scope, row["id"], cid)
                        chart = plugin.archive.get_chart(scope, chart_id)["chart"]
                        plugin.chart_cache.put(scope, chart, chart_id=chart_id)
                        yield event.plain_result(
                            f"已选择 {cid}：{candidate['label']}，设为当前命盘。\n"
                            "这是所选范围的代表盘，不代表已经确认精确出生时刻。"
                        )
                        return
                    if plugin.renderer is None:
                        raise ValueError("中文字体暂不可用，请配置字体后查看候选图片")
                    if plugin.pending >= 4:
                        raise ValueError("排盘请求较多，请稍后重试")
                    plugin.pending += 1
                    counted = True
                    async with plugin.gate:
                        await self.check(scope)
                        theme = row["metadata"].get(
                            "image_theme", plugin.config.get("image_theme", "day")
                        )
                        png = await asyncio.to_thread(
                            plugin.renderer.render, candidate["chart"], theme
                        )
                        await self.check(scope)
                        yield event.plain_result(
                            f"候选 {cid}：{candidate['label']}\n"
                            "图片时间为范围代表，不是确定出生时刻。"
                        )
                        await self.check(scope)
                        yield event.chain_result([Image.fromBytes(png)])
                    return
            else:
                if plugin.pending >= 4:
                    raise ValueError("排盘请求较多，请稍后重试")
                plugin.pending += 1
                counted = True
                async with plugin.gate:
                    row = await self.create_session(scope, event, key, text)
            lines = [
                f"定盘会话：{row['id']}",
                f"状态：{STATE_LABELS[row['status']]}；"
                f"总结：{STATE_LABELS[row['summary_status']]}",
            ]
            lines += [f"{c['id']}：{c['label']}" for c in row["candidates"]]
            lines += [
                "候选按钟表范围和历法边界展开，图片中的时间仅为范围代表。",
                "请 @机器人说“开始定盘”，随后按提问提供已发生的经历和年月。",
                "查看或确认：/紫微 定盘 查看 c01；/紫微 定盘 确认 c01。",
                "全部候选拼图：/紫微 定盘 总图。",
            ]
            await self.check(scope)
            report = "\n".join(lines)
            for offset in range(0, len(report), 2500):
                await self.check(scope)
                yield event.plain_result(report[offset : offset + 2500])
        except (PermissionError, ValueError) as exc:
            yield event.plain_result(str(exc))
        except Exception as exc:
            logger.warning(f"定盘请求失败：{type(exc).__name__}")
            yield event.plain_result("定盘暂时失败，请稍后重试；已归档会话仍保留。")
        finally:
            if counted:
                plugin.pending -= 1
