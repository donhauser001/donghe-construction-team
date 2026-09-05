import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BROWSER = Path(os.environ.get('DONGHE_UI_TEST_BINARY',
    '/Users/aiden/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'))
spec = importlib.util.spec_from_file_location('donghe_ui', ROOT / 'scripts/donghe_ui.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class Project:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, relative):
        return self.root / relative


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/second':
            body = b'<!doctype html><p id="second">same origin path</p>'
        elif self.path == '/long':
            body = (('<!doctype html>' + '<div style="height:100px">card</div>' * 60 +
                     '<button id="bottom" onclick="document.querySelector(\'#longresult\').textContent=\'changed\'">bottom</button>' +
                     '<p id="longresult">before</p>').encode())
        else:
            body = f'''<!doctype html><meta charset="utf-8"><title>fixture</title>
        <input id="name" type="password"><button id="apply" onclick="document.querySelector('#result').textContent='applied';document.querySelector('#hidden').hidden=false;console.log('applied')">apply</button>
        <button id="leave" onclick="location.href='http://example.com/'">leave</button>
        <button id="same" onclick="location.href='/second'">same</button>
        <button id="attack" onclick="fetch('http://0.0.0.0:{self.server.sink_port}/leak').catch(()=>{{}});setTimeout(()=>{{throw new Error('boom')}},50)">attack</button>
        <button id="wsattack" onclick="const ws=new WebSocket('ws://0.0.0.0:{self.server.sink_port}/leak');ws.onerror=()=>{{}}">wsattack</button>
        <button id="allowedfetch" onclick="fetch('http://127.0.0.1:{self.server.sink_port}/allowed').catch(()=>{{}})">allowedfetch</button>
        <p id="result">before</p><p id="hidden" hidden>now visible</p>'''.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@unittest.skipUnless(BROWSER.is_file(), 'author acceptance Chrome is unavailable')
class BrowserAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Sink(BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.hits += 1
                self.send_response(204)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Private-Network', 'true')
                self.end_headers()
            def log_message(self, *_args):
                pass
        cls.sink = ThreadingHTTPServer(('127.0.0.1', 0), Sink)
        cls.sink.hits = 0
        cls.sink_thread = threading.Thread(target=cls.sink.serve_forever, daemon=True)
        cls.sink_thread.start()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.sink_port = cls.sink.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.sink.shutdown()
        cls.sink.server_close()
        cls.sink_thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Project(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_real_click_changes_ui_then_screenshot(self):
        result = ui.run(self.project, {'url': self.url, 'authorization': '测试真实交互链', 'timeout': 30, 'steps': [
            {'action': 'fill', 'selector': '#name', 'value': '真实变化', 'secret': True},
            {'action': 'click', 'selector': '#apply'},
            {'action': 'assert_text', 'selector': '#result', 'text': 'applied'},
            {'action': 'assert_visible', 'selector': '#hidden'},
            {'action': 'screenshot', 'name': 'after.png'},
        ]}, str(BROWSER))
        self.assertEqual(result['status'], 'passed', result)
        self.assertTrue(all(step['passed'] for step in result['steps']))
        self.assertEqual(result['screenshots'][0]['path'], 'after.png')
        run_dir = self.project.root / Path(result['resultPath']).parent
        self.assertGreater((run_dir / 'after.png').stat().st_size, 1000)
        persisted = json.loads((run_dir / 'result.json').read_text())
        self.assertEqual(persisted['status'], 'passed')
        self.assertTrue(any('applied' in item.get('values', []) for item in persisted['console']))
        self.assertNotIn('真实变化', json.dumps(persisted['contract'], ensure_ascii=False))
        self.assertIn('valueSha256', persisted['contract']['steps'][0])
        self.assertEqual(len(persisted['contractSha256']), 64)
        self.assertTrue(persisted['startedAt'].endswith('+00:00'))
        self.assertTrue(persisted['finishedAt'].endswith('+00:00'))

    def test_assertion_failure_is_nonpassing_and_keeps_evidence(self):
        result = ui.run(self.project, {'url': self.url, 'authorization': '测试失败留证', 'steps': [
            {'action': 'assert_text', 'selector': '#result', 'text': 'absent'},
        ]}, str(BROWSER))
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['steps'][0]['passed'])
        self.assertTrue((self.project.root / result['resultPath']).is_file())

    def test_navigation_off_localhost_stops_contract(self):
        result = ui.run(self.project, {'url': self.url, 'authorization': '测试离站拦截', 'steps': [
            {'action': 'click', 'selector': '#leave'},
            {'action': 'screenshot', 'name': 'must-not-run.png'},
        ]}, str(BROWSER))
        self.assertEqual(result['status'], 'failed')
        self.assertIn('left authorized origin', result['error'])
        self.assertEqual(len(result['steps']), 1)

    def test_same_origin_path_navigation_is_allowed(self):
        result = ui.run(self.project, {'url': self.url, 'authorization': '测试同源路径', 'steps': [
            {'action': 'click', 'selector': '#same'},
            {'action': 'assert_text', 'selector': '#second', 'text': 'same origin path'},
        ]}, str(BROWSER))
        self.assertEqual(result['status'], 'passed', result)

    def test_click_scrolls_long_page_then_keeps_real_cover_check(self):
        result = ui.run(self.project, {'url': self.url + 'long', 'authorization': '测试长页面真实按钮', 'steps': [
            {'action': 'click', 'selector': '#bottom'},
            {'action': 'assert_text', 'selector': '#longresult', 'text': 'changed'},
        ]}, str(BROWSER))
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['steps'][0]['detail']['input'], 'trusted CDP pointer')

    def test_cross_origin_fetch_and_delayed_exception_fail_without_sink_hit(self):
        self.sink.hits = 0
        result = ui.run(self.project, {'url': self.url, 'authorization': '反证外发与异常', 'steps': [
            {'action': 'click', 'selector': '#attack'},
            {'action': 'assert_visible', 'selector': 'body'},
        ]}, str(BROWSER))
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(self.sink.hits, 0, result)
        self.assertTrue(result['networkViolations'])
        self.assertTrue(result['exceptions'])

    def test_cross_origin_websocket_is_blocked_before_handshake(self):
        self.sink.hits = 0
        result = ui.run(self.project, {'url': self.url, 'authorization': '反证 WebSocket 旁路', 'steps': [
            {'action': 'click', 'selector': '#wsattack'},
            {'action': 'assert_visible', 'selector': 'body'},
        ]}, str(BROWSER))
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(self.sink.hits, 0, result)
        self.assertTrue(any(item.get('url', '').startswith('ws://0.0.0.0:')
                            for item in result['networkViolations']), result)

    def test_screenshot_only_is_captured_not_passed(self):
        result = ui.run(self.project, {'url': self.url, 'authorization': '只采集截图', 'steps': [
            {'action': 'screenshot', 'name': 'capture.png'},
        ]}, str(BROWSER))
        self.assertEqual(result['status'], 'captured', result)

    def test_explicit_local_api_origin_is_allowed(self):
        self.sink.hits = 0
        api_origin = f'http://127.0.0.1:{self.sink.server_port}'
        result = ui.run(self.project, {'url': self.url, 'authorization': '授权本机 API 端口',
            'allowedOrigins': [api_origin], 'steps': [
                {'action': 'click', 'selector': '#allowedfetch'},
                {'action': 'assert_visible', 'selector': 'body'},
            ]}, str(BROWSER))
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(self.sink.hits, 1, result)
        self.assertEqual(result['contract']['allowedOrigins'], [api_origin])


class AdapterBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Project(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_browser_resolves_from_skill_package_not_business_project(self):
        package = self.project.root / 'uninstalled-skill'
        with mock.patch.object(ui, '__file__', str(package / 'scripts/donghe_ui.py')):
            result = ui.run(self.project, {'url': 'http://localhost:4318',
                'authorization': 'package browser path regression',
                'steps': [{'action': 'assert_visible', 'selector': 'body'}]})
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn(str(package / ui.DEFAULT_BINARY), result['error'])

    def test_missing_binary_is_unavailable_and_persists_result(self):
        result = ui.run(self.project, {'url': 'http://localhost:4318', 'authorization': '测试缺浏览器',
            'steps': [{'action': 'assert_visible', 'selector': 'body'}]}, '/missing/chrome')
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('unavailable', result['error'])
        self.assertTrue((self.project.root / result['resultPath']).is_file())

    def test_invalid_url_and_contract_leave_failed_evidence(self):
        for request in ({'url': 'https://example.com', 'authorization': '测试坏地址', 'steps': [{'action': 'screenshot'}]},
                        {'url': 'http://localhost:1', 'authorization': '测试空合同', 'steps': []},
                        {'url': 'http://localhost:1', 'authorization': '测试远程白名单',
                         'allowedOrigins': ['https://example.com'], 'steps': [{'action': 'screenshot'}]},
                        {'url': 'http://localhost:1', 'authorization': '测试路径白名单',
                         'allowedOrigins': ['http://localhost:2/api'], 'steps': [{'action': 'screenshot'}]}):
            result = ui.run(self.project, request, '/missing/chrome')
            self.assertEqual(result['status'], 'failed')
            self.assertTrue((self.project.root / result['resultPath']).is_file())

    def test_cli_returns_nonzero_for_unavailable_browser(self):
        request = self.project.root / 'contract.json'
        request.write_text(json.dumps({'url': 'http://localhost:4318', 'authorization': '测试 CLI 非零',
            'steps': [{'action': 'assert_visible', 'selector': 'body'}]}))
        completed = subprocess.run(['python3', str(ROOT / 'scripts/donghe_ui.py'),
            '--project', str(self.project.root), '--spec', str(request), '--binary', '/missing/chrome'],
            text=True, capture_output=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)['status'], 'unavailable')

    def test_evidence_directory_symlink_cannot_escape_project(self):
        outside = Path(self.temp.name).parent / ('ui-outside-' + Path(self.temp.name).name)
        outside.mkdir()
        try:
            (self.project.root / 'docs').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'escapes project'):
                ui.run(self.project, {'url': 'http://localhost:4318', 'authorization': '测试产物边界',
                    'steps': [{'action': 'assert_visible', 'selector': 'body'}]}, '/missing/chrome')
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            outside.rmdir()


if __name__ == '__main__':
    unittest.main()
