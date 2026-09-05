"""Small stdlib-only localhost browser acceptance adapter using Chrome CDP."""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid


DEFAULT_BINARY = 'runtime/browser/chrome-headless-shell-mac-arm64/chrome-headless-shell'
MAX_STEPS = 50


def _authorized(url):
    parsed = urlparse(url)
    return (parsed.scheme in {'http', 'https'} and parsed.hostname in {'localhost', '127.0.0.1'}
            and parsed.username is None and parsed.password is None)


def _origin(url):
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    return parsed.scheme, parsed.hostname, port


def _network_allowed(url, allowed_origins):
    parsed = urlparse(url)
    if parsed.scheme in {'data', 'blob', 'about'}:
        return True
    scheme = {'ws': 'http', 'wss': 'https'}.get(parsed.scheme, parsed.scheme)
    port = parsed.port or (443 if scheme == 'https' else 80)
    return (scheme, parsed.hostname, port) in allowed_origins


def _blocked_url_patterns(origins):
    patterns = []
    for scheme, host, port in sorted(origins):
        authority = host + ((':' + str(port)) if port not in {80, 443} else '')
        websocket_scheme = 'wss' if scheme == 'https' else 'ws'
        patterns.extend(({'urlPattern': f'{scheme}://{authority}/*', 'block': False},
                         {'urlPattern': f'{websocket_scheme}://{authority}/*', 'block': False}))
    return patterns + [
        {'urlPattern': 'http://*/*', 'block': True},
        {'urlPattern': 'http://*:*/*', 'block': True},
        {'urlPattern': 'https://*/*', 'block': True},
        {'urlPattern': 'https://*:*/*', 'block': True},
        {'urlPattern': 'ws://*/*', 'block': True},
        {'urlPattern': 'ws://*:*/*', 'block': True},
        {'urlPattern': 'wss://*/*', 'block': True},
        {'urlPattern': 'wss://*:*/*', 'block': True},
    ]


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _http_json(url, method='GET', timeout=2):
    with urlopen(Request(url, method=method), timeout=timeout) as response:
        return json.loads(response.read())


