"""Browser regression checks using real templates and synthetic data only.

Run: python3 -m unittest tests.test_admin_table_columns_browser -v
Requires Jinja2, Playwright and Chromium (CHROMIUM_PATH may override the path).
No running admin service, credentials or model endpoints are used.
"""

import functools
import json
import os
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
import unittest

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright


ADMIN = Path(__file__).resolve().parents[1] / "admin_ui"
KEY = "moe-column-widths:v2:/:inference-servers:compact"


class FixtureHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.server.html)
        elif self.path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[]" if "servers/health" in self.path else b"{}")
        elif self.path.startswith("/static/"):
            super().do_GET()
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


class AdminTableColumnsBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        translations = json.loads((ADMIN / "lang/de_DE.lang").read_text())
        env = Environment(loader=FileSystemLoader(ADMIN / "templates"), autoescape=select_autoescape())
        env.globals.update(t=lambda request, key: translations.get(key, key), get_lang=lambda request: "de_DE")
        request = SimpleNamespace(session={"authenticated": True}, url=SimpleNamespace(path="/"))
        html = env.get_template("dashboard.html").render(
            request=request, csrf_token="synthetic-test-token", config={},
            enabled_profiles_count=0, profiles=[], server_names=["comparison-node-alpha"],
            inference_servers=[dict(name="comparison-node-alpha", url="https://inference.example.test/v1", gpu_count=1)],
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(FixtureHandler, directory=str(ADMIN)))
        html = html.replace('</main>', '<table id="untouched"><thead><tr><th>Name</th><th>Value</th></tr></thead><tbody><tr><td>Example</td><td><input value="unchanged"></td></tr></tbody></table></main>')
        cls.server.html = html.encode()
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"), headless=True)
        cls.addClassCleanup(cls.browser.close)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block")
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.goto(self.url)
        self.page.wait_for_selector("#servers-table .moe-column-handle")

    def width(self, index=0):
        return self.page.locator("#servers-table thead th").nth(index).bounding_box()["width"]

    def test_resize_keeps_table_width_and_form_data(self):
        page = self.page
        values = page.locator("#config-form").evaluate("form => [...new FormData(form).entries()]")
        before = [self.width(i) for i in (0, 1, 2, 3, 8, 10, 12, 13)]
        table_width = page.locator('#servers-table').bounding_box()['width']
        handle = page.locator("#servers-table .moe-column-handle").first
        handle.scroll_into_view_if_needed()
        box = handle.bounding_box()
        page.mouse.move(box["x"] + 6, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + 46, box["y"] + box["height"] / 2, steps=8)
        page.mouse.up()
        self.assertAlmostEqual(self.width(), before[0] + 40, delta=1)
        self.assertAlmostEqual(self.width(1), before[1] - 40, delta=1)
        self.assertAlmostEqual(page.locator('#servers-table').bounding_box()['width'], table_width, delta=1)
        self.assertEqual(values, page.locator("#config-form").evaluate("form => [...new FormData(form).entries()]"))
        page.reload()
        page.wait_for_selector("#servers-table .moe-column-handle")
        self.assertAlmostEqual(self.width(), before[0] + 40, delta=1)
        handle.press("ArrowLeft")
        self.assertAlmostEqual(self.width(), before[0] + 30, delta=1)
        page.get_by_role("button", name="Spaltenbreiten zurücksetzen").click()
        self.assertAlmostEqual(self.width(), before[0], delta=1)
        self.assertIsNone(page.evaluate("key => localStorage.getItem(key)", KEY))
        for _ in range(20):
            handle.press('Shift+ArrowRight')
        self.assertAlmostEqual(self.width(1), 240, delta=1)
        self.assertAlmostEqual(page.locator('#servers-table').bounding_box()['width'], table_width, delta=1)

    def test_expanded_columns_and_dynamic_rows_preserve_values(self):
        page = self.page
        before = page.locator("#config-form").evaluate("form => [...new FormData(form).entries()]")
        self.assertEqual(page.locator('#servers-table th:visible').count(), 8)
        self.assertFalse(page.locator('[name="srv_token_0"]').is_visible())
        page.get_by_label('Weitere Einstellungen').check()
        self.assertEqual(page.locator('#servers-table th:visible').count(), 14)
        self.assertTrue(page.locator('[name="srv_token_0"]').is_visible())
        page.locator('[name="srv_timeout_0"]').fill('180')
        page.get_by_label('Weitere Einstellungen').uncheck()
        self.assertEqual(page.locator('[name="srv_timeout_0"]').input_value(), '180')
        form = page.locator("#config-form").evaluate("form => [...new FormData(form).entries()]")
        self.assertEqual(len(before), len(form))
        self.assertEqual(dict(form)['srv_timeout_0'], '180')
        page.evaluate("addServerRow()")
        self.assertEqual(page.locator('#server-tbody tr').count(), 2)
        urls = page.locator('#server-tbody input[name^="srv_url_"]')
        self.assertAlmostEqual(urls.nth(0).bounding_box()['width'], urls.nth(1).bounding_box()['width'], delta=1)
        self.assertFalse(page.locator('[name="srv_token_1"]').is_visible())
        urls.nth(1).fill('https://second.example.test/v1')
        page.evaluate("removeServerRow(document.querySelector('#server-tbody tr button'))")
        self.assertEqual(page.locator('[name="srv_url_0"]').input_value(), 'https://second.example.test/v1')
        self.assertEqual(page.locator('#servers-table .moe-column-handle').count(), 7)

    def test_responsive_layout_and_resize_bounds(self):
        page = self.page
        for width in (375, 768, 1024, 1440):
            for theme in ('light', 'dark'):
                for expanded in (False, True):
                    with self.subTest(width=width, theme=theme, expanded=expanded):
                        page.set_viewport_size(dict(width=width, height=1000))
                        page.evaluate("theme => document.documentElement.dataset.bsTheme = theme", theme)
                        page.get_by_label('Weitere Einstellungen').set_checked(expanded)
                        page.wait_for_timeout(50)
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
                        table = page.locator('#servers-table')
                        total = table.bounding_box()['width']
                        handle = page.locator('#servers-table .moe-column-handle').first
                        handle.press('ArrowRight')
                        self.assertAlmostEqual(table.bounding_box()['width'], total, delta=1)
                        self.assertGreaterEqual(self.width(1), 239)
        page.get_by_label('Weitere Einstellungen').uncheck()
        page.get_by_role('button', name='Spaltenbreiten zurücksetzen').click()
        page.locator('#servers-table').scroll_into_view_if_needed()
        page.screenshot(path='/tmp/moe-admin-columns-refined.png')

    def test_old_and_invalid_storage_are_ignored(self):
        page = self.page
        before = self.width()
        page.evaluate("localStorage.setItem('moe-column-widths:v1:/:inference-servers', JSON.stringify(Array(14).fill(1600)))")
        for saved in ('{broken', '[null]', '["wrong"]', '[0]'):
            page.evaluate("([key, value]) => localStorage.setItem(key, value)", [KEY, saved])
            page.reload()
            page.wait_for_selector('#servers-table .moe-column-handle')
            self.assertAlmostEqual(self.width(), before, delta=1)
        page.evaluate("() => { Storage.prototype.setItem = () => { throw new Error('storage disabled'); }; }")
        page.locator('#servers-table .moe-column-handle').first.press('ArrowRight')
        self.assertAlmostEqual(self.width(), before + 10, delta=1)

    def test_unrelated_tables_are_not_modified(self):
        page = self.page
        self.assertEqual(page.locator('#untouched colgroup, #untouched .moe-column-handle').count(), 0)
        self.assertIsNone(page.locator('#untouched').get_attribute('style'))
        page.locator('#untouched input').fill('still editable')
        self.assertEqual(page.locator('#untouched input').input_value(), 'still editable')
        self.assertEqual(page.locator('.moe-table-scroll').count(), 1)

    def test_without_javascript_all_settings_remain_available(self):
        context = self.browser.new_context(java_script_enabled=False, viewport=dict(width=1440, height=1000))
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(self.url)
        self.assertEqual(page.locator('#servers-table th:visible').count(), 14)
        self.assertTrue(page.locator('[name="srv_token_0"]').is_visible())
        self.assertTrue(page.locator('[name="srv_timeout_0"]').is_visible())


if __name__ == "__main__":
    unittest.main()
