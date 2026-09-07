"""Reader bridge regression tests, confined to temporary projects and loopback."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import donghe_workspace as workspace
import donghe_workspace_server as server


class WorkspaceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        self.state = self.base / 'reader'
        self.state.mkdir()
        self.patches = [patch.object(workspace, 'home', return_value=self.state),
                        patch.object(server, 'home', return_value=self.state)]
        # Exercise snapshot/HTTP logic independently of UI assets.
        renderer = lambda data, bridge=None: workspace.MARKER + '\n' + json.dumps(data)
        self.patches += [patch.object(workspace, 'render', side_effect=renderer),
                         patch.object(server, 'render', side_effect=renderer)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_attach_preserves_foreign_entry_and_does_not_initialize(self):
        target = self.root / workspace.ENTRY
        target.write_text('user owned file')
        with self.assertRaises(ValueError):
            workspace.attach(self.root)
        self.assertEqual(target.read_text(), 'user owned file')
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_attach_only_derives_entry_and_registration(self):
        (self.root / 'README.md').write_text('# Existing project')
        result = workspace.attach(self.root)
        self.assertEqual({p.name for p in self.root.iterdir()}, {'README.md', workspace.ENTRY})
        self.assertFalse((self.root / 'docs').exists())
        self.assertEqual(workspace.registration(result['projectId'])['root'], str(self.root))

    def test_url_only_accepts_registered_identity(self):
        result = workspace.attach(self.root)
        identifier = result['projectId']
        self.assertEqual(workspace.resolve_url('donghe://workspace?id=' + identifier)['root'], str(self.root))
        for url in ['https://workspace?id=' + identifier,
                    'donghe://workspace/execute?id=' + identifier,
                    'donghe://workspace?id=' + identifier + '&command=ls',
                    'donghe://workspace?id=' + identifier + '&id=' + identifier,
                    'donghe://workspace?id=../../project',
                    'donghe://workspace?id=' + identifier + '#fragment']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                workspace.resolve_url(url)

    def test_http_rejects_unauthorized_and_refreshes_root_snapshot(self):
        source = self.root / 'README.md'
        source.write_text('# Original')
        attached = workspace.attach(self.root)
        item = workspace.registration(attached['projectId'])
        runner = threading.Thread(target=server.serve, args=(item,), kwargs={'idle_seconds': 1.5})
        runner.start()
        try:
            session_path = self.state / 'sessions' / (item['id'] + '.json')
            deadline = time.monotonic() + 5
            while not session_path.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            session = json.loads(session_path.read_text())
            port, token = session['port'], session['token']
            origin = 'http://127.0.0.1:' + str(port)

            def request(method, path, headers=None):
                client = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                try:
                    client.request(method, path, headers=headers or {})
                    response = client.getresponse()
                    return response.status, response.read()
                finally:
                    client.close()

            self.assertEqual(request('GET', '/api/snapshot')[0], 403)
            self.assertEqual(request('GET', '/api/health', {'X-Donghe-Token': token, 'Host': 'evil.test'})[0], 403)
            self.assertEqual(request('POST', '/api/refresh', {'X-Donghe-Token': token, 'Origin': 'https://evil.test'})[0], 403)
            self.assertEqual(request('POST', '/api/refresh', {'X-Donghe-Token': token})[0], 403)
            self.assertEqual(request('GET', '/?token=wrong')[0], 403)
            source.write_text('# Updated while waiting for a decision')
            status, payload = request('POST', '/api/refresh', {'X-Donghe-Token': token, 'Origin': origin})
            self.assertEqual(status, 200)
            self.assertIn('Updated while waiting', payload.decode())
            root_snapshot = (self.root / workspace.ENTRY).read_text()
            self.assertIn('Updated while waiting', root_snapshot)
            self.assertNotIn(token, root_snapshot)
            self.assertEqual(source.read_text(), '# Updated while waiting for a decision')
        finally:
            runner.join(timeout=5)
            self.assertFalse(runner.is_alive(), 'reader must exit after idle timeout')


if __name__ == '__main__':
    unittest.main()
