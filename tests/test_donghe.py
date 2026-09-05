import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'scripts/donghe.py'
spec = importlib.util.spec_from_file_location('donghe', MODULE)
donghe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(donghe)


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = donghe.Project(self.root)
        (self.root / 'input.txt').write_text('original')
        self.project.init()

    def tearDown(self):
        self.temp.cleanup()

    def create(self, ui=False, command=None):
        criteria = [{'id': 'backend', 'label': '后端', 'command': command or [sys.executable, '-c', "print('real execution')"], 'inputs': ['input.txt'], 'reuse': True}]
        if ui:
            criteria.append({**criteria[0], 'id': 'ui', 'label': '界面'})
        return self.project.create({'id': 'T1', 'title': '闭环', 'authorization': '用户授权', 'criteria': criteria, 'issues': []})

    def test_no_goal_stops_without_execution(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('must not execute')):
            self.assertEqual(self.project.status()['decision']['action'], 'stop')

    def test_normal_completion_and_duplicate_finish(self):
        self.create()
        result = self.project.verify('T1', 'backend')
        self.assertEqual(result['state'], 'passed')
        state = self.project.finish('T1')
        self.assertEqual(state['tasks'][0]['status'], 'completed')
        self.assertEqual(state['decision']['action'], 'stop')
        log = self.project.read(state['logPaths'][0])
        self.project.finish('T1')
        self.assertEqual(log, self.project.read(state['logPaths'][0]))
        self.assertIn('real execution', self.project.path(result['receiptPath']).with_name('stdout.txt').read_text())

    def test_partial_completion_recovers(self):
        self.create()
        self.project.verify('T1', 'backend')
        with patch.dict(os.environ, {'DONGHE_TEST_FAIL_AFTER_WRITE': '1'}):
            with self.assertRaises(RuntimeError):
                self.project.finish('T1')
        state = self.project.status()
        self.assertEqual(state['decision']['action'], 'recover')
        self.assertEqual(state['tasks'][0]['status'], 'active')
        self.assertEqual(self.project.finish('T1')['tasks'][0]['status'], 'completed')
        self.assertFalse(self.project.pending())

    def test_recovery_preserves_conflicting_human_edit(self):
        self.create()
        self.project.verify('T1', 'backend')
        with patch.dict(os.environ, {'DONGHE_TEST_FAIL_AFTER_WRITE': '1'}):
            with self.assertRaises(RuntimeError):
                self.project.finish('T1')
        self.project.write(donghe.DOC + '/工作交接.md', 'human edit')
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.project.finish('T1')
        self.assertEqual(self.project.read(donghe.DOC + '/工作交接.md'), 'human edit')

    def test_unchanged_reuse_does_not_execute(self):
        self.create()
        first = self.project.verify('T1', 'backend')
        with patch.object(subprocess, 'run', side_effect=AssertionError('must reuse')):
            second = self.project.verify('T1', 'backend')
        self.assertTrue(second['reused'])
        self.assertEqual(first['receiptPath'], second['receiptPath'])

    def test_stale_source_blocks_finish(self):
        self.create()
        self.project.verify('T1', 'backend')
        (self.root / 'input.txt').write_text('changed')
        self.assertEqual(self.project.status()['tasks'][0]['criteria'][0]['state'], 'stale')
        with self.assertRaises(ValueError):
            self.project.finish('T1')

    def test_backend_pass_ui_missing_blocks(self):
        self.create(ui=True)
        self.project.verify('T1', 'backend')
        with self.assertRaises(ValueError):
            self.project.finish('T1')
        self.assertEqual(self.project.status()['tasks'][0]['criteria'][1]['state'], 'missing')

    def test_tamper_detected_and_preserved(self):
        self.create()
        result = self.project.verify('T1', 'backend')
        stdout = self.project.path(result['receiptPath']).with_name('stdout.txt')
        stdout.chmod(0o644)
        stdout.write_text('tampered')
        self.assertEqual(self.project.status()['tasks'][0]['criteria'][0]['state'], 'invalid')
        with self.assertRaises(ValueError):
            self.project.finish('T1')
        next_result = self.project.verify('T1', 'backend')
        self.assertNotEqual(next_result['receiptPath'], result['receiptPath'])
        self.assertEqual(stdout.read_text(), 'tampered')

    def test_failures_and_input_mutation_preserved(self):
        self.create(command=[sys.executable, '-c', "import pathlib; pathlib.Path('input.txt').write_text('modified'); print('failed'); raise SystemExit(4)"])
        result = self.project.verify('T1', 'backend')
        self.assertEqual(result['state'], 'invalid')
        receipt = self.project.receipt(result['receiptPath'])
        self.assertEqual(receipt['exitCode'], 4)
        self.assertNotEqual(receipt['before'], receipt['after'])

    def test_paths_and_empty_criteria_rejected(self):
        for path in ['../outside', '/tmp/outside', 'docs/../outside']:
            with self.assertRaises(ValueError):
                self.project.path(path)
        (self.root / 'linked').symlink_to('/tmp')
        with self.assertRaises(ValueError):
            self.project.path('linked/file')
        with self.assertRaises(ValueError):
            self.project.create({'id': 'T1', 'title': 'empty', 'authorization': True, 'criteria': []})
        with self.assertRaises(ValueError):
            self.project.task_path('../task')

    def test_concurrent_finish_is_idempotent(self):
        self.create()
        self.project.verify('T1', 'backend')
        command = [sys.executable, str(MODULE), '--project', str(self.root), 'finish', 'T1']
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: subprocess.run(command, capture_output=True, text=True), range(2)))
        self.assertEqual([r.returncode for r in results], [0, 0], [r.stderr for r in results])
        logs = self.project.status()['logPaths']
        self.assertEqual(self.project.read(logs[0]).count('## T1'), 1)

    def test_journal_cross_project_and_path_tampering_blocked(self):
        self.create()
        self.project.verify('T1', 'backend')
        with patch.dict(os.environ, {'DONGHE_TEST_FAIL_AFTER_WRITE': '1'}):
            with self.assertRaises(RuntimeError):
                self.project.finish('T1')
        rel, op = self.project.pending()[0]
        op['projectRoot'] = '/tmp/other'
        self.project.write(rel, donghe.encode(op))
        with self.assertRaisesRegex(ValueError, 'different project'):
            self.project.finish('T1')
        op['projectRoot'] = str(self.root)
        op['snapshots'][1]['path'] = '../outside'
        self.project.write(rel, donghe.encode(op))
        with self.assertRaisesRegex(ValueError, 'unsafe journal'):
            self.project.finish('T1')

    def test_completed_task_can_reverify_after_source_drift(self):
        self.create()
        self.project.verify('T1', 'backend')
        self.project.finish('T1')
        (self.root / 'input.txt').write_text('revision')
        self.assertEqual(self.project.status()['tasks'][0]['status'], 'active')
        self.project.verify('T1', 'backend')
        self.assertEqual(self.project.finish('T1')['tasks'][0]['status'], 'completed')
        log = self.project.status()['logPaths'][0]
        self.assertEqual(self.project.read(log).count('## T1'), 2)

    def test_abort_after_evidence_drift_then_reverify(self):
        self.create()
        result = self.project.verify('T1', 'backend')
        with patch.dict(os.environ, {'DONGHE_TEST_FAIL_AFTER_WRITE': '2'}):
            with self.assertRaises(RuntimeError):
                self.project.finish('T1')
        (self.root / 'input.txt').write_text('revision')
        with self.assertRaises(ValueError):
            self.project.finish('T1')
        self.assertEqual(self.project.abort('T1')['decision']['action'], 'work')
        self.assertFalse(self.project.pending())
        self.assertTrue(self.project.path(result['receiptPath']).exists())
        self.project.verify('T1', 'backend')
        self.assertEqual(self.project.finish('T1')['tasks'][0]['status'], 'completed')

    def test_issues_stop_next(self):
        task = self.create()
        task['issues'] = ['等待设计判断']
        self.project.write(self.project.task_path('T1'), self.project.render(task))
        self.assertEqual(self.project.status()['decision']['action'], 'stop')

    def test_command_can_query_status_without_lock_deadlock(self):
        self.create(command=[sys.executable, str(MODULE), '--project', str(self.root), 'status'])
        self.assertEqual(self.project.verify('T1', 'backend')['state'], 'passed')

    def test_task_edit_during_command_is_not_overwritten(self):
        command = [sys.executable, '-c', "from pathlib import Path; p=Path('docs/东合/任务卡/T1.md'); p.write_text(p.read_text() + '# human note\\n')"]
        self.create(command=command)
        with self.assertRaisesRegex(ValueError, 'task changed'):
            self.project.verify('T1', 'backend')
        self.assertIn('# human note', self.project.read(self.project.task_path('T1')))
        self.assertEqual(len(self.project.candidates('T1', 'backend')), 1)

    def test_status_flip_and_missing_log_do_not_prove_completion(self):
        self.create()
        self.project.verify('T1', 'backend')
        task = self.project.task('T1')
        task['status'] = 'completed'
        self.project.write(self.project.task_path('T1'), self.project.render(task))
        self.assertEqual(self.project.status()['tasks'][0]['verification'], 'invalid')
        self.assertEqual(self.project.status()['tasks'][0]['status'], 'active')
        state = self.project.finish('T1')
        self.assertEqual(state['tasks'][0]['status'], 'completed')
        self.project.path(state['logPaths'][0]).unlink()
        self.assertEqual(self.project.status()['tasks'][0]['status'], 'active')

    def test_failed_and_timeout_receipts_are_preserved(self):
        self.create(command=[sys.executable, '-c', "import sys; print('error', file=sys.stderr); sys.exit(3)"])
        failed = self.project.verify('T1', 'backend')
        self.assertEqual(failed['state'], 'failed')
        task = self.project.task('T1')
        task['criteria'][0]['command'] = [sys.executable, '-c', 'import time; time.sleep(1)']
        task['criteria'][0]['timeout'] = .02
        self.project.write(self.project.task_path('T1'), self.project.render(task))
        timed_out = self.project.verify('T1', 'backend')
        receipt = self.project.receipt(timed_out['receiptPath'])
        self.assertEqual(receipt['error'], 'verification timed out')
        self.assertIsNotNone(receipt['after'])
        self.assertTrue(self.project.path(failed['receiptPath']).exists())

    def test_directory_membership_and_symlink_inputs(self):
        folder = self.root / 'inputs'
        folder.mkdir()
        (folder / 'one').write_text('one')
        task = self.create()
        task['criteria'][0]['inputs'] = ['inputs']
        self.project.write(self.project.task_path('T1'), self.project.render(task))
        self.project.verify('T1', 'backend')
        (folder / 'two').write_text('two')
        self.assertEqual(self.project.status()['tasks'][0]['criteria'][0]['state'], 'stale')
        (folder / 'symlink').symlink_to('/tmp')
        self.assertEqual(self.project.verify('T1', 'backend')['state'], 'invalid')

    def test_abort_conflict_preserves_user_edit(self):
        self.create()
        self.project.verify('T1', 'backend')
        with patch.dict(os.environ, {'DONGHE_TEST_FAIL_AFTER_WRITE': '1'}):
            with self.assertRaises(RuntimeError):
                self.project.finish('T1')
        self.project.write(donghe.DOC + '/工作交接.md', 'human edit')
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.project.abort('T1')
        self.assertEqual(self.project.read(donghe.DOC + '/工作交接.md'), 'human edit')
        self.assertTrue(self.project.pending())

    def test_http_state_source_and_confinement(self):
        self.create()
        process = subprocess.Popen([sys.executable, str(MODULE), '--project', str(self.root), 'serve', '--port', '0'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = process.stdout.readline()
            # Server startup is a pretty JSON object; read its bounded remaining lines.
            data = line + process.stdout.readline() + process.stdout.readline()
            base = json.loads(data)['url']
            with urllib.request.urlopen(base + '/api/state', timeout=3) as response:
                self.assertEqual(json.load(response)['tasks'][0]['id'], 'T1')
            path = urllib.parse.quote(self.project.task_path('T1'))
            with urllib.request.urlopen(base + '/api/source?path=' + path, timeout=3) as response:
                self.assertIn('donghe-json', json.load(response)['content'])
            for path in ['../input.txt', 'docs/东合/../../input.txt']:
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(base + '/api/source?path=' + urllib.parse.quote(path), timeout=3)
            (self.root / 'docs/东合/leak.txt').symlink_to(self.root / 'input.txt')
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + '/api/source?path=' + urllib.parse.quote('docs/东合/leak.txt'), timeout=3)
        finally:
            process.terminate()
            process.communicate(timeout=3)


if __name__ == '__main__':
    unittest.main()
