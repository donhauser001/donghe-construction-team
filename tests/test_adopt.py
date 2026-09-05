import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('donghe_adopt', ROOT / 'scripts/donghe_adopt.py')
adopt_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adopt_module)


class Project:
    def __init__(self, root):
        self.root = Path(root)
        self.fail_manifest_once = False

    def path(self, relative):
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('unsafe path')
        return self.root / path

    def read(self, relative):
        path = self.path(relative)
        return path.read_text() if path.exists() else None

    def write(self, relative, content):
        if relative == adopt_module.MANIFEST and self.fail_manifest_once:
            self.fail_manifest_once = False
            raise OSError('simulated interrupted manifest write')
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def put(self, relative, content):
        self.write(relative, content)


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Project(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_scan_is_bounded_and_excludes_managed_secret_and_symlink_sources(self):
        p = self.project
        p.put('README.md', '# 项目入口\n真实方向说明')
        p.put('docs/README.md', '# 文档入口\n文档索引')
        p.put('docs/plan.md', '# 路线\n现有路线正文')
        p.put('docs/东合/资料/knowledge/K.md', '# 机器资料\n不得回扫')
        p.put('secrets/private.md', '# secret')
        p.put('governance/decision.md', '# 决策\n显式目录事实')
        (p.root / 'docs' / 'linked.md').symlink_to(p.root / 'governance' / 'decision.md')
        result = adopt_module.scan(p, directories=['governance'])
        paths = {item['path'] for item in result['candidates']}
        self.assertEqual(paths, {'README.md', 'docs/README.md', 'docs/plan.md', 'governance/decision.md'})
        self.assertNotIn('docs/linked.md', paths)
        limited = adopt_module.scan(p, limit=2, directories=['governance'])
        self.assertEqual(len(limited['candidates']), 2)
        self.assertTrue(limited['truncated'])
        self.assertLessEqual(limited['bytesRead'], adopt_module.MAX_TOTAL_BYTES)

    def test_scan_rejects_malicious_directories_and_marks_oversize_skipped(self):
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            adopt_module.scan(self.project, directories=['../outside'])
        with self.assertRaisesRegex(ValueError, 'excluded'):
            adopt_module.scan(self.project, directories=['secrets'])
        self.project.put('docs/huge.md', 'x' * (adopt_module.MAX_FILE_BYTES + 1))
        result = adopt_module.scan(self.project)
        self.assertEqual(result['candidates'], [])
        self.assertTrue(result['truncated'])

    def test_adopt_is_evidenced_idempotent_and_has_no_git_dependency(self):
        p = self.project
        original = '# 当前方向\n只记录已有事实。\n'
        p.put('docs/current.md', original)
        request = {'id': 'K1', 'kind': 'knowledge', 'sourcePath': 'docs/current.md',
                   'userNote': '用户明确要求接入这份现有说明'}
        first = adopt_module.adopt(p, request)
        second = adopt_module.adopt(p, request)
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        self.assertFalse(second['updated'])
        manifest = json.loads(p.read(adopt_module.MANIFEST))
        self.assertEqual(manifest['sources']['K1']['path'], 'docs/current.md')
        record_text = p.read('docs/东合/资料/knowledge/K1.md')
        self.assertIn('来源登记：`K1`', record_text)
        self.assertIn('只记录已有事实', record_text)
        self.assertNotIn('docs/current.md', record_text)
        self.assertNotIn(manifest['sources']['K1']['sha256'], record_text)
        readback = adopt_module.source(p, 'K1')
        self.assertEqual(readback['content'], original)
        self.assertFalse(readback['stale'])
        self.assertFalse((p.root / '.git').exists())

    def test_source_change_requires_update_and_readback_marks_stale(self):
        p = self.project
        p.put('AGENTS.md', '# 规则\n第一版')
        request = {'id': 'B1', 'kind': 'blueprint', 'sourcePath': 'AGENTS.md',
                   'userNote': '用户指定现有规则'}
        first = adopt_module.adopt(p, request)
        p.put('AGENTS.md', '# 规则\n第二版')
        self.assertTrue(adopt_module.source(p, 'B1')['stale'])
        with self.assertRaisesRegex(ValueError, 'explicit update'):
            adopt_module.adopt(p, request)
        updated = adopt_module.adopt(p, {**request, 'update': True})
        self.assertTrue(updated['updated'])
        self.assertFalse(adopt_module.source(p, 'B1')['stale'])
        self.assertNotEqual(first['source']['sha256'], updated['source']['sha256'])

    def test_missing_unsafe_and_unregistered_sources_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            adopt_module.adopt(self.project, {'id': 'K1', 'kind': 'knowledge',
                'sourcePath': 'docs/missing.md', 'userNote': '明确说明'})
        with self.assertRaisesRegex(ValueError, 'managed'):
            adopt_module.adopt(self.project, {'id': 'K1', 'kind': 'knowledge',
                'sourcePath': 'docs/东合/private.md', 'userNote': '明确说明'})
        with self.assertRaisesRegex(ValueError, 'not registered'):
            adopt_module.source(self.project, '../../README.md')

    def test_manifest_write_interruption_recovers_without_duplicate_record(self):
        p = self.project
        p.put('docs/fact.md', '# 事实\n现有说明')
        request = {'id': 'N1', 'kind': 'next', 'sourcePath': 'docs/fact.md',
                   'userNote': '用户指定接入'}
        p.fail_manifest_once = True
        with self.assertRaisesRegex(OSError, 'interrupted'):
            adopt_module.adopt(p, request)
        self.assertIsNone(p.read(adopt_module.MANIFEST))
        recovered = adopt_module.adopt(p, request)
        self.assertTrue(recovered['recovered'])
        self.assertEqual(len(list((p.root / 'docs/东合/资料/next').glob('N1.md'))), 1)
        self.assertFalse(adopt_module.source(p, 'N1')['stale'])

    def test_registered_symlink_or_tampered_manifest_cannot_browse_root(self):
        p = self.project
        p.put('README.md', '# safe')
        adopt_module.adopt(p, {'id': 'K1', 'kind': 'knowledge', 'sourcePath': 'README.md',
                               'userNote': '明确说明'})
        manifest = json.loads(p.read(adopt_module.MANIFEST))
        manifest['sources']['K1']['path'] = '../outside.md'
        p.write(adopt_module.MANIFEST, json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            adopt_module.source(p, 'K1')
        manifest['sources']['K1']['path'] = 'docs/link.md'
        p.put('outside.md', '# outside')
        (p.root / 'docs').mkdir(exist_ok=True)
        (p.root / 'docs/link.md').symlink_to(p.root / 'outside.md')
        p.write(adopt_module.MANIFEST, json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'symlink'):
            adopt_module.source(p, 'K1')
        manifest['sources']['K1']['sha256'] = 'not-a-digest'
        p.write(adopt_module.MANIFEST, json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'invalid source registration'):
            adopt_module.source(p, 'K1')


if __name__ == '__main__':
    unittest.main()
