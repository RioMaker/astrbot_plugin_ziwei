"""Authorized direct-command delivery of an archived life atlas."""

import asyncio

from astrbot.api import logger
from astrbot.api.message_components import Image

from .access import capture_group
from .commands import DEMO_BIRTH
from .life_atlas import build_life_atlas
from .life_atlas_commands import parse_life_atlas_request
from .life_atlas_renderer import LifeAtlasRenderer

LIFE_ATLAS_HELP = f"""人生变动图谱
/紫微 图谱 {DEMO_BIRTH}
/人生 199006150830 男
/人生 19900615083045 男 主题=白天
以上均为虚构演示资料，请替换为需要排盘的资料。
支持12位或14位日期时间连写，也支持分开的日期、时刻与性别。
默认深色星图，从出生画到60虚岁所在大运末年；只返回图片。
可用经度、时区、真太阳时、农历等出生选项，不与流年、流盘或运限混用。
/紫微 图谱：优先使用当前本人命盘；多份档案且无当前选择时列出选择。
/人生 命盘=<本人命盘ID>：使用指定档案；可附主题=白天或主题=夜间。
仅已开启的群可用。当前为P1结构候选图谱，不代表已判定重大事件。"""


class LifeAtlasService:
    def __init__(self, plugin):
        self.plugin = plugin

    def _archived_chart(self, scope, chart_id):
        plugin = self.plugin
        if not chart_id:
            cached = plugin.chart_cache.get(scope)
            if cached:
                chart_id = cached.chart_id
            else:
                choices = plugin.archive.chart_choices(scope)
                if len(choices) > 1:
                    lines = ["本人在本群有多份命盘，请指定要生成图谱的命盘："]
                    lines += [f"/人生 命盘={c['id']}" for c in choices[:5]]
                    if len(choices) > 5:
                        lines.append(
                            "还有其他档案，可提供已知命盘ID或重新填写出生资料。"
                        )
                    return None, "\n".join(lines)
                if choices:
                    chart_id = choices[0]["id"]
        row = plugin.archive.get_chart(scope, chart_id)
        if row is None:
            raise ValueError("没有本人在本群的对应命盘，请填写出生日期、时刻和性别。")
        return row, None

    async def run(self, event, args):
        event.stop_event()
        text = str(args).strip()
        if text.lower() in {"帮助", "help", "?", "？"}:
            yield event.plain_result(LIFE_ATLAS_HELP)
            return
        plugin = self.plugin
        try:
            scope = capture_group(event)
            rejection = await plugin._chart_access(scope)
            if rejection:
                yield event.plain_result(rejection)
                return
            if not scope.user_id:
                yield event.plain_result("无法确认当前发送者身份，不能建立图谱档案。")
                return
        except Exception as exc:
            logger.error(f"人生图谱授权检查失败：{type(exc).__name__}")
            yield event.plain_result("群使用白名单暂不可用，请稍后重试。")
            return
        if plugin.pending >= 4:
            yield event.plain_result("排盘请求较多，请稍后重试。")
            return
        plugin.pending += 1
        try:
            request = parse_life_atlas_request(text, plugin.config)
            async with plugin.gate:
                # A queued task must not start work after group closure or reload.
                rejection = await plugin._chart_access(scope)
                if rejection:
                    yield event.plain_result(rejection)
                    return
                source_chart_id = ""
                if request.chart_request is not None:
                    chart = await plugin._compute_chart(request.chart_request)
                else:
                    row, message = self._archived_chart(scope, request.chart_id)
                    if message:
                        yield event.plain_result(message)
                        return
                    chart, source_chart_id = row["chart"], row["id"]
                atlas = await asyncio.to_thread(build_life_atlas, chart)
                rejection = await plugin._chart_access(scope)
                if rejection:
                    yield event.plain_result(rejection)
                    return
                try:
                    renderer = LifeAtlasRenderer(
                        str(plugin.config.get("font_path", "") or "")
                    )
                    png = await asyncio.to_thread(
                        renderer.render, atlas, request.image_theme
                    )
                except (OSError, ValueError) as exc:
                    logger.warning(f"人生图谱图片生成失败：{type(exc).__name__}")
                    yield event.plain_result(
                        "图谱图片暂不可用，请检查中文字体配置后重试。"
                    )
                    return
                rejection = await plugin._chart_access(scope)
                if rejection:
                    yield event.plain_result(rejection)
                    return
                # No await separates this final authorization, atomic save and yield.
                _, chart_id = plugin.archive.save_life_atlas(
                    scope, atlas, request.image_theme, source_chart_id
                )
                # An existing ID must keep the same chart (including flow layers)
                # in cache as in permanent storage; the atlas owns its natal copy.
                cached_chart = chart if source_chart_id else atlas["natal"]
                plugin.chart_cache.put(scope, cached_chart, chart_id=chart_id)
                yield event.chain_result([Image.fromBytes(png)])
        except ValueError as exc:
            yield event.plain_result(f"图谱参数有误：{exc}\n发送 /人生 帮助 查看格式。")
        except Exception as exc:
            logger.error(f"人生图谱生成失败：{type(exc).__name__}")
            yield event.plain_result("图谱暂时生成失败，请稍后重试。")
        finally:
            plugin.pending -= 1
