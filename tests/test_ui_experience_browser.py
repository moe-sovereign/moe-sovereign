"""Shared UI behavior, local asset loading and responsive layout with synthetic data.

Run: python3 -m unittest tests.test_ui_experience_browser -v
Requires local Jinja2, Playwright and Chromium; never uses real credentials/services.
"""
import functools
import json
import os
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
import unittest
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

ADMIN = Path(__file__).resolve().parents[1] / 'admin_ui'


class FixtureHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path in self.server.pages:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(self.server.pages[path])
        elif path.startswith('/static/'):
            super().do_GET()
        else:
            data = [] if 'servers/health' in path else {}
            if path == '/user/api/budget':
                data = dict(daily_used=200, monthly_used=300, total_used=500)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

    def log_message(self, *_):
        pass


class UiExperienceBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lang = json.loads((ADMIN / 'lang/de_DE.lang').read_text())
        env = Environment(loader=FileSystemLoader(ADMIN / 'templates'), autoescape=select_autoescape())
        env.globals.update(t=lambda request, key: lang.get(key, key), get_lang=lambda request: 'de_DE')
        cls.base = dict(csrf_token='synthetic', config={}, enabled_profiles_count=0, profiles=[], server_names=['alpha', 'beta'],
            inference_servers=[dict(name=n, url=f'https://{n}.example.test/v1', gpu_count=1) for n in ['alpha', 'beta']],
            user=dict(display_name='Demo Nutzer', username='demo', email='demo@example.test'),
            redis=dict(daily_used=200, monthly_used=300, total_used=500, daily_input=100, daily_output=100),
            budget=dict(daily_limit=1000, monthly_limit=10000, total_limit=100000),
            summary=dict(daily=[dict(day='2026-09-18', prompt_tokens=100, completion_tokens=100)], total=dict(requests=2, tokens=200)),
            keys=[dict(id='demo', key_prefix='demo', label='Development', is_active=True, created_at='2026-09-18', last_used_at='', dynamic_routing=True, local_only_routing=True)],
            can_create_templates=True, can_create_cc_profiles=True, available_cc_profiles=[dict(id='standard', name='Standard')], my_cc_profiles=[], permitted_servers=[],
            user_templates=[], connections=[], usage=[], days=7,
            templates=[dict(id='one', name='Research', experts={}, planner_model='org/' + 'long-model-name-' * 18, judge_model='judge-alpha', _privacy_level='local_only'),
                       dict(id='two', name='Coding', experts={}, planner_model='coder-beta', _privacy_level='external')], expert_categories=[])
        cls.pages = {}
        for path, template, page in [('/', 'dashboard.html', ''), ('/templates', 'expert_templates.html', ''), ('/geo', 'geo.html', '')] + [
            (f'/user/{p}', 'user_portal.html', p) for p in ['dashboard', 'keys', 'connections', 'usage', 'login']]:
            request = SimpleNamespace(session={'authenticated': True, 'user_name': 'Demo'}, url=SimpleNamespace(path=path))
            cls.pages[path] = env.get_template(template).render(**cls.base, request=request, page=page).encode()
        request = SimpleNamespace(session={'authenticated': True}, url=SimpleNamespace(path='/user/dashboard'))
        limited = {**cls.base, 'can_create_templates': False, 'can_create_cc_profiles': False}
        cls.pages['/limited'] = env.get_template('user_portal.html').render(**limited, request=request, page='dashboard').encode()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(FixtureHandler, directory=str(ADMIN)))
        cls.server.pages = cls.pages
        Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.pw = sync_playwright().start()
        cls.addClassCleanup(cls.pw.stop)
        cls.browser = cls.pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), headless=True)
        cls.addClassCleanup(cls.browser.close)
        cls.origin = f'http://127.0.0.1:{cls.server.server_port}'

    def context(self, **kwargs):
        context = self.browser.new_context(service_workers='block', reduced_motion='reduce', **kwargs)
        self.addCleanup(context.close)
        return context

    def visit(self, path='/', **kwargs):
        page = self.context(**kwargs).new_page()
        page.goto(self.origin + path)
        page.wait_for_timeout(80)
        return page

    def test_responsive_assets_and_errors(self):
        report = []
        for theme in ['light', 'dark']:
            context = self.context()
            context.add_init_script(f'localStorage.setItem("moe-theme", "{theme}")')
            page = context.new_page()
            errors, external = [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda r: external.append(r.url) if not r.url.startswith(self.origin) else None)
            # Abort accidental external resource requests before they can leave the machine.
            page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.origin) else route.abort())
            for path in self.pages:
                for width in [375, 768, 1024, 1440]:
                    with self.subTest(path=path, width=width, theme=theme):
                        page.set_viewport_size(dict(width=width, height=1000))
                        page.goto(self.origin + path)
                        page.wait_for_timeout(80)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (path, width))
                        self.assertEqual(page.locator('main').count(), 1)
                        if width == 375 and path.startswith('/user/') and path != '/user/login':
                            self.assertFalse(page.locator('#portal-navigation').evaluate('e => e.open'))
                            self.assertLess(page.locator('.portal-content').bounding_box()['y'], 400)
                        report.append(dict(page=path, theme=theme, width=width, scripts=page.locator('script[src]').evaluate_all('es => es.map(e => new URL(e.src).pathname)')))
                        if os.environ.get('MOE_UI_SCREENSHOTS') and path in ['/', '/templates', '/user/dashboard', '/user/keys'] and width in [375, 1440]:
                            out = Path(os.environ['MOE_UI_SCREENSHOTS']); out.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(out / f'{path.strip("/").replace("/", "-") or "admin"}-{theme}-{width}.png'))
            self.assertEqual(external, [])
            self.assertEqual(errors, [])
        if os.environ.get('MOE_UI_SCREENSHOTS'):
            (Path(os.environ['MOE_UI_SCREENSHOTS']) / 'measurements.json').write_text(json.dumps(report, indent=2))

    def test_search_preserves_submission_and_dirty_state(self):
        page = self.visit()
        snapshot = 'JSON.stringify([...new FormData(document.querySelector("#config-form"))])'
        before = page.evaluate(snapshot)
        page.locator('#server-search').fill('alpha')
        page.locator('#server-search').press('Enter')
        self.assertEqual(page.url, self.origin + '/')
        self.assertEqual(page.locator('#server-tbody tr:visible').count(), 1)
        self.assertEqual(page.evaluate(snapshot), before)
        self.assertIn('Keine', page.locator('#config-save-status').inner_text())
        page.locator('[name="srv_name_0"]').fill('updated')
        self.assertEqual(page.locator('#config-save-status').inner_text(), 'Ungespeicherte Änderungen')
        page.locator('[name="srv_name_0"]').fill('alpha')
        self.assertIn('Keine', page.locator('#config-save-status').inner_text())
        page.evaluate('addServerRow()')
        self.assertEqual(page.locator('#server-tbody tr:visible').count(), 3)
        self.assertEqual(page.locator('#server-search').input_value(), '')
        self.assertIn('Ungespeicherte', page.locator('#config-save-status').inner_text())
        page.evaluate("removeServerRow(document.querySelector('#server-tbody tr:last-child button'))")
        self.assertIn('Keine', page.locator('#config-save-status').inner_text())
        page.locator('#server-search').fill('not-found')
        self.assertTrue(page.locator('[data-list-empty]').is_visible())

    def test_save_and_discard_contract(self):
        page = self.visit(viewport=dict(width=1440, height=1000))
        self.assertLess(page.locator('#save-btn').bounding_box()['y'], 1000)
        page.locator('[name="srv_name_0"]').fill('changed')
        page.once('dialog', lambda dialog: dialog.dismiss())
        page.locator('#config-discard').click()
        self.assertEqual(page.locator('[name="srv_name_0"]').input_value(), 'changed')
        posted = []
        page.route('**/save', lambda route: (posted.append(route.request.post_data), route.fulfill(status=200, body='<p>Saved fixture</p>')))
        page.locator('#server-search').fill('beta')
        page.locator('#save-btn').click()
        page.wait_for_url('**/save')
        self.assertEqual(len(posted), 1)
        self.assertIn('srv_name_0=changed', posted[0])
        self.assertIn('srv_name_1=beta', posted[0])
        self.assertIn('csrf_token=synthetic', posted[0])

    def test_navigation_filters_and_access(self):
        page = self.visit('/user/dashboard', viewport=dict(width=375, height=900))
        page.locator('#portal-navigation summary').click()
        self.assertTrue(page.locator('.sidebar-link').first.is_visible())
        self.assertEqual(page.locator('[aria-current="page"]').count(), 1)
        page.goto(self.origin + '/limited')
        self.assertEqual(page.locator('.moe-action-card').count(), 1)
        self.assertEqual(page.locator('a[href="/user/connections"]').count(), 0)
        page.goto(self.origin + '/templates')
        page.locator('#template-search').fill('judge-alpha')
        self.assertEqual(page.locator('#templates-list > .col-12:visible').count(), 1)
        page.locator('[data-list-privacy]').select_option('external')
        self.assertEqual(page.locator('#templates-list > .col-12:visible').count(), 0)
        page.locator('#template-search').fill('')
        self.assertEqual(page.locator('#templates-list > .col-12:visible').count(), 1)
        page.locator('[data-list-privacy]').select_option('')
        page.locator('[data-list-density]').select_option('compact')
        self.assertTrue(page.locator('#templates-list').evaluate('e => e.classList.contains("moe-compact")'))
        page.goto(self.origin + '/user/keys')
        page.locator('#key-search').fill('missing')
        self.assertTrue(page.locator('[data-list-empty]').is_visible())

    def test_page_specific_libraries(self):
        for path, expected in [('/user/keys', []), ('/user/connections', []), ('/user/login', []), ('/user/dashboard', ['chart.umd.min.js']), ('/user/usage', ['cytoscape.min.js', 'pipeline_diagram.js'])]:
            page = self.visit(path)
            requested = page.evaluate('performance.getEntriesByType("resource").map(r=>new URL(r.name).pathname.split("/").pop())')
            for name in ['chart.umd.min.js', 'cytoscape.min.js', 'pipeline_diagram.js']:
                self.assertEqual(name in requested, name in expected, (path, name))
        self.assertNotIn('"/static/js/chart.umd.min.js",', (ADMIN / 'app.py').read_text())

    def test_pointer_width_buttons_and_keyboard(self):
        page = self.visit()
        before = page.locator('#servers-table').bounding_box()['width']
        first = page.locator('#servers-table th').first
        width = first.bounding_box()['width']
        page.get_by_role('button', name='Spaltenbreite', exact=True).click()
        page.locator('[data-column-step="24"]').click()
        self.assertGreater(first.bounding_box()['width'], width)
        self.assertAlmostEqual(page.locator('#servers-table').bounding_box()['width'], before, delta=1)
        page.keyboard.press('Escape')
        page.locator('.moe-column-handle').first.focus()
        page.keyboard.press('ArrowLeft')
        self.assertAlmostEqual(page.locator('#servers-table').bounding_box()['width'], before, delta=1)

    def test_no_javascript_fallback(self):
        page = self.visit('/user/keys', java_script_enabled=False, viewport=dict(width=375, height=900))
        self.assertTrue(page.locator('.sidebar-link').first.is_visible())
        self.assertEqual(page.locator('main').count(), 1)
        self.assertFalse(page.locator('[data-list-tools]').is_visible())
        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))


if __name__ == '__main__':
    unittest.main()
