"""Authenticated AstrBot Plugin Pages transport, with no independent web server."""

import json
from functools import wraps

from astrbot.api import logger

from .admin_service import AdminError, AdminService


class AdminPage:
    def __init__(self, plugin):
        self.plugin = plugin
        self.service = AdminService(plugin)
        self.handlers = []

    def register(self):
        register = getattr(self.plugin.context, "register_web_api", None)
        if not callable(register):
            return
        try:
            from astrbot.api import web
        except ImportError:
            logger.warning("紫微控制页需要支持 astrbot.api.web 的 AstrBot 版本。")
            return
        self.web = web
        for route, callback, method in (
            ("manage/overview", self.overview, "GET"),
            ("manage/settings", self.settings, "GET"),
            ("manage/settings/save", self.save, "POST"),
            ("manage/records", self.records, "GET"),
            ("manage/records/<record_id>", self.detail, "GET"),
            ("manage/charts", self.create, "POST"),
        ):
            handler = self.guard(callback)
            register(
                f"/astrbot_plugin_ziwei/{route}", handler, [method], "紫微排盘控制台"
            )
            self.handlers.append(handler)

    def close(self):
        routes = getattr(self.plugin.context, "registered_web_apis", None)
        if isinstance(routes, list):
            routes[:] = [row for row in routes if row[1] not in self.handlers]

    def guard(self, callback):
        @wraps(callback)
        async def guarded(**params):
            try:
                self.service.active()
                if not self.web.request.username:
                    raise AdminError("请先登录 AstrBot 管理后台。", 401)
                if (
                    self.web.request.headers.get("Sec-Fetch-Site", "").lower()
                    == "cross-site"
                ):
                    raise AdminError("请通过 AstrBot 管理后台访问。", 403)
                value = await callback(**params)
                return self.web.json_response(
                    value, headers={"Cache-Control": "no-store"}
                )
            except ValueError as exc:
                # Return parser errors only to this authenticated request; never log.
                return self.web.error_response(
                    str(exc), status_code=getattr(exc, "status", 400)
                )
            except Exception as exc:
                logger.error(f"紫微控制页操作失败：{type(exc).__name__}")
                return self.web.error_response(
                    "操作失败，请刷新后重试。", status_code=500
                )

        return guarded

    async def payload(self):
        if (self.web.request.content_type or "").split(";", 1)[0] != "application/json":
            raise AdminError("请求须为 JSON。", 415)
        raw = await self.web.request.body()
        if len(raw) > 16384:
            raise AdminError("请求过大。", 413)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise AdminError("JSON 格式无效。") from exc
        if not isinstance(value, dict):
            raise AdminError("请求须为 JSON 对象。")
        return value

    async def overview(self):
        return self.plugin.archive.records.overview()

    async def settings(self):
        return self.service.settings()

    async def save(self):
        return await self.service.save_settings(await self.payload())

    async def records(self):
        return self.service.records(self.web.request.query)

    async def detail(self, record_id):
        return await self.service.detail(record_id)

    async def create(self):
        return await self.service.create(
            await self.payload(), self.web.request.username
        )
