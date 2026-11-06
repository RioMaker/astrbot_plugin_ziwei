"""Dashboard operations, separate from real chat identities and authorization."""

import asyncio
import base64
import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .admin_records import request_fingerprint
from .commands import DEMO_BIRTH, parse_request
from .help_renderer import HelpRenderer
from .life_atlas_renderer import LifeAtlasRenderer
from .renderer import Renderer, text_chart
from .rules import RuleProfile

SCHEMA = json.loads((Path(__file__).parent / "_conf_schema.json").read_text("utf-8"))
KINDS = {"natal", "decade", "year", "date", "atlas"}


class AdminError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text(value, label, maximum=256, *, required=False):
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (required and not value.strip())
    ):
        raise AdminError(f"{label}格式或长度无效。")
    return value.strip()


def revision(settings):
    return hashlib.sha256(
        json.dumps(settings, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def finish_worker(awaitable):
    """Keep the shared slot occupied until a cancelled CPU worker actually stops."""
    task = asyncio.create_task(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


class AdminService:
    def __init__(self, plugin):
        self.plugin = plugin
        self.config_lock = asyncio.Lock()
        self.create_lock = asyncio.Lock()

    def active(self):
        if self.plugin.closed:
            raise AdminError("插件正在重载，请刷新页面后重试。", 503)

    def settings(self):
        self.active()
        values = {
            key: self.plugin.config.get(key, item["default"])
            for key, item in SCHEMA.items()
        }
        return {
            "values": values,
            "schema": SCHEMA,
            "revision": revision(values),
            "can_save": callable(getattr(self.plugin.host_config, "save_config", None)),
            "image_available": self.plugin.renderer is not None,
        }

    @staticmethod
    def validate_settings(values):
        if not isinstance(values, dict) or set(values) != set(SCHEMA):
            raise AdminError("配置项目不完整或包含未知项目，请刷新页面。")
        for key, item in SCHEMA.items():
            value, kind = values[key], item["type"]
            valid = (
                type(value) is bool
                if kind == "bool"
                else type(value) is int
                if kind == "int"
                else type(value) in (int, float) and math.isfinite(value)
                if kind == "float"
                else isinstance(value, str) and len(value) <= 1024
            )
            options = item.get("options")
            if not valid or (isinstance(options, list) and value not in options):
                raise AdminError(f"{item['description']}的值无效。")
        RuleProfile.from_config(values)
        parse_request(DEMO_BIRTH, values)
        return deepcopy(values)

    async def save_settings(self, payload):
        self.active()
        if set(payload) != {"values", "revision"}:
            raise AdminError("配置请求格式无效。")
        values = self.validate_settings(payload["values"])
        async with self.config_lock:
            self.active()
            if payload["revision"] != self.settings()["revision"]:
                raise AdminError("配置已被修改，请重新载入后再保存。", 409)
            host = self.plugin.host_config
            if not callable(getattr(host, "save_config", None)):
                raise AdminError(
                    "当前配置不支持持久保存，请使用 AstrBot 插件配置页。", 503
                )
            renderer = self.plugin.renderer
            try:
                renderer = Renderer(values["font_path"], values["image_theme"])
            except (OSError, ValueError) as exc:
                if values["font_path"] or renderer is not None:
                    raise AdminError("中文字体不可用，请检查字体路径。") from exc
            previous = deepcopy(dict(host))
            try:
                host.update(values)
                host.save_config()
            except Exception as exc:
                host.clear()
                host.update(previous)
                raise AdminError(
                    "配置保存失败，本次修改未应用，请检查配置文件权限。", 500
                ) from exc
            # Swap complete snapshots only after successful persistence.
            self.plugin.config = dict(host)
            self.plugin.renderer = renderer
            self.plugin.help_renderer = (
                HelpRenderer(renderer.font_path) if renderer else None
            )
        return self.settings()

    def records(self, query):
        self.active()
        source = text(query.get("source", ""), "来源")
        kind = text(query.get("kind", ""), "盘型")
        if source not in {
            "",
            "group",
            "web",
            "legacy",
            "binding_import",
        } or kind not in {"", *KINDS}:
            raise AdminError("筛选条件无效。")
        raw_page = query.get("page", "1")
        if not re.fullmatch(r"[0-9]{1,6}", str(raw_page)) or int(raw_page) < 1:
            raise AdminError("页码无效。")
        bounds = []
        for key in ("start", "end"):
            raw = text(query.get(key, ""), "起盘日期", 10)
            try:
                day = date.fromisoformat(raw) if raw else None
                if day and key == "end":
                    day += timedelta(days=1)
                stamp = (
                    datetime.combine(
                        day, time(), timezone(timedelta(hours=8))
                    ).timestamp()
                    if day
                    else None
                )
            except (ValueError, OverflowError) as exc:
                raise AdminError("起盘日期无效。") from exc
            bounds.append(stamp)
        if all(v is not None for v in bounds) and bounds[0] >= bounds[1]:
            raise AdminError("结束日期不能早于开始日期。")
        return self.plugin.archive.records.list(
            page=int(raw_page),
            source=source,
            kind=kind,
            platform=text(query.get("platform", ""), "平台"),
            group=text(query.get("group", ""), "群号"),
            user=text(query.get("user", ""), "用户账号"),
            search=text(query.get("search", ""), "搜索内容"),
            start=bounds[0],
            end=bounds[1],
        )

    async def _render(self, row, renderer):
        self.active()
        image, warning = None, ""
        chart = row["chart"]
        if renderer is not None:
            try:
                target = (
                    LifeAtlasRenderer(renderer.font_path)
                    if row.get("atlas")
                    else renderer
                )
                png = await finish_worker(
                    asyncio.to_thread(
                        target.render, row.get("atlas") or chart, row["theme"]
                    )
                )
                if png:
                    image = "data:image/png;base64," + base64.b64encode(png).decode(
                        "ascii"
                    )
            except Exception:
                pass  # Never log chart inputs, archived snapshots or birth information.
        if not image:
            warning = "图片暂不可用，已保留记录并显示文字盘。请检查中文字体配置。"
        self.active()
        return {
            key: value for key, value in row.items() if key not in {"chart", "atlas"}
        } | {
            "image": image,
            "text": text_chart(chart),
            "warning": warning,
        }

    async def detail(self, record_id):
        self.active()
        if not re.fullmatch(r"[a-f0-9]{32}", record_id):
            raise AdminError("记录不存在。", 404)
        plugin = self.plugin
        if plugin.pending >= 4:
            raise AdminError("排盘请求较多，请稍后重试。", 429)
        plugin.pending += 1
        renderer = plugin.renderer
        try:
            async with plugin.gate:
                self.active()
                row = plugin.archive.records.get(record_id)
                if row is None:
                    raise AdminError("记录不存在。", 404)
                return await self._render(row, renderer)
        finally:
            plugin.pending -= 1

    async def create(self, payload, actor):
        self.active()
        if set(payload) != {"args", "request_id"}:
            raise AdminError("排盘请求格式无效。")
        args = text(payload["args"], "排盘资料", 1024, required=True)
        key = text(payload["request_id"], "请求编号", 80, required=True)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", key):
            raise AdminError("请求编号无效，请刷新页面。")
        actor = text(actor, "登录身份", 256, required=True)
        fingerprint = request_fingerprint(args)
        plugin = self.plugin
        if plugin.pending >= 4:
            raise AdminError("排盘请求较多，请稍后重试。", 429)
        config, renderer = dict(plugin.config), plugin.renderer
        plugin.pending += 1
        try:
            async with self.create_lock, plugin.gate:
                self.active()
                existing = plugin.archive.records.existing_web(actor, key)
                if existing:
                    if existing["fingerprint"] != fingerprint:
                        raise AdminError(
                            "同一请求编号的资料发生变化，请重新排盘。", 409
                        )
                    row = plugin.archive.records.get(existing["id"])
                else:
                    request = parse_request(args, config)
                    chart = await finish_worker(plugin._compute_chart(request))
                    self.active()
                    record_id = plugin.archive.records.save_web(
                        chart,
                        request.image_theme,
                        actor,
                        key,
                        fingerprint,
                    )
                    row = plugin.archive.records.get(record_id)
                return await self._render(row, renderer)
        finally:
            plugin.pending -= 1
