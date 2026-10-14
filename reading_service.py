"""Event-bound reading tools and delivery capture, separate from natal calculation."""

import json
import uuid

from astrbot.core.agent.message import TextPart

from .access import capture_group
from .ai_limits import QuotaExceeded
from .analysis_facts import build_facts, feature_tags
from .delivery_capture import install_capture
from .knowledge import KnowledgeBase
from .llm_data import tool_error

POLICY = """紫微工具使用规则：单纯排盘只返回盘和归档状态，默认不分析。
只有用户明确询问命盘相关内容才调用ziwei_analysis_context，范围为natal/decade/annual。
只回答所问主题，缺少目标大运或农历年份先询问。先获取本人命盘再查询知识，勿编造盘面事实。
知识has_more为true时，可用ziwei_query_knowledge继续获取本次所需条目；不把检索命中当条件已满足。
后续相关问题或反馈用ziwei_continue_reading登记当前真实消息。不得登记其他成员或无关聊天。
当用户已反馈且你判断本次问题闭环时调用ziwei_finish_reading；谢谢不等于预测已验证。
没有用户反馈、尚在追问或未发送分析结果时不得结束。调用后后台归档，不等待总结回答。
ziwei_reference_cases只供内部参考，不得在回复展示参考案例、样本统计、原文或他人资料。
不得将古籍断语当事实，不作死亡预言、医疗诊断或必然收益承诺。"""

POLICY += """
AI限额：每人滚动一小时解盘提问6次、生年排盘2张、大运排盘3张。
新问题和相关追问必须先登记分析工具；同一真实消息重复调用只计一次提问。
收到rate_limited必须告知剩余等待秒数并停止本次分析，不能改用读盘或历史资料绕过。
纯反馈不算问题；反馈中含新问题须作为followup登记，不能假报feedback绕过。
重发已排好的盘用ziwei_get_chart(send_image=true)，不重新调用排盘工具。
不得代用户执行指令排盘绕过AI额度；用户直接发送排盘指令不受AI次数限制。
"""