class _WebSocket:
    def __init__(self, url, timeout):
        parsed = urlparse(url)
        if parsed.scheme != 'ws' or parsed.hostname not in {'localhost', '127.0.0.1'}:
            raise ValueError('unsafe CDP websocket URL')
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path + (('?' + parsed.query) if parsed.query else '')
        request = (f'GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n'
                   f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                   'Sec-WebSocket-Version: 13\r\n\r\n').encode()
        self.sock.sendall(request)
        response = self._headers()
        expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if b' 101 ' not in response.split(b'\r\n', 1)[0] or ('sec-websocket-accept: ' + expected).encode().lower() not in response.lower():
            self.close()
            raise RuntimeError('CDP websocket handshake failed')

    def _headers(self):
        data = bytearray()
        while b'\r\n\r\n' not in data:
            chunk = self.sock.recv(4096)
            if not chunk or len(data) + len(chunk) > 65536:
                raise RuntimeError('invalid websocket handshake')
            data.extend(chunk)
        return bytes(data)

    def _exact(self, count):
        data = bytearray()
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise RuntimeError('CDP websocket closed')
            data.extend(chunk)
        return bytes(data)

    def send(self, value, opcode=1):
        payload = value.encode() if isinstance(value, str) else value
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | opcode, 0x80 | (length if length < 126 else 126 if length < 65536 else 127)])
        if length >= 65536:
            header.extend(struct.pack('!Q', length))
        elif length >= 126:
            header.extend(struct.pack('!H', length))
        header.extend(mask)
        header.extend(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(header)

    def receive(self):
        fragments = bytearray()
        while True:
            first, second = self._exact(2)
            final, opcode, masked = bool(first & 0x80), first & 0x0f, bool(second & 0x80)
            length = second & 0x7f
            if length == 126:
                length = struct.unpack('!H', self._exact(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self._exact(8))[0]
            if length > 16 * 1024 * 1024:
                raise RuntimeError('CDP frame exceeds budget')
            mask = self._exact(4) if masked else None
            payload = self._exact(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 8:
                raise RuntimeError('CDP websocket closed')
            if opcode == 9:
                self.send(payload, opcode=10)
                continue
            if opcode in (1, 2):
                fragments = bytearray(payload)
            elif opcode == 0:
                fragments.extend(payload)
            else:
                continue
            if final:
                return json.loads(fragments.decode())

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class _CDP:
    def __init__(self, websocket, deadline, events, request_policy=None):
        self.ws = websocket
        self.deadline = deadline
        self.events = events
        self.next_id = 0
        self.request_policy = request_policy
        self.automatic_ids = set()

    def _send_automatic(self, method, params):
        self.next_id += 1
        self.automatic_ids.add(self.next_id)
        self.ws.send(json.dumps({'id': self.next_id, 'method': method, 'params': params}))

    def _fulfill_with_headers(self, paused, headers):
        self.next_id += 1
        body_id = self.next_id
        self.ws.send(json.dumps({'id': body_id, 'method': 'Fetch.getResponseBody',
                                 'params': {'requestId': paused['requestId']}}))
        while time.monotonic() < self.deadline:
            message = self.ws.receive()
            if message.get('id') == body_id:
                if 'error' in message:
                    raise RuntimeError('Fetch.getResponseBody: ' + message['error'].get('message', 'CDP error'))
                body = message.get('result', {}).get('body', '')
                if not message.get('result', {}).get('base64Encoded'):
                    body = base64.b64encode(body.encode()).decode()
                self._send_automatic('Fetch.fulfillRequest', {'requestId': paused['requestId'],
                    'responseCode': paused['responseStatusCode'],
                    'responsePhrase': paused.get('responseStatusText', ''),
                    'responseHeaders': headers, 'body': body})
                return
            self.events.append(message)
        raise TimeoutError('response policy timed out')

    def call(self, method, params=None):
        self.next_id += 1
        call_id = self.next_id
        self.ws.send(json.dumps({'id': call_id, 'method': method, 'params': params or {}}))
        while time.monotonic() < self.deadline:
            message = self.ws.receive()
            if message.get('id') in self.automatic_ids:
                self.automatic_ids.remove(message['id'])
                if 'error' in message:
                    raise RuntimeError('automatic CDP command failed: ' + message['error'].get('message', 'CDP error'))
                continue
            if message.get('method') == 'Fetch.requestPaused' and self.request_policy:
                decision = self.request_policy(message.get('params', {}))
                if decision[0] == 'donghe.fulfillWithHeaders':
                    self._fulfill_with_headers(message.get('params', {}), decision[1]['headers'])
                else:
                    self._send_automatic(*decision)
                self.events.append(message)
                continue
            if message.get('id') == call_id:
                if 'error' in message:
                    raise RuntimeError(method + ': ' + message['error'].get('message', 'CDP error'))
                return message.get('result', {})
            self.events.append(message)
        raise TimeoutError('browser contract timed out')


def _evaluate(cdp, expression):
    result = cdp.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True,
                                            'awaitPromise': True, 'userGesture': True})
    if result.get('exceptionDetails'):
        raise RuntimeError('page expression failed')
    return result.get('result', {}).get('value')


def _check_url(cdp, allowed_origin):
    current = _evaluate(cdp, 'location.href')
    if not _authorized(current) or _origin(current) != allowed_origin:
        raise RuntimeError('page left authorized origin: ' + str(current))
    return current


def _poll(cdp, expression, timeout=5):
    end = min(cdp.deadline, time.monotonic() + timeout)
    last = None
    while time.monotonic() < end:
        last = _evaluate(cdp, expression)
        if isinstance(last, dict) and last.get('ok'):
            return last
        time.sleep(0.05)
    return last or {'ok': False, 'reason': 'condition timed out'}


def _safe_child(run_dir, name):
    if Path(name).name != name:
        raise ValueError('unsafe evidence filename')
    target = run_dir / name
    if run_dir.is_symlink() or target.is_symlink():
        raise ValueError('evidence path contains a symlink')
    try:
        target.resolve(strict=False).relative_to(run_dir.resolve())
    except ValueError as exc:
        raise ValueError('evidence path escapes run directory') from exc
    return target


def _step(cdp, step, run_dir, index):
    if not isinstance(step, dict) or set(step) - {'action', 'selector', 'value', 'text', 'name', 'secret'}:
        raise ValueError('invalid UI step')
    action = step.get('action')
    selector = step.get('selector')
    if action in {'click', 'fill', 'assert_text', 'assert_visible'} and not isinstance(selector, str):
        raise ValueError('step selector is required')
    if action == 'fill' and not isinstance(step.get('value'), str):
        raise ValueError('fill value must be text')
    if action == 'assert_text' and not isinstance(step.get('text'), str):
        raise ValueError('assert text must be text')
    quoted = json.dumps(selector)
    if action == 'click':
        found = _evaluate(cdp, f'(()=>{{const e=document.querySelector({quoted});if(!e)return {{ok:false,reason:"missing selector"}};e.scrollIntoView({{block:"center",inline:"center"}});return {{ok:true}}}})()')
        if found.get('ok'):
            _evaluate(cdp, 'new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))')
        value = found if not found.get('ok') else _evaluate(cdp, f'(()=>{{const e=document.querySelector({quoted}),r=e.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2,t=document.elementFromPoint(x,y);return r.width>0&&r.height>0&&(t===e||e.contains(t))?{{ok:true,x,y,input:"trusted CDP pointer"}}:{{ok:false,reason:"element is hidden or covered"}}}})()')
        if value.get('ok'):
            for event_type in ('mousePressed', 'mouseReleased'):
                cdp.call('Input.dispatchMouseEvent', {'type': event_type, 'x': value['x'], 'y': value['y'],
                                                       'button': 'left', 'clickCount': 1})
    elif action == 'fill':
        value = _evaluate(cdp, f'(()=>{{const e=document.querySelector({quoted});if(!e)return {{ok:false,reason:"missing selector"}};e.focus();e.value="";return document.activeElement===e?{{ok:true,input:"trusted CDP text"}}:{{ok:false,reason:"element cannot receive focus"}}}})()')
        if value.get('ok'):
            cdp.call('Input.insertText', {'text': step['value']})
    elif action == 'assert_text':
        expected = json.dumps(step.get('text'))
        value = _poll(cdp, f'(()=>{{const e=document.querySelector({quoted});return e&&e.textContent.includes({expected})?{{ok:true,text:e.textContent}}:{{ok:false,reason:e?"text mismatch":"missing selector"}}}})()')
    elif action == 'assert_visible':
        value = _poll(cdp, f'(()=>{{const e=document.querySelector({quoted});if(!e)return {{ok:false,reason:"missing selector"}};const s=getComputedStyle(e),r=e.getBoundingClientRect();return s.display!=="none"&&s.visibility!=="hidden"&&Number(s.opacity)!==0&&r.width>0&&r.height>0?{{ok:true}}:{{ok:false,reason:"not visible"}}}})()')
    elif action == 'screenshot':
        name = step.get('name') or f'step-{index}.png'
        if not name.endswith('.png'):
            raise ValueError('unsafe screenshot name')
        data = cdp.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True})['data']
        image = base64.b64decode(data, validate=True)
        _safe_child(run_dir, name).write_bytes(image)
        value = {'ok': True, 'path': name, 'sha256': hashlib.sha256(image).hexdigest()}
    else:
        raise ValueError('unsupported UI action')
    if not isinstance(value, dict) or not value.get('ok'):
        raise AssertionError((value or {}).get('reason', 'UI step failed'))
    return value


