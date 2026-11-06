"""Dashboard-authorized binding management; never fabricate chat events."""

import asyncio
import hashlib
import re

from .admin_records import chart_summary
from .admin_service import AdminError, finish_worker, text
from .compact_commands import normalize_compact_arguments
from .personal_commands import natal_request
from .personal_store import BindingError, encode, identity


class AdminBindings:
    def __init__(self, service):
        self.service = service
        self.plugin = service.plugin
        self.write_lock = asyncio.Lock()

    def users(self, query):
        self.service.active()
        page = str(query.get("page", "1"))
        if not re.fullmatch(r"[1-9][0-9]{0,5}", page):
            raise AdminError("页码无效。")
        return self.plugin.archive.bindings.users(
            text(query.get("search", ""), "搜索内容"),
            text(query.get("platform", ""), "机器人实例"),
            int(page),
        )

    async def _preview(self, state, renderer, theme):
        row = {
            "id": state["revision"],
            "created": state["updated"],
            "kind": "natal",
            "source": "binding",
            "theme": theme,
            "summary": chart_summary(state["chart"])[1],
            "chart": state["chart"],
        }
        return await self.service._render(row, renderer)

    async def target(self, query):
        self.service.active()
        platform, user = identity(query.get("platform_id"), query.get("user_id"))
        store, plugin = self.plugin.archive.bindings, self.plugin
        state = store.state(platform, user)
        if query.get("render", "0") not in {"0", "1"}:
            raise AdminError("预览选项无效。")
        if query.get("render") != "1" or not state["active"]:
            return store.public(state)
        if plugin.pending >= 4:
            raise AdminError("排盘请求较多，请稍后重试。", 429)
        renderer, theme = plugin.renderer, plugin.config.get("image_theme", "night")
        plugin.pending += 1
        try:
            async with plugin.gate:
                self.service.active()
                preview = await self._preview(state, renderer, theme)
                store.assert_current(platform, user, state["revision"])
                return store.public(state) | {"preview": preview}
        finally:
            plugin.pending -= 1

    async def save(self, payload, actor):
        self.service.active()
        if "record_id" in payload:
            raise AdminError("不支持选择已有命盘，请刷新页面并重新填写出生资料。")
        if set(payload) != {
            "platform_id",
            "user_id",
            "action",
            "revision",
            "request_id",
            "args",
        }:
            raise AdminError("绑定请求格式无效。")
        platform, user = identity(payload["platform_id"], payload["user_id"])
        actor = text(actor, "登录身份", 256, required=True)
        action = text(payload["action"], "操作", 16, required=True)
        if action not in {"bind", "replace", "unbind"}:
            raise AdminError("绑定操作无效。")
        expected = text(payload["revision"], "绑定版本", 32)
        if expected and not re.fullmatch(r"[a-f0-9]{32}", expected):
            raise AdminError("绑定版本无效，请重新载入群友。")
        key = text(payload["request_id"], "请求编号", 80, required=True)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", key):
            raise AdminError("请求编号无效，请刷新页面。")
        args = text(payload["args"], "出生资料", 1024)
        if action == "unbind":
            if args:
                raise AdminError("解绑无需附加命盘资料。")
        elif not args:
            raise AdminError("请重新填写出生日期时间和性别。")
        fingerprint = hashlib.sha256(
            encode({k: v for k, v in payload.items() if k != "request_id"}).encode()
        ).hexdigest()
        plugin, store = self.plugin, self.plugin.archive.bindings
        if plugin.pending >= 4:
            raise AdminError("排盘请求较多，请稍后重试。", 429)
        config = dict(plugin.config)
        plugin.pending += 1
        try:
            async with self.write_lock, plugin.gate:
                self.service.active()
                replay = store.replay(encode(["admin", actor]), key, fingerprint)
                if replay is not None:
                    return store.public(replay) | {"replayed": True}
                old = store.assert_current(platform, user, expected)
                if action == "bind" and old["active"]:
                    raise BindingError("该群友已绑定命盘，请载入后使用换绑。", 409)
                if action != "bind" and not old["active"]:
                    raise BindingError("该群友尚未绑定命盘，请先绑定。", 409)
                chart, theme = None, config.get("image_theme", "night")
                if args:
                    request = natal_request(
                        normalize_compact_arguments(args).args, config
                    )
                    chart = await finish_worker(plugin._compute_chart(request))
                    theme = request.image_theme
                self.service.active()
                state = store.mutate(
                    platform,
                    user,
                    action=action,
                    expected=expected,
                    actor=actor,
                    via="admin",
                    request_id=key,
                    fingerprint=fingerprint,
                    chart=chart,
                    theme=theme,
                )
                return store.public(state)
        finally:
            plugin.pending -= 1

    def history(self, query):
        self.service.active()
        platform, user = identity(query.get("platform_id"), query.get("user_id"))
        page = str(query.get("page", "1"))
        if not re.fullmatch(r"[1-9][0-9]{0,5}", page):
            raise AdminError("页码无效。")
        return self.plugin.archive.bindings.history.list(platform, user, int(page))