def event_text(event):
    getter = getattr(event, "get_message_str", None)
    value = getter() if callable(getter) else getattr(event, "message_str", "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("无法取得当前真实用户消息")
    return value.strip()


def event_key(event):
    # A private per-event token, never supplied by an LLM parameter.
    if not getattr(event, "_ziwei_event_key", None):
        message_id = getattr(event.message_obj, "message_id", "")
        event._ziwei_event_key = str(message_id) if message_id else uuid.uuid4().hex
    return event._ziwei_event_key


class ReadingService:
    def __init__(self, plugin):
        self.plugin = plugin
        self.knowledge = KnowledgeBase()

    async def authorize(self, event):
        scope = capture_group(event)
        rejection = await self.plugin._chart_access(scope)
        if rejection or not scope.user_id:
            raise PermissionError(rejection or "无法确认发送者")
        return scope

    async def call(self, action, event, **kwargs):
        try:
            scope = await self.authorize(event)
            result = await getattr(self, action)(scope, event, **kwargs)
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except PermissionError as exc:
            return tool_error("denied", str(exc))
        except QuotaExceeded as exc:
            return exc.payload()
        except (ValueError, TypeError) as exc:
            return tool_error("invalid_input", str(exc))
        except Exception:
            return tool_error("unavailable", "档案或分析资料暂不可用，请勿猜测结果")

    async def list_archives(self, scope, event, offset):
        if type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError("档案分页起点无效")
        charts = self.plugin.archive.list_charts(scope, limit=11, offset=offset)
        return {
            "status": "ok",
            "charts": charts[:10],
            "next_offset": offset + 10 if len(charts) > 10 else None,
            "readings": self.plugin.archive.active_readings(scope),
        }

    async def prepare(self, scope, event, chart_id, reading_scope, topic):
        chart_id = self._identifier(chart_id, optional=True)
        row = self.plugin.archive.get_chart(scope, chart_id)
        if row is None:
            return {"status": "not_found", "message": "请先按明确资料排盘"}
        facts = build_facts(row["chart"], reading_scope, topic)
        knowledge = self.knowledge.query(facts)
        for rid in getattr(event, "_ziwei_delivery", {}) or {}:
            previous = self.plugin.archive.get_reading(scope, rid)
            if (previous["chart_id"], previous["scope"], previous["topic"]) != (
                row["id"],
                reading_scope,
                topic,
            ):
                raise ValueError("一次回答仅关联一张命盘的一个分析范围，请分轮提问")
        reading_id = self.plugin.archive.start(
            scope,
            row["id"],
            reading_scope,
            topic,
            event_key(event),
            event_text(event),
            knowledge,
            feature_tags(facts),
        )
        self.mark_delivery(event, scope, reading_id)
        return {
            "status": "ok",
            "reading_id": reading_id,
            "chart_id": row["id"],
            "facts": facts,
            "knowledge": knowledge,
            "response_guidance": POLICY,
        }

    @staticmethod
    def _identifier(value, optional=False):
        if (
            not isinstance(value, str)
            or (not value and not optional)
            or len(value) > 64
        ):
            raise ValueError("档案ID无效")
        return value.strip()

    def mark_delivery(self, event, scope, reading_id):
        if not getattr(event, "_ziwei_delivery", None):
            event._ziwei_delivery = {}
        event._ziwei_delivery[reading_id] = (scope, event_key(event))
        install_capture(
            event,
            lambda text: self.capture_answer(event, text),
            lambda: self.capture_failed(event),
        )

    async def history(self, scope, event, chart_id, offset):
        if type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError("档案分页起点无效")
        result = self.plugin.archive.history(
            scope, self._identifier(chart_id, optional=True), offset
        )
        self.plugin.archive.charge_question(scope, event_key(event))
        return result

    async def query_knowledge(self, scope, event, reading_id, offset):
        reading_id = self._identifier(reading_id)
        row = self.plugin.archive.get_reading(scope, reading_id)
        if row is None or row["status"] != "active":
            raise ValueError("请先建立当前问题的分析记录")
        chart = self.plugin.archive.get_chart(scope, row["chart_id"])
        facts = build_facts(chart["chart"], row["scope"], row["topic"])
        knowledge = self.knowledge.query(facts, offset=offset)
        self.plugin.archive.record_knowledge(
            scope, reading_id, knowledge, event_key(event)
        )
        return {"status": "ok", "knowledge": knowledge}

    async def continue_reading(self, scope, event, reading_id, kind):
        reading_id = self._identifier(reading_id)
        if kind not in {"followup", "feedback"}:
            raise ValueError("消息类型须为followup或feedback")
        delivery = getattr(event, "_ziwei_delivery", {}) or {}
        if delivery and reading_id not in delivery:
            raise ValueError("一次回答仅关联一个分析记录，请分轮提问")
        self.plugin.archive.add_turn(
            scope, reading_id, event_key(event), kind, event_text(event)
        )
        if kind == "feedback":
            return {
                "status": "ok",
                "reading_id": reading_id,
                "message": "已记录反馈；不新增分析。反馈闭环时调用结束工具。",
            }
        self.mark_delivery(event, scope, reading_id)
        row = self.plugin.archive.get_reading(scope, reading_id)
        chart = self.plugin.archive.get_chart(scope, row["chart_id"])
        facts = build_facts(chart["chart"], row["scope"], row["topic"])
        return {
            "status": "ok",
            "reading_id": reading_id,
            "facts": facts,
            "knowledge": json.loads(row["knowledge"]),
        }

    async def finish(self, scope, event, reading_id, feedback_complete):
        reading_id = self._identifier(reading_id)
        if feedback_complete is not True:
            raise ValueError("只有确认用户反馈闭环后才能归档")
        row = self.plugin.archive.get_reading(scope, reading_id)
        if row is None:
            return {"status": "not_found"}
        if row["status"] in {"queued", "running", "archived"}:
            return {"status": row["status"], "reading_id": reading_id}
        if row["status"] == "active":
            self.plugin.archive.add_turn(
                scope, reading_id, event_key(event), "feedback", event_text(event)
            )
        else:
            raise ValueError("总结失败，请使用ziwei_retry_summary重试原任务")
        status = self.plugin.archive.enqueue(scope, reading_id, event_key(event), "")
        # No LLM await in this call. The consumer resolves its own provider.
        self.plugin.summary_worker.wake()
        if getattr(event, "_ziwei_delivery", None):
            event._ziwei_delivery.pop(reading_id, None)
        return {
            "status": status,
            "reading_id": reading_id,
            "message": "独立总结已排队，主对话可继续",
        }

    async def retry(self, scope, event, reading_id):
        reading_id = self._identifier(reading_id)
        row = self.plugin.archive.get_reading(scope, reading_id)
        if row is None or row["status"] != "failed":
            raise ValueError("只有本人已失败的总结任务可以重试")
        feedback = [t for t in row["turns"] if t["kind"] == "feedback"]
        status = self.plugin.archive.enqueue(
            scope, reading_id, feedback[-1]["event_id"], ""
        )
        self.plugin.summary_worker.wake()
        return {"status": status}

    async def reference(self, scope, event, reading_id):
        row = self.plugin.archive.get_reading(scope, self._identifier(reading_id))
        if row is None or row["status"] != "active":
            raise ValueError("请先针对当前问题建立分析记录")
        self.plugin.archive.charge_question(scope, event_key(event))
        return self.plugin.archive.shared_reference(
            scope, row["topic"], row["scope"], json.loads(row["features"])
        )

    async def on_request(self, event, request):
        try:
            scope = await self.authorize(event)
            install_capture(
                event,
                lambda text: self.capture_answer(event, text),
                lambda: self.capture_failed(event),
            )
            active = self.plugin.archive.active_readings(scope)
            request.system_prompt = (request.system_prompt or "") + "\n" + POLICY
            if active:
                request.extra_user_content_parts.append(
                    TextPart(
                        text="本人当前分析记录："
                        + json.dumps(active, ensure_ascii=False)
                    ).mark_as_temp()
                )
        except Exception:
            return

    def capture_failed(self, event):
        if self.plugin.closed:
            return
        for reading_id, (scope, _) in (
            getattr(event, "_ziwei_delivery", {}) or {}
        ).items():
            try:
                self.plugin.archive.block_capture(scope, reading_id)
            except Exception:
                pass

    async def capture_answer(self, event, text):
        delivery = getattr(event, "_ziwei_delivery", None)
        if delivery is None or self.plugin.closed:
            return
        try:
            if text.strip():
                for reading_id, (scope, key) in delivery.items():
                    if not await self.plugin._chart_access(scope):
                        self.plugin.archive.add_turn(
                            scope, reading_id, key, "answer", text
                        )
        except Exception:
            self.capture_failed(event)