def _canonical(spec):
    allowed = {'url', 'authorization', 'steps', 'timeout', 'allowExceptions', 'allowedOrigins'}
    if set(spec) - allowed:
        raise ValueError('invalid UI contract field')
    authorization = spec.get('authorization')
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError('UI contract authorization is required')
    clean_steps = []
    for item in spec.get('steps', []):
        if not isinstance(item, dict):
            raise ValueError('invalid UI step')
        clean = dict(item)
        if clean.get('action') == 'fill' and clean.get('secret') is True:
            value = clean.pop('value', None)
            if not isinstance(value, str):
                raise ValueError('secret fill value must be text')
            clean['valueSha256'] = hashlib.sha256(value.encode()).hexdigest()
        clean_steps.append(clean)
    extra_origins = spec.get('allowedOrigins', [])
    if not isinstance(extra_origins, list) or len(extra_origins) > 10:
        raise ValueError('allowedOrigins must contain at most 10 origins')
    normalized = []
    for value in extra_origins:
        if not isinstance(value, str) or not _authorized(value):
            raise ValueError('allowedOrigins must be localhost origins')
        parsed = urlparse(value)
        if parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
            raise ValueError('allowedOrigins entries cannot contain paths, queries, or fragments')
        scheme, host, port = _origin(value)
        authority = host + ((':' + str(port)) if port not in {80, 443} else '')
        origin = f'{scheme}://{authority}'
        if origin not in normalized:
            normalized.append(origin)
    return {'url': spec['url'], 'authorization': authorization.strip(), 'allowedOrigins': normalized,
            'timeout': spec.get('timeout', 45), 'allowExceptions': spec.get('allowExceptions', False),
            'steps': clean_steps}


