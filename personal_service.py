"""Real-event authorization and delivery for persistent personal natal charts."""

import asyncio
import hashlib
import re
import uuid

from astrbot.api import logger
from astrbot.api.message_components import Image

from .access import capture_group, field
from .admin_service import finish_worker
from .compact_commands import normalize_compact_arguments, normalize_help_arguments
from .compact_filter import claim_command
from .engine import apply_decade, apply_flow
from .personal_commands import (
    PERSONAL_HELP,
    display_options,
    natal_request,
    split_personal_command,
)
from .personal_store import BindingError, encode, natal_snapshot
from .renderer import text_chart


class PersonalService:
    def __init__(self, plugin):
        self.plugin = plugin

    def choices(self, scope, action, page):
        rows = self.plugin.archive.bindings.own_choices(scope, page=page)
        if not rows["items"]:
            return (
                f"暂无可选记录，请发送 /紫微 {action} 199006150830 男"
                "（虚构示例，请替换）。"
            )
        lines = [f"请选择自己的本命盘（第{rows['page']}/{rows['pages']}页）："]
        for row in rows["items"]:
            summary = row["summary"]
            lines += [
                f"{summary['civil']} · {summary['sex']} · "
                f"群 {row['group_id'] or '历史未记录'}",
                f"/紫微 {action} 命盘={row['id'][:12]}",
            ]
        if rows["page"] < rows["pages"]:
            lines.append(f"下一页：/紫微 {action} 页={rows['page'] + 1}")
        lines.append("流年、大运或图谱记录仅提取本命盘；不会保留流盘叠层。")
        return "\n".join(lines)

    async def run(self, event, args, *, quiet=False):
        action, tail = split_personal_command(str(args).strip())
        normalized = normalize_compact_arguments(tail)
        tail, quiet = normalized.args, quiet or normalized.changed
        claim_command(event, quiet)
        if normalize_help_arguments(tail) is not None:
            yield event.plain_result(PERSONAL_HELP)
            return
        plugin = self.plugin
        try:
            scope = capture_group(event)
            message_id = str(
                field(event.message_obj, "message_id", "") or uuid.uuid4().hex
            )
            rejection = await plugin._chart_access(scope)
            if rejection:
                if not quiet:
                    yield event.plain_result(rejection)
                return
            if not scope.user_id:
                raise BindingError("无法确认当前发送者身份，不能使用个人命盘。")
            store = plugin.archive.bindings
            state = store.state(scope.platform_id, scope.user_id)
            expected = state["revision"]
            actor = encode([scope.platform_id, scope.group_id, scope.user_id])
            fingerprint = hashlib.sha256(encode([action, tail]).encode()).hexdigest()
            mutation = {"绑定": "bind", "换绑": "replace", "解绑": "unbind"}.get(action)
            replay = (
                store.replay(encode(["chat", actor]), message_id, fingerprint)
                if mutation
                else None
            )
            if replay is not None:
                yield event.plain_result(
                    "该操作已处理。"
                    + (
                        "发送 /我的命盘 查看当前绑定。"
                        if replay["active"]
                        else "当前未绑定命盘。"
                    )
                )
                return
            if action == "绑定" and state["active"]:
                raise BindingError(
                    "已有绑定命盘。发送 /我的命盘 查看；修改请使用 /紫微 换绑。"
                )
            if action not in {"绑定", "换绑"} and not state["active"]:
                raise BindingError(
                    "尚未绑定命盘，请使用 /紫微 绑定 出生日期时间 性别，"
                    "或 /紫微 绑定 从自己的记录中选择。"
                )
            if action == "换绑" and not state["active"]:
                raise BindingError("尚未绑定命盘，请先使用 /紫微 绑定。")
            if action in {"绑定", "换绑"} and (
                not tail or re.fullmatch(r"页=[1-9][0-9]{0,5}", tail)
            ):
                yield event.plain_result(
                    self.choices(scope, action, int(tail[2:]) if tail else 1)
                )
                return
            if action == "解绑":
                if tail:
                    raise BindingError("解绑无需附加参数，请发送 /紫微 解绑。")
                store.mutate(
                    scope.platform_id,
                    scope.user_id,
                    action=mutation,
                    expected=expected,
                    actor=actor,
                    via="chat",
                    request_id=message_id,
                    fingerprint=fingerprint,
                )
                yield event.plain_result(
                    "已解除绑定，历史起盘记录仍保留。需要时可重新绑定。"
                )
                return
            if plugin.pending >= 4:
                raise BindingError("排盘请求较多，请稍后重试。")
        except ValueError as exc:
            if not quiet:
                yield event.plain_result(str(exc))
            return
        except Exception as exc:
            logger.error(f"个人命盘授权或读取失败：{type(exc).__name__}")
            if not quiet:
                yield event.plain_result("个人命盘暂不可用，请稍后重试。")
            return
        plugin.pending += 1
        config, renderer = dict(plugin.config), plugin.renderer
        try:
            async with plugin.gate:
                rejection = await plugin._chart_access(scope)
                if rejection:
                    raise BindingError(rejection)
                store.assert_current(scope.platform_id, scope.user_id, expected)
                new_record, source_record, source_group = False, "", ""
                if action in {"绑定", "换绑"}:
                    if tail.startswith("命盘="):
                        selection, _, options = tail.partition(" ")
                        if not re.fullmatch(r"[a-f0-9]{8,32}", selection[3:]):
                            raise BindingError("请填写列表中的命盘短编号或完整编号。")
                        rows = store.own_choices(scope, selection[3:])
                        if rows["total"] != 1:
                            raise BindingError(
                                "未找到唯一的本人记录，请重新发送 /紫微 "
                                + action
                                + " 选择。"
                            )
                        row = rows["items"][0]
                        chart = natal_snapshot(row["chart"])
                        source_record, source_group = row["id"], row["group_id"]
                        output, theme = display_options(options, config)
                    else:
                        request = natal_request(tail, config)
                        chart = await finish_worker(plugin._compute_chart(request))
                        output, theme = request.output, request.image_theme
                        new_record, source_group = True, scope.group_id
                else:
                    chart = state["chart"]
                    options = tail
                    if action in {"流年", "大运"}:
                        number, _, options = tail.partition(" ")
                        if not re.fullmatch(
                            r"[0-9]{4}" if action == "流年" else r"(?:[0-9]|1[0-2])",
                            number,
                        ):
                            raise BindingError(
                                "请发送 /紫微 流年 2026（农历年）。"
                                if action == "流年"
                                else "请发送 /紫微 大运 2；0为童限，1—12为大运序号。"
                            )
                        if action == "流年":
                            chart = await finish_worker(
                                asyncio.to_thread(apply_flow, chart, year=int(number))
                            )
                        else:
                            chart = await finish_worker(
                                asyncio.to_thread(apply_decade, chart, int(number))
                            )
                    output, theme = display_options(options, config)
                png = None
                if output == "image" and renderer is not None:
                    try:
                        png = await finish_worker(
                            asyncio.to_thread(renderer.render, chart, theme)
                        )
                    except Exception:
                        pass  # Do not log birth data or rendering arguments.
                rejection = await plugin._chart_access(scope)
                if rejection:
                    raise BindingError(rejection)
                store.assert_current(scope.platform_id, scope.user_id, expected)
                notice = ""
                if mutation:
                    state = store.mutate(
                        scope.platform_id,
                        scope.user_id,
                        action=mutation,
                        expected=expected,
                        actor=actor,
                        via="chat",
                        request_id=message_id,
                        fingerprint=fingerprint,
                        chart=chart,
                        source_record=source_record,
                        source_group=source_group,
                        user_name=scope.user_name,
                        new_record=new_record,
                        scope=scope,
                        theme=theme,
                    )
                    expected = state["revision"]
                    notice = (
                        "已绑定本命盘。" if action == "绑定" else "已更换绑定本命盘。"
                    )
                    notice += "同一机器人内各已开启的群均可发送 /我的命盘 查看。"
                elif action in {"流年", "大运"}:
                    chart_id = plugin.archive.save_chart(scope, chart, theme=theme)
                    plugin.chart_cache.put(scope, chart, chart_id=chart_id)
                if notice:
                    yield event.plain_result(notice)
                    rejection = await plugin._chart_access(scope)
                    if rejection:
                        return
                    store.assert_current(scope.platform_id, scope.user_id, expected)
                if png:
                    yield event.chain_result([Image.fromBytes(png)])
                else:
                    prefix = (
                        "图片暂不可用，先返回文字盘。\n" if output == "image" else ""
                    )
                    yield event.plain_result(prefix + text_chart(chart))
        except ValueError as exc:
            if not quiet:
                yield event.plain_result(
                    f"个人命盘：{exc}\n发送 /紫微 绑定 帮助 查看用法。"
                )
        except Exception as exc:
            logger.error(f"个人命盘处理失败：{type(exc).__name__}")
            if not quiet:
                yield event.plain_result("个人命盘暂时处理失败，请稍后重试。")
        finally:
            plugin.pending -= 1
