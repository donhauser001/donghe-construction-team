"""Ephemeral loopback reader, authenticated and limited to snapshot refresh."""
import fcntl
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse

from donghe_workspace import atomic, home, render, snapshot


def serve(item, idle_seconds=900):
    locks = home() / 'sessions'
    locks.mkdir(exist_ok=True)
    with (locks / (item['id'] + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        token = secrets.token_urlsafe(32)
        data = snapshot(item)
        last_request = [time.monotonic()]
        mutation = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Never log session tokens or document contents.

            def valid_host(self):
                return self.headers.get('Host') == '127.0.0.1:%d' % self.server.server_port

            def authorized(self):
                supplied = self.headers.get('X-Donghe-Token', '')
                return self.valid_host() and supplied.isascii() and secrets.compare_digest(supplied, token)

            def send(self, status, value, kind='application/json; charset=utf-8'):
                payload = (json.dumps(value, ensure_ascii=False) if 'json' in kind else value).encode()
                self.send_response(status)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == '/':
                    supplied = parse_qs(parsed.query).get('token', [''])[0]
                    if not self.valid_host() or not supplied.isascii() or not secrets.compare_digest(supplied, token):
                        self.send(403, {'error': '请从项目根目录入口连接阅读助手。'})
                        return
                    last_request[0] = time.monotonic()
                    self.send(200, render(data, {'url': 'http://127.0.0.1:%d' % self.server.server_port,
                                                'token': token}), 'text/html; charset=utf-8')
                elif not self.authorized():
                    self.send(403, {'error': '阅读会话无效，请重新连接。'})
                elif parsed.path == '/api/health':
                    self.send(200, {'id': item['id']})
                elif parsed.path == '/api/snapshot':
                    last_request[0] = time.monotonic()
                    self.send(200, {**data, 'bridge': {'token': token}})
                else:
                    self.send(404, {'error': '不存在此阅读操作。'})

            def do_POST(self):
                nonlocal data
                origin = 'http://127.0.0.1:%d' % self.server.server_port
                if not self.authorized() or self.headers.get('Origin') != origin:
                    self.send(403, {'error': '跨来源或无效阅读请求已拒绝。'})
                    return
                if self.path not in ['/api/refresh', '/api/reindex']:
                    self.send(404, {'error': '只支持资料刷新和索引重建。'})
                    return
                if self.headers.get('Content-Length', '0') != '0' or self.headers.get('Transfer-Encoding'):
                    self.send(400, {'error': '刷新操作不接受脚本或命令参数。'})
                    return
                if not mutation.acquire(blocking=False):
                    self.send(409, {'error': '资料正在刷新，请稍候。'})
                    return
                try:
                    data = snapshot(item)
                    last_request[0] = time.monotonic()
                    self.send(200, {**data, 'bridge': {'token': token}})
                except (OSError, ValueError) as exc:
                    self.send(409, {'error': '刷新未完成，保留上次快照：' + str(exc)})
                finally:
                    mutation.release()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.timeout = 1
        atomic(locks / (item['id'] + '.json'), json.dumps({'port': server.server_port, 'token': token}))
        try:
            while time.monotonic() - last_request[0] < idle_seconds:
                server.handle_request()
        finally:
            server.server_close()