def _step_evidence(step):
    value = {'action': step.get('action')}
    for key in ('selector', 'text', 'name'):
        if key in step:
            value[key] = step[key]
    if step.get('action') == 'fill':
        if step.get('secret') is True:
            value['secret'] = True
            value['valueSha256'] = hashlib.sha256(step['value'].encode()).hexdigest()
        else:
            value['value'] = step.get('value')
    return value


def _facts(events):
    console, exceptions = [], []
    for event in events:
        method, params = event.get('method'), event.get('params', {})
        if method == 'Runtime.consoleAPICalled':
            console.append({'type': params.get('type'), 'values': [arg.get('value') for arg in params.get('args', [])]})
        elif method == 'Runtime.exceptionThrown':
            detail = params.get('exceptionDetails', {})
            exceptions.append({'text': detail.get('text'), 'url': detail.get('url'),
                               'lineNumber': detail.get('lineNumber'),
                               'description': detail.get('exception', {}).get('description')})
        elif method == 'Log.entryAdded':
            console.append(params.get('entry', {}))
    return console, exceptions


def _network_facts(events, allowed_origins):
    violations = []
    for event in events:
        method, params = event.get('method'), event.get('params', {})
        if method == 'Network.webSocketCreated':
            url = params.get('url', '')
        elif method == 'Network.requestWillBeSent':
            url = params.get('request', {}).get('url', '')
        elif method == 'Network.webTransportCreated':
            url = params.get('url', '')
        elif method == 'Log.entryAdded' and params.get('entry', {}).get('source') == 'security':
            text = params.get('entry', {}).get('text', '')
            if 'Content Security Policy' not in text or "Connecting to '" not in text:
                continue
            url = text.split("Connecting to '", 1)[1].split("'", 1)[0]
        else:
            continue
        if url and not _network_allowed(url, allowed_origins):
            item = {'url': url, 'event': method}
            if item not in violations:
                violations.append(item)
    return violations


