"""Browser QA with fictional data and a local Dashboard bridge double only.

Requires optional development dependency playwright and an installed Chromium.
No production archive, live AstrBot account, QQ bot or online model is used.
"""

import argparse
import asyncio
import contextvars
import json
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_ziwei.tests import conftest  # noqa: E402

# isort: split

from astrbot_plugin_ziwei.access import capture_group  # noqa: E402
from astrbot_plugin_ziwei.main import ZiweiPlugin  # noqa: E402
from astrbot_plugin_ziwei.tests.conftest import Event  # noqa: E402
from astrbot_plugin_ziwei.tests.helpers import allow  # noqa: E402
from astrbot_plugin_ziwei.tests.test_admin_page import Config, PageContext  # noqa: E402
from astrbot_plugin_ziwei.tests.test_readings import natal  # noqa: E402


class StaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


async def check(args, data_dir, address):
    from playwright.async_api import async_playwright

    conftest._data_path = data_dir
    request_context = contextvars.ContextVar("dashboard_request")

    class Request:
        def __getattr__(self, name):
            return getattr(request_context.get(), name)

    web = SimpleNamespace(
        request=Request(),
        json_response=lambda data, **kwargs: {"data": data, "status": 200},
        error_response=lambda message, status_code=400: {
            "message": message,
            "status": status_code,
        },
    )
    sys.modules["astrbot.api"].web = web
    config = Config(data_dir / "demo-config.json")
    plugin = ZiweiPlugin(PageContext(), config)
    chart = natal()
    for index in range(23):
        event = Event(user=str(10001 + index), group="20001", platform="demo-qq")
        event.message_obj.raw_message["sender"]["nickname"] = "演示群友 " + str(
            index + 1
        )
        plugin.archive.save_chart(capture_group(event), chart)
    malicious = Event(user="10099", group="20002", platform="demo-qq")
    malicious.message_obj.raw_message["sender"]["nickname"] = (
        '<img src=x onerror="alert(1)">'
    )
    malicious_id = plugin.archive.save_chart(capture_group(malicious), chart)
    legacy_id = plugin.archive.save_chart(capture_group(Event()), chart)
    with plugin.archive.transaction() as db:
        db.execute(
            "UPDATE admin_records SET source='legacy',platform_id='',group_id='',"
            "user_id='',user_name='' WHERE id=?",
            (legacy_id,),
        )
    fail_once = False
    fail_binding_once = False

    async def backend(_source, method, endpoint, value):
        nonlocal fail_once, fail_binding_once

        async def body():
            return json.dumps(value).encode("utf-8")

        request = SimpleNamespace(
            username="演示管理员",
            headers={},
            query=value if method == "GET" else {},
            content_type="application/json",
            body=body,
        )
        token = request_context.set(request)
        try:
            params = {}
            if endpoint.startswith("manage/records/"):
                params["record_id"] = endpoint.split("/")[-1]
                endpoint = "manage/records/<record_id>"
            callback = next(
                row[1]
                for row in plugin.context.registered_web_apis
                if row[0] == "/astrbot_plugin_ziwei/" + endpoint and method in row[2]
            )
            result = await callback(**params)
            if result["status"] != 200:
                raise RuntimeError(result["message"])
            if fail_once and endpoint == "manage/charts":
                fail_once = False
                raise RuntimeError("演示网络中断，请再次点击生成命盘。")
            if fail_binding_once and endpoint == "manage/bindings/save":
                fail_binding_once = False
                raise RuntimeError("演示绑定网络中断，请再次点击绑定。")
            return result["data"]
        finally:
            request_context.reset(token)

    bridge_script = """window.AstrBotPluginPage = {
      ready: async () => ({locale:'zh-CN'}),
      apiGet: (endpoint, params={}) => window.demoBackend('GET', endpoint, params),
      apiPost: (endpoint, body) => window.demoBackend('POST', endpoint, body)
    };"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=args.chromium)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1080}, accept_downloads=True
        )
        await context.expose_binding("demoBackend", backend)
        await context.add_init_script(bridge_script)
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(address)
        await page.locator("#create:enabled").wait_for()
        await page.locator("#demo").click()
        await page.locator("#create").click()
        await page.locator("#chart-result img").wait_for()
        await page.locator("#create:enabled").wait_for()
        assert plugin.archive.records.list(source="web")["total"] == 1
        async with page.expect_download() as download:
            await page.locator("#chart-result .download").click()
        saved = await download.value
        await saved.save_as(args.output / "demo-chart.png")
        assert (args.output / "demo-chart.png").read_bytes().startswith(b"\x89PNG")
        await page.screenshot(
            path=str(args.output / "admin-chart-desktop.png"), full_page=True
        )
        await page.locator('[data-tab="records"]').click()
        await page.locator("#filter-source").select_option("group")
        await page.locator('#filter-form button[type="submit"]').click()
        await page.wait_for_function(
            "document.getElementById('record-total').textContent === '共 24 条'"
        )
        await page.locator("#next").click()
        await page.wait_for_function(
            "document.getElementById('page-info').textContent === '2 / 2'"
        )
        assert await page.locator("#record-rows tr").count() == 4
        await page.locator("#filter-search").fill("10099")
        await page.locator('#filter-form button[type="submit"]').click()
        await page.wait_for_function(
            "document.getElementById('record-total').textContent === '共 1 条'"
        )
        assert await page.locator("#record-rows img").count() == 0
        assert "<img src=x" in await page.locator("#record-rows").inner_text()
        await (
            page.locator("#record-rows")
            .get_by_role("button", name="查看命盘", exact=True)
            .click()
        )
        await page.locator("#detail-dialog[open] img").wait_for()
        await page.locator("#close-detail").click()
        # Keep the distributed screenshot readable after checking literal XSS text.
        with plugin.archive.transaction() as db:
            db.execute(
                "UPDATE admin_records SET user_name=? WHERE id=?",
                ("演示群友 24", malicious_id),
            )
        await page.locator("#reset-filters").click()
        await page.wait_for_function(
            "document.getElementById('record-total').textContent === '共 26 条'"
        )
        await page.screenshot(
            path=str(args.output / "admin-records-desktop.png"), full_page=True
        )
        await page.locator('[data-tab="settings"]').click()
        await page.locator("#setting-image_theme").select_option("day")
        await page.locator("#setting-longitude").fill("116.4")
        await page.locator("#save-settings").click()
        await page.wait_for_function(
            "document.getElementById('notice').textContent.includes('配置已保存')"
        )
        assert json.loads(config.path.read_text())["longitude"] == 116.4
        assert plugin.config["image_theme"] == "day"
        await page.locator("#settings-form > details > summary").click()
        assert await page.locator("#setting-void_rule").is_visible()
        await page.locator("#settings-form > details > summary").click()
        await page.screenshot(
            path=str(args.output / "admin-settings-desktop.png"), full_page=True
        )
        await page.locator('[data-tab="chart"]').click()
        fail_once = True
        await page.locator("#create").click()
        await page.wait_for_function(
            "document.getElementById('notice').textContent.includes('演示网络中断')"
        )
        await page.locator("#create").click()
        await page.wait_for_function(
            "document.getElementById('notice').textContent.includes('命盘已生成')"
        )
        await page.locator("#create:enabled").wait_for()
        assert plugin.archive.records.list(source="web")["total"] == 2
        await page.locator("#birth").fill("199013990830")
        await page.locator("#create").click()
        await page.locator("#notice.error").wait_for()
        assert plugin.archive.records.list(source="web")["total"] == 2
        await page.locator("#demo").click()

        await page.locator('[data-tab="bindings"]').click()
        await page.locator("#binding-search-text").fill("10001")
        await page.locator('#binding-search button[type="submit"]').click()
        await page.wait_for_function(
            "document.getElementById('binding-total').textContent === '1 位群友'"
        )
        await page.locator("#binding-rows button").click()
        await page.locator("#binding-save:enabled").wait_for()
        assert await page.locator("#binding-platform").input_value() == "demo-qq"
        await page.locator("#binding-birth").fill("199006150830")
        fail_binding_once = True
        await page.locator("#binding-save").click()
        await page.wait_for_function(
            "document.getElementById('notice').textContent.includes('演示绑定网络中断')"
        )
        await page.locator("#binding-save").click()
        await page.wait_for_function(
            "document.getElementById('binding-state').textContent.includes('已绑定：')"
        )
        await page.locator("#binding-save:enabled").wait_for()
        assert plugin.archive.records.list(source="web")["total"] == 3
        original = plugin.archive.bindings.state("demo-qq", "10001")
        assert original["actor"] == "演示管理员"
        assert await page.locator("#binding-source, #binding-record").count() == 0
        assert (
            await page.get_by_role("button", name="用于绑定", exact=True).count() == 0
        )
        await page.locator("#binding-birth").fill("199207201430")
        await page.locator("#binding-sex").select_option("女")
        await page.locator("#binding-save").click()
        await page.locator("#binding-save:enabled").wait_for()
        assert (
            plugin.archive.bindings.state("demo-qq", "10001")["revision"]
            != original["revision"]
        )
        await page.locator("#binding-unbind").click()
        await page.locator("#binding-save:enabled").wait_for()
        assert not plugin.archive.bindings.state("demo-qq", "10001")["active"]
        assert plugin.archive.records.list(source="web")["total"] == 4
        assert await page.locator("#binding-history-list li").count() == 3
        await page.locator("#binding-birth").fill("199006150830")
        await page.locator("#binding-sex").select_option("男")
        await page.locator("#binding-save").click()
        await page.locator("#binding-save:enabled").wait_for()
        assert await page.locator("#binding-history-list li").count() == 4
        assert plugin.archive.records.list(source="web")["total"] == 5
        replacement = page.locator('#binding-history-list [data-action="replace"]')
        assert "1992-07-20" in await replacement.text_content()
        assert "1990-06-15" in await replacement.text_content()
        await replacement.get_by_role("button", name="查看换绑前命盘").click()
        await page.locator("#detail-dialog[open] img").wait_for()
        assert "1990-06-15" in await page.locator("#detail-result").text_content()
        assert "10001" in await page.locator("#detail-result").text_content()
        await page.locator("#close-detail").click()
        await page.locator("#binding-user-records").click()
        await page.wait_for_function(
            "document.getElementById('record-total').textContent === '共 4 条'"
        )
        assert await page.locator("#filter-user").input_value() == "10001"
        owned = plugin.archive.records.list(platform="demo-qq", user="10001")
        assert owned["total"] == 4
        assert len({row["id"] for row in owned["items"]}) == 4
        await page.locator('[data-tab="bindings"]').click()
        await page.locator("#binding-view").click()
        await page.locator("#binding-result img").wait_for()
        assert plugin.archive.bindings.state("demo-qq", "10001")["active"]
        for group in ("20001", "20002"):
            event = Event(platform="demo-qq", group=group)
            await allow(plugin, event)
            replies = [reply async for reply in plugin.my_chart(event, "")]
            assert replies[0][0].data.startswith(b"\x89PNG")
        await page.screenshot(
            path=str(args.output / "admin-bindings-desktop.png"), full_page=True
        )

        await page.set_viewport_size({"width": 390, "height": 844})
        for tab in ("chart", "records", "settings", "bindings"):
            await page.locator('[data-tab="' + tab + '"]').click()
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), tab
            await page.screenshot(
                path=str(args.output / ("admin-" + tab + "-mobile.png")), full_page=True
            )
        # Exercise the same external module inside an opaque-origin iframe.
        frame_page = await context.new_page()
        await frame_page.set_content(
            '<iframe title="控制台" '
            'sandbox="allow-scripts allow-forms allow-downloads" src="'
            + address
            + '" style="width:100%;height:1000px"></iframe>'
        )
        frame = frame_page.frame_locator("iframe")
        await frame.locator("#create:enabled").wait_for()
        await frame.locator("#demo").click()
        await frame.locator("#create").click()
        await frame.locator("#chart-result img").wait_for()
        assert plugin.archive.records.list(source="web")["total"] == 6
        # Without the Dashboard bridge, the distributed page is inert.
        standalone = await browser.new_context()
        missing = await standalone.new_page()
        await missing.goto(address)
        await missing.locator("#notice.error").wait_for()
        assert await missing.locator("#create").is_disabled()
        assert not errors, errors
        await browser.close()
    await plugin.terminate()
    print(
        json.dumps(
            {
                "result": "passed",
                "screenshots": 8,
                "checks": [
                    "real backend create/config/history",
                    "PNG download",
                    "pagination/search",
                    "XSS text",
                    "network retry deduplication",
                    "birth-only binding, replace/unbind, retry and immutable history",
                    "target user's complete chart records and historical PNG preview",
                    "bound PNG readback across authorized groups",
                    "validation errors",
                    "390px layout",
                    "sandbox iframe",
                    "missing bridge",
                ],
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chromium", default=None)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(StaticHandler, directory=str(ROOT / "pages/manage"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="ziwei-admin-qa-") as folder:
            asyncio.run(
                check(
                    args,
                    Path(folder),
                    "http://127.0.0.1:" + str(server.server_port) + "/",
                )
            )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
