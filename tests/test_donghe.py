import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
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
        drifted = self.project.status()
        self.assertEqual(drifted['tasks'][0]['status'], 'active')
        self.assertNotEqual(drifted['tasks'][0]['verification'], 'passed')
        self.assertEqual(drifted['decision']['action'], 'stop')
        self.assertIn('历史完工', drifted['decision']['reason'])
        self.project.verify('T1', 'backend')
        reopened = self.project.status()
        self.assertEqual(reopened['tasks'][0]['declaredStatus'], 'active')
        self.assertEqual(reopened['decision']['action'], 'work')
        self.assertEqual(self.project.finish('T1')['tasks'][0]['status'], 'completed')
        log = self.project.status()['logPaths'][0]
        self.assertEqual(self.project.read(log).count('## T1'), 2)

    def test_completed_project_clone_requires_review_instead_of_work(self):
        self.create()
        self.project.verify('T1', 'backend')
        self.project.finish('T1')
        with tempfile.TemporaryDirectory() as parent:
            clone_root = Path(parent).resolve() / 'clone'
            shutil.copytree(self.root, clone_root)
            cloned = donghe.Project(clone_root)
            state = cloned.status()
        self.assertEqual(state['tasks'][0]['declaredStatus'], 'completed')
        self.assertNotEqual(state['tasks'][0]['status'], 'completed')
        self.assertNotEqual(state['tasks'][0]['verification'], 'passed')
        self.assertEqual(state['decision']['action'], 'stop')
        self.assertIn('历史完工', state['decision']['reason'])

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
        self.assertEqual(self.project.status()['decision']['action'], 'stop')
        state = self.project.finish('T1')
        self.assertEqual(state['tasks'][0]['status'], 'completed')
        self.project.path(state['logPaths'][0]).unlink()
        self.assertEqual(self.project.status()['tasks'][0]['status'], 'active')

    def test_new_active_task_remains_authorized_work(self):
        self.create()
        state = self.project.status()
        self.assertEqual(state['tasks'][0]['declaredStatus'], 'active')
        self.assertEqual(state['decision']['action'], 'work')

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
        receipt_path = self.project.verify('T1', 'backend')['receiptPath']
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
            with urllib.request.urlopen(base + '/api/receipt?path=' + urllib.parse.quote(receipt_path), timeout=3) as response:
                summary = json.load(response)
                self.assertEqual(summary['integrity'], 'valid')
                self.assertEqual(summary['summary']['formatVersion'], 2)
                self.assertNotIn('before', summary['summary'])
                self.assertEqual(summary['rawPath'], receipt_path)
            with urllib.request.urlopen(base + '/api/receipt?path=../outside', timeout=3) as response:
                self.assertEqual(json.load(response)['integrity'], 'invalid')
            for path in ['../input.txt', 'docs/东合/../../input.txt']:
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(base + '/api/source?path=' + urllib.parse.quote(path), timeout=3)
            (self.root / 'docs/东合/leak.txt').symlink_to(self.root / 'input.txt')
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + '/api/source?path=' + urllib.parse.quote('docs/东合/leak.txt'), timeout=3)
        finally:
            process.terminate()
            process.communicate(timeout=3)

    def replace_receipt_fixture(self, relative, record):
        """Construct old/malformed fixture with matching manifest, not a migration."""
        path = self.project.path(relative)
        path.chmod(0o644)
        path.write_text(donghe.encode(record))
        manifest_path = path.with_name('manifest.json')
        manifest = json.loads(manifest_path.read_text())
        manifest['receipt.json'] = donghe.digest(path.read_bytes())
        manifest_path.chmod(0o644)
        manifest_path.write_text(donghe.encode(manifest))

    def test_v2_shares_objects_across_receipts_and_phases(self):
        self.create(ui=True)
        paths = [self.project.verify('T1', criterion)['receiptPath'] for criterion in ['backend', 'ui']]
        records = [json.loads(self.project.read(path)) for path in paths]
        self.assertEqual(records[0]['beforeRef'], records[0]['afterRef'])
        self.assertEqual(records[0]['beforeRef'], records[1]['beforeRef'])
        for record in records:
            self.assertEqual(record['formatVersion'], 2)
            self.assertNotIn('before', record)
            self.assertNotIn('after', record)
        self.assertEqual(len(list(self.project.path(donghe.DOC + '/证据/指纹').glob('*.json'))), 1)

    def test_legacy_receipt_and_completion_are_read_without_rewrite(self):
        self.create()
        relative = self.project.verify('T1', 'backend')['receiptPath']
        legacy = self.project.receipt(relative)
        for key in ['formatVersion', 'beforeRef', 'afterRef']:
            legacy.pop(key)
        self.replace_receipt_fixture(relative, legacy)
        before = {p: p.read_bytes() for p in self.project.path(relative).parent.iterdir()}
        self.assertEqual(self.project.receipt(relative), legacy)
        self.assertEqual(self.project.receipt_summary(relative)['summary']['formatVersion'], 1)
        self.assertEqual(self.project.finish('T1')['tasks'][0]['status'], 'completed')
        self.assertEqual(self.project.status()['tasks'][0]['status'], 'completed')
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_shared_tamper_invalidates_every_reference_without_cache(self):
        self.create(ui=True)
        paths = [self.project.verify('T1', criterion)['receiptPath'] for criterion in ['backend', 'ui']]
        for path in paths:
            self.assertEqual(self.project.receipt_summary(path)['integrity'], 'valid')
        reference = json.loads(self.project.read(paths[0]))['beforeRef']
        blob = self.project.path(donghe.DOC + '/证据/指纹/' + reference + '.json')
        blob.chmod(0o644)
        blob.write_text('{}')
        for path in paths:
            summary = self.project.receipt_summary(path)
            self.assertEqual(summary['integrity'], 'invalid')
            self.assertIsNone(summary['summary'])
            self.assertEqual(summary['output'], {})
            self.assertIn('fingerprint digest mismatch', summary['error'])
        self.assertTrue(all(c['state'] == 'invalid' for c in self.project.status()['tasks'][0]['criteria']))
        blob.unlink()
        self.assertEqual(self.project.receipt_summary(paths[0])['integrity'], 'invalid')

    def test_object_publish_is_concurrent_and_does_not_repair_corruption(self):
        value = {'input.txt': donghe.digest(b'original')}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            refs = list(pool.map(lambda _: self.project.store_fingerprints(value), range(24)))
        self.assertEqual(len(set(refs)), 1)
        base = self.project.path(donghe.DOC + '/证据/指纹')
        self.assertEqual(len(list(base.iterdir())), 1)
        blob = base / (refs[0] + '.json')
        self.assertEqual(blob.read_bytes(), donghe.encode(value).encode())
        blob.chmod(0o644)
        blob.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            self.project.store_fingerprints(value)
        self.assertEqual(blob.read_bytes(), b'corrupt')
        self.assertEqual(len(list(base.iterdir())), 1)

    def test_missing_input_records_null_references_and_failure(self):
        self.create()
        (self.root / 'input.txt').unlink()
        relative = self.project.verify('T1', 'backend')['receiptPath']
        raw = json.loads(self.project.read(relative))
        self.assertIsNone(raw['beforeRef'])
        self.assertIsNone(raw['afterRef'])
        summary = self.project.receipt_summary(relative)
        self.assertEqual(summary['integrity'], 'valid')
        self.assertIn('input missing', summary['summary']['error'])
        self.assertIsNone(summary['summary']['exitCode'])
        self.assertEqual(summary['summary']['inputCounts'], {'before': 0, 'after': 0})

    def test_unknown_versions_and_unsafe_or_missing_references_rejected(self):
        self.create()
        relative = self.project.verify('T1', 'backend')['receiptPath']
        raw = json.loads(self.project.read(relative))
        fixtures = [{**raw, 'formatVersion': version} for version in [0, 3, '2', True, None]]
        fixtures += [{**raw, 'beforeRef': ref} for ref in ['../other.json', 'A' * 64, 7, {}, 'a' * 63]]
        fixtures += [{key: value for key, value in raw.items() if key != 'beforeRef'}]
        fixtures += [{**raw, 'before': {}}]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.replace_receipt_fixture(relative, fixture)
                result = self.project.receipt_summary(relative)
                self.assertEqual(result['integrity'], 'invalid')
                self.assertIsNone(result['summary'])
                self.assertEqual(result['output'], {})

    def test_summary_previews_are_bounded_and_streams_are_checked(self):
        self.create(command=[sys.executable, '-c', "import sys; sys.stdout.buffer.write(b'x'*20000); sys.stderr.buffer.write(b'\\xff'*5000); sys.exit(4)"])
        relative = self.project.verify('T1', 'backend')['receiptPath']
        summary = self.project.receipt_summary(relative)
        self.assertEqual(summary['integrity'], 'valid')
        self.assertEqual(summary['summary']['exitCode'], 4)
        self.assertFalse(summary['summary']['inputChanged'])
        for key, size in [('stdout', 20000), ('stderr', 5000)]:
            output = summary['output'][key]
            self.assertTrue(output['truncated'])
            self.assertEqual(output['bytes'], size)
            self.assertLessEqual(len(output['preview'].encode()), 4096)
        stderr = self.project.path(relative).with_name('stderr.txt')
        stderr.chmod(0o644)
        stderr.write_text('tampered')
        self.assertEqual(self.project.receipt_summary(relative)['integrity'], 'invalid')

    def test_missing_receipt_and_hash_valid_malformed_blob_fail_closed(self):
        self.assertEqual(self.project.receipt_summary(donghe.DOC + '/证据/missing/receipt.json')['integrity'], 'invalid')
        self.create()
        relative = self.project.verify('T1', 'backend')['receiptPath']
        raw = json.loads(self.project.read(relative))
        for data in [b'{', b'[]', b'{"input.txt":"untrusted"}']:
            reference = donghe.digest(data)
            self.project.write(donghe.DOC + '/证据/指纹/' + reference + '.json', data.decode())
            self.replace_receipt_fixture(relative, {**raw, 'beforeRef': reference})
            self.assertEqual(self.project.receipt_summary(relative)['integrity'], 'invalid')

    def test_receipt_cli_returns_bounded_summary_and_invalid_exit(self):
        self.create(command=[sys.executable, '-c', 'raise SystemExit(7)'])
        relative = self.project.verify('T1', 'backend')['receiptPath']
        command = [sys.executable, str(MODULE), '--project', str(self.root), 'receipt', relative]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['summary']['exitCode'], 7)
        self.project.path(relative).with_name('stdout.txt').unlink()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['integrity'], 'invalid')

    def test_self_consistent_manifest_does_not_accept_malformed_core_types(self):
        task = self.create()
        relative = self.project.verify('T1', 'backend')['receiptPath']
        raw = json.loads(self.project.read(relative))
        legacy = self.project.receipt(relative)
        for key in ['formatVersion', 'beforeRef', 'afterRef']:
            legacy.pop(key)
        for base in [raw, legacy]:
            mutations = [
                ('exitCode', False), ('exitCode', True), ('exitCode', '0'), ('exitCode', 0.0),
                ('error', False), ('error', {}), ('taskId', '../T1'), ('criterionId', 3),
                ('argv', []), ('argv', 'python'), ('argv', [False]), ('argv', ['']),
                ('argv', ['python', '\0']), ('startedAt', 5), ('finishedAt', 'yesterday'),
                ('contractHash', None), ('contractHash', 'unverified'),
                ('environment', []), ('environment', {}),
                ('environment', {**raw['environment'], 'sha256': False}),
            ]
            for field, value in mutations:
                with self.subTest(version=base.get('formatVersion', 1), field=field, value=value):
                    self.replace_receipt_fixture(relative, {**base, field: value})
                    summary = self.project.receipt_summary(relative)
                    self.assertEqual(summary['integrity'], 'invalid')
                    self.assertIsNone(summary['summary'])
                    self.assertEqual(summary['output'], {})
                    self.assertEqual(self.project.inspect(task, task['criteria'][0])['state'], 'invalid')
            for field in ['environment', 'contractHash']:
                self.replace_receipt_fixture(relative, {key: value for key, value in base.items() if key != field})
                self.assertEqual(self.project.receipt_summary(relative)['integrity'], 'invalid')


if __name__ == '__main__':
    unittest.main()