def _run_directory(project, relative):
    root = Path(project.root).resolve()
    candidate = Path(project.path(relative))
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ValueError('evidence path escapes project') from exc
    cursor = root
    for part in Path(relative).parts:
        cursor = cursor / part
        if cursor.exists() and cursor.is_symlink():
            raise ValueError('evidence path contains a symlink')
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def run(project, spec, binary=None):
    run_id = uuid.uuid4().hex
    run_rel = f'docs/东合/证据/UI/{run_id}'
    run_dir = _run_directory(project, run_rel)
    root = Path(project.root).resolve()
    browser_path = Path(binary) if binary else Path(__file__).resolve().parents[1] / DEFAULT_BINARY
    result = {'runId': run_id, 'status': 'failed', 'startedAt': _now(),
              'url': spec.get('url') if isinstance(spec, dict) else None, 'steps': [],
              'console': [], 'exceptions': [], 'networkViolations': [], 'screenshots': [], 'error': None,
              'networkBoundary': 'CDP interception plus additive connect-src CSP; not an operating-system network sandbox',
              'browserPolicy': {'isolatedProfile': True, 'additiveCSP': True,
                                'disabledFeatures': ['LocalNetworkAccessChecks', 'PrivateNetworkAccessChecks',
                                                     'PrivateNetworkAccessRespectPreflightResults'],
                                'scope': 'controlled localhost acceptance; not production browser network-policy certification'}}
    process = None
    profile = None
    websocket = None
    events = []
    deadline = time.monotonic() + 45
    try:
        if not isinstance(spec, dict) or not _authorized(spec.get('url', '')):
            raise ValueError('UI contract URL must be localhost')
        canonical = _canonical(spec)
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        result['contract'] = canonical
        result['contractSha256'] = hashlib.sha256(encoded).hexdigest()
        steps = spec.get('steps')
        if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
            raise ValueError('UI contract requires 1-50 steps')
        timeout = spec.get('timeout', 45)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 1 <= timeout <= 60:
            raise ValueError('timeout must be 1-60 seconds')
        deadline = time.monotonic() + timeout
        if not isinstance(spec.get('allowExceptions', False), bool):
            raise ValueError('allowExceptions must be boolean')
        allowed_origin = _origin(spec['url'])
        network_origins = {allowed_origin}
        network_origins.update(_origin(value) for value in canonical['allowedOrigins'])
        if not browser_path.is_file() or not os.access(browser_path, os.X_OK):
            result['status'] = 'unavailable'
            raise FileNotFoundError('Chrome binary unavailable: ' + str(browser_path))
        profile = tempfile.mkdtemp(prefix='donghe-ui-')
        process = subprocess.Popen([str(browser_path), '--headless=new', '--remote-debugging-port=0',
            '--remote-allow-origins=*', '--no-first-run', '--no-default-browser-check',
            '--disable-background-networking', '--disable-component-update', '--disable-sync',
            '--disable-default-apps', '--disable-extensions',
            '--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessChecks,PrivateNetworkAccessRespectPreflightResults',
            '--user-data-dir=' + profile, 'about:blank'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = Path(profile) / 'DevToolsActivePort'
        while not port_file.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Chrome exited before CDP became available')
            time.sleep(0.05)
        if not port_file.exists():
            raise TimeoutError('Chrome debug port timed out')
        port = int(port_file.read_text().splitlines()[0])
        targets = _http_json(f'http://127.0.0.1:{port}/json/list')
        target = next((item for item in targets if item.get('type') == 'page'), None)
        if not target:
            raise RuntimeError('Chrome page target unavailable')
        websocket = _WebSocket(target['webSocketDebuggerUrl'], max(0.1, deadline - time.monotonic()))
        def request_policy(params):
            requested = params.get('request', {}).get('url', '')
            if 'responseStatusCode' in params:
                headers = [header for header in params.get('responseHeaders', [])
                           if header.get('name', '').lower() not in {'content-length', 'content-encoding', 'transfer-encoding'}]
                sources = []
                for scheme, hostname, origin_port in sorted(network_origins):
                    host = hostname + ((':' + str(origin_port)) if origin_port not in {80, 443} else '')
                    sources.extend((f'{scheme}://{host}', f'{"wss" if scheme == "https" else "ws"}://{host}'))
                headers.append({'name': 'Content-Security-Policy',
                                'value': "connect-src 'self' " + ' '.join(sources) + ' blob:'})
                return 'donghe.fulfillWithHeaders', {'headers': headers}
            parsed = urlparse(requested)
            permitted = parsed.scheme in {'data', 'blob'} or (_authorized(requested) and _origin(requested) in network_origins)
            if permitted:
                return 'Fetch.continueRequest', {'requestId': params['requestId']}
            result['networkViolations'].append({'url': requested, 'resourceType': params.get('resourceType')})
            return 'Fetch.failRequest', {'requestId': params['requestId'], 'errorReason': 'BlockedByClient'}
        cdp = _CDP(websocket, deadline, events, request_policy)
        for method in ('Page.enable', 'Runtime.enable', 'Log.enable', 'Network.enable'):
            cdp.call(method)
        cdp.call('Network.setBlockedURLs', {'urlPatterns': _blocked_url_patterns(network_origins)})
        cdp.call('Fetch.enable', {'patterns': [
            {'urlPattern': f'{urlparse(spec["url"]).scheme}://*/*', 'resourceType': 'Document', 'requestStage': 'Response'},
        ]})
        cdp.call('Page.navigate', {'url': spec['url']})
        target_url = json.dumps(spec['url'])
        loaded = _poll(cdp, f'location.href===new URL({target_url}).href&&document.readyState==="complete"?{{ok:true}}:{{ok:false}}', timeout=5)
        if not loaded.get('ok'):
            raise TimeoutError('authorized page navigation timed out')
        _poll(cdp, 'document.readyState==="complete"?{ok:true}:{ok:false}', timeout=5)
        _check_url(cdp, allowed_origin)
        for index, item in enumerate(steps, 1):
            started = time.monotonic()
            try:
                detail = _step(cdp, item, run_dir, index)
                if item.get('action') == 'click':
                    time.sleep(0.1)
                current = _check_url(cdp, allowed_origin)
                entry = {'index': index, **_step_evidence(item), 'passed': True,
                         'durationMs': round((time.monotonic() - started) * 1000), 'url': current, 'detail': detail}
                if item.get('action') == 'screenshot':
                    result['screenshots'].append({'path': detail['path'], 'sha256': detail['sha256']})
                result['steps'].append(entry)
            except Exception as exc:
                result['steps'].append({'index': index, **_step_evidence(item), 'passed': False,
                    'durationMs': round((time.monotonic() - started) * 1000), 'error': str(exc)})
                raise
        _evaluate(cdp, 'new Promise(resolve=>setTimeout(()=>resolve(true),250))')
        result['finalUrl'] = _check_url(cdp, allowed_origin)
        result['console'], result['exceptions'] = _facts(events)
        for violation in _network_facts(events, network_origins):
            if violation not in result['networkViolations']:
                result['networkViolations'].append(violation)
        if result['networkViolations']:
            raise RuntimeError('page attempted a request outside the authorized origin')
        if result['exceptions'] and not spec.get('allowExceptions', False):
            raise RuntimeError('page raised an uncaught exception')
        has_assertion = any(item.get('action') in {'assert_text', 'assert_visible'} for item in steps)
        result['status'] = 'passed' if has_assertion else 'captured'
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        result['console'], result['exceptions'] = _facts(events)
        if isinstance(spec, dict) and _authorized(spec.get('url', '')):
            allowed = {_origin(spec['url'])}
            for value in result.get('contract', {}).get('allowedOrigins', []):
                allowed.add(_origin(value))
            for violation in _network_facts(events, allowed):
                if violation not in result['networkViolations']:
                    result['networkViolations'].append(violation)
        if websocket:
            websocket.close()
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if profile:
            shutil.rmtree(profile, ignore_errors=True)
        result['finishedAt'] = _now()
        _safe_child(run_dir, 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    result['resultPath'] = run_rel + '/result.json'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--spec', required=True)
    parser.add_argument('--binary')
    args = parser.parse_args(argv)
    class Project:
        root = Path(args.project)
        def path(self, relative):
            return self.root / relative
    try:
        request = json.loads(Path(args.spec).read_text())
        result = run(Project(), request, args.binary)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] in {'passed', 'captured'} else 1
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
