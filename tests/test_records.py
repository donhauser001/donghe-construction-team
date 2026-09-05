import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('donghe_records', ROOT / 'scripts/donghe_records.py')
records = importlib.util.module_from_spec(spec)
spec.loader.exec_module(records)


class Project:
    def __init__(self, root):
        self.root = Path(root)
        self.tasks = {}

    def path(self, relative):
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('unsafe path')
        return self.root / path

    def read(self, relative):
        path = self.path(relative)
        return path.read_text() if path.exists() else None

    def write(self, relative, content):
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def task(self, task_id):
        if task_id not in self.tasks:
            raise ValueError('task does not exist')
        return self.tasks[task_id]


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Project(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, record_id='I1', kind='idea', **extra):
        return records.create(self.project, {'id': record_id, 'kind': kind, 'title': '资料', 'body': '正文内容', **extra})

    def test_create_read_catalog_and_global_duplicate(self):
        created = self.create(createdAt='2026-08-01T00:00:00+00:00', updatedAt='2026-08-02T00:00:00+00:00')
        self.assertEqual(created['path'], 'docs/东合/资料/idea/I1.md')
        self.assertEqual(created['updatedAt'], '2026-08-02T00:00:00+00:00')
        index = records.catalog(self.project)
        self.assertEqual(index['records'][0]['summary'], '正文内容')
        self.assertNotIn('body', index['records'][0])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.create('I1', 'blueprint')
        duplicate = self.project.read(created['path']).replace('"kind": "idea"', '"kind": "blueprint"')
        self.project.write('docs/东合/资料/blueprint/I1.md', duplicate)
        self.assertIn('duplicate_id', {issue['code'] for issue in records.catalog(self.project)['issues']})

    def test_link_reverse_index_and_idempotence(self):
        self.create('I1')
        self.create('B1', 'blueprint')
        first = records.link(self.project, 'B1', 'I1', 'derived_from')
        second = records.link(self.project, 'B1', 'I1', 'derived_from')
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        self.assertEqual(records.catalog(self.project)['relations'], [
            {'source': 'B1', 'target': 'I1', 'type': 'derived_from'}])

    def test_bad_metadata_missing_targets_and_cycles_are_diagnosed(self):
        self.create('I1')
        path = 'docs/东合/资料/idea/bad.md'
        self.project.write(path, '```donghe-meta\n{bad json}\n```\n')
        good = self.project.read('docs/东合/资料/idea/I1.md').replace('"relations": []', '"relations": [{"target": "MISSING", "type": "implements"}]')
        self.project.write('docs/东合/资料/idea/I1.md', good)
        codes = {issue['code'] for issue in records.catalog(self.project)['issues']}
        self.assertIn('invalid_record', codes)
        self.assertIn('missing_record_target', codes)
        # An unrelated diagnostic remains visible but does not block a valid write.
        self.create('I2')
        records.link(self.project, 'I1', 'I2', 'supersedes')
        before = self.project.read('docs/东合/资料/idea/I2.md')
        with self.assertRaisesRegex(ValueError, 'cycle'):
            records.link(self.project, 'I2', 'I1', 'supersedes')
        self.assertEqual(before, self.project.read('docs/东合/资料/idea/I2.md'))

    def test_task_target_validation_and_unsafe_values(self):
        self.create()
        with self.assertRaisesRegex(ValueError, 'task does not exist'):
            records.link(self.project, 'I1', 'task:T404', 'implements')
        source = self.project.read('docs/东合/资料/idea/I1.md')
        source = source.replace('"relations": []', '"relations": [{"target": "task:T404", "type": "implements"}]')
        self.project.write('docs/东合/资料/idea/I1.md', source)
        self.assertIn('missing_task_target', {issue['code'] for issue in records.catalog(self.project)['issues']})
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            self.create('../escape')
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            records.link(self.project, 'I1', 'I1', 'invented')

    def test_feedback_uses_completed_fact_and_is_idempotent(self):
        self.project.tasks['T1'] = {
            'id': 'T1', 'status': 'completed',
            'events': [
                {'kind': 'verified', 'at': '2026-09-05T01:00:00+00:00', 'receiptPath': 'docs/东合/证据/r1/receipt.json'},
                {'kind': 'completed', 'at': '2026-09-05T01:01:00+00:00'},
            ],
        }
        self.create(state='open')
        records.link(self.project, 'I1', 'task:T1', 'implements')
        self.assertIn('missing_feedback', {issue['code'] for issue in records.catalog(self.project)['issues']})
        first = records.feedback(self.project, 'T1')
        source = self.project.read('docs/东合/资料/idea/I1.md')
        second = records.feedback(self.project, 'T1')
        self.assertEqual(first['updated'], ['I1'])
        self.assertEqual(second['unchanged'], ['I1'])
        self.assertEqual(source, self.project.read('docs/东合/资料/idea/I1.md'))
        record = records.catalog(self.project)['records'][0]
        self.assertEqual(record['state'], 'open')
        self.assertEqual(record['feedback'][0]['taskId'], 'T1')
        self.assertNotIn('missing_feedback', {issue['code'] for issue in records.catalog(self.project)['issues']})

    def test_feedback_requires_completed_and_relation(self):
        self.project.tasks['T1'] = {'id': 'T1', 'status': 'active', 'events': []}
        self.create()
        with self.assertRaisesRegex(ValueError, 'not declared completed'):
            records.feedback(self.project, 'T1')
        self.project.tasks['T1']['status'] = 'completed'
        self.project.tasks['T1']['events'] = [{'kind': 'completed', 'at': 'x'}]
        self.assertEqual(records.feedback(self.project, 'T1')['updated'], [])

    def test_feedback_propagates_only_through_explicit_derived_from(self):
        self.project.tasks['T1'] = {'id': 'T1', 'status': 'completed',
                                    'events': [{'kind': 'completed', 'at': '2026-09-05T02:00:00+00:00'}]}
        for record_id in ('I1', 'I2', 'B1'):
            self.create(record_id, 'blueprint' if record_id == 'B1' else 'idea')
        records.link(self.project, 'B1', 'I1', 'derived_from')
        records.link(self.project, 'B1', 'I2', 'relates_to')
        records.link(self.project, 'B1', 'task:T1', 'implements')
        result = records.feedback(self.project, 'T1')
        self.assertEqual(result['updated'], ['B1', 'I1'])
        catalog = {record['id']: record for record in records.catalog(self.project)['records']}
        self.assertNotIn('feedback', catalog['I2'])
        self.assertEqual(catalog['I1']['feedback'][0]['via'], ['B1', 'I1'])
        self.assertEqual({record['state'] for record in catalog.values()}, {'open'})

    def test_archived_feedback_skips_existing_but_requires_restore_when_missing(self):
        completed = '2026-09-05T02:00:00+00:00'
        self.project.tasks['T1'] = {'id': 'T1', 'status': 'completed',
                                    'events': [{'kind': 'completed', 'at': completed}]}
        edge = {'source': 'I1', 'target': 'task:T1', 'type': 'implements'}
        archived = {'id': 'I1', 'kind': 'idea', 'title': '旧资料', 'state': 'closed', 'path': 'x',
                    'archived': True, 'feedback': [{'taskId': 'T1', 'completedAt': completed}]}
        with patch.object(records, 'catalog', return_value={'records': [archived], 'relations': [edge], 'issues': []}):
            self.assertEqual(records.feedback(self.project, 'T1')['unchanged'], ['I1'])
            archived.pop('feedback')
            with self.assertRaisesRegex(ValueError, 'restore'):
                records.feedback(self.project, 'T1')

    def test_update_preserves_identity_creation_and_feedback(self):
        original = self.create()
        changed = records.update(self.project, {'id': 'I1', 'kind': 'idea', 'title': '新标题',
                                                'body': '新正文', 'state': 'active'})
        self.assertEqual(changed['createdAt'], original['createdAt'])
        self.assertEqual(changed['title'], '新标题')
        self.assertEqual(changed['summary'], '新正文')
        with self.assertRaisesRegex(ValueError, 'kind cannot change'):
            records.update(self.project, {'id': 'I1', 'kind': 'debt'})

    def test_schema_and_timestamps_are_strict(self):
        with self.assertRaisesRegex(ValueError, 'ISO timestamp'):
            self.create(createdAt='August')
        with self.assertRaisesRegex(ValueError, 'timezone'):
            self.create(createdAt='2026-08-01')
        self.create()
        path = 'docs/东合/资料/idea/I1.md'
        self.project.write(path, self.project.read(path).replace('"schemaVersion": 1', '"schemaVersion": true'))
        self.assertIn('invalid_record', {issue['code'] for issue in records.catalog(self.project)['issues']})


if __name__ == '__main__':
    unittest.main()
