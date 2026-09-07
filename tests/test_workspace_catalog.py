import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    'workspace_catalog', Path(__file__).resolve().parents[1] / 'scripts/donghe_workspace_catalog.py')
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


class WorkspaceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        return target

    def test_explicit_membership_status_and_order(self):
        self.write('docs/下一阶段工作.md', '# 工作\n## 当前 focus-79 扩容 <a id="focus-79"></a>')
        for identifier in ('S10', 'S2', 'QA010'):
            self.write(f'docs/任务卡/{identifier}.md', f'# {identifier} · 任务\n'
                       '> - **`parent_focus`**：[`docs/下一阶段工作.md#focus-79`](../下一阶段工作.md#focus-79)\n'
                       '> - 状态：待用户确认（技术已通过）\n> 下一步：等待裁决\n')
        result = catalog.scan_workspace(self.root)
        line = result['lines'][0]
        self.assertEqual(line['id'], 'focus-79')
        self.assertEqual(line['title'], '当前 focus-79 扩容')
        self.assertEqual([task['id'] for task in line['tasks']], ['QA010', 'S2', 'S10'])
        self.assertEqual(line['tasks'][0]['status'], '待用户确认（技术已通过）')
        self.assertIn('等待裁决', line['tasks'][0]['excerpt'])
        self.assertEqual(line['status'], 'unknown')

    def test_never_guess_membership_and_archive_not_current(self):
        self.write('docs/任务卡/S1.md', '# S1 与 focus-79 有关的标题\n正文：已经通过')
        self.write('docs/档案/任务卡/S0.md', '# S0\n> parent_focus：focus-79')
        self.write('docs/任务卡/README.md', '# 导航')
        result = catalog.scan_workspace(self.root)
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['id'], 'unclassified')
        self.assertEqual(result['lines'][0]['tasks'][0]['status'], 'unknown')
        self.assertEqual(len(result['documents']), 3)
        self.assertTrue(next(doc for doc in result['documents'] if 'S0' in doc['path'])['archived'])

    def test_bare_path_and_evidence_are_not_confused_with_task(self):
        self.write('docs/任务卡/S2.md', '# S2\n> parent_focus：`docs/下一阶段工作.md#p9-next`')
        self.write('docs/任务卡/_evidence/report.md', '# S99 evidence')
        result = catalog.scan_workspace(self.root)
        self.assertEqual([line['id'] for line in result['lines']], ['p9-next'])
        self.assertEqual(len(result['lines'][0]['tasks']), 1)

    def test_donghe_json_read_without_init_or_inferred_authorization(self):
        self.write('docs/东合/任务卡/T1.md', '# 示例\n```donghe-json\n'
                   '{"id":"T1","title":"真实任务","status":"blocked",'
                   '"authorization":"用户批准 focus-79 相关尝试"}\n```')
        self.write('docs/东合/任务卡/T2.md', '# 示例\n```donghe-json\n'
                   '{"id":"T2","status":"completed","parent_focus":"focus-79"}\n```')
        before = set(self.root.rglob('*'))
        result = catalog.scan_workspace(self.root)
        self.assertEqual(before, set(self.root.rglob('*')))
        self.assertEqual(result['lines'][0]['tasks'][0]['id'], 'T2')
        self.assertEqual(result['lines'][-1]['tasks'][0]['status'], 'blocked')
        self.assertIn('unresolved_focus', [item['code'] for item in result['diagnostics']])

    def test_symlink_and_non_markdown_secrets_are_not_read(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'secret.md').write_text('SECRET', encoding='utf-8')
        docs = self.root / 'docs'
        docs.mkdir()
        (docs / 'linked').symlink_to(outside, target_is_directory=True)
        (docs / 'secret.md').symlink_to(outside / 'secret.md')
        self.write('docs/.env', 'PASSWORD=secret')
        self.write('README.md', '# project')
        result = catalog.scan_workspace(self.root)
        self.assertEqual([doc['path'] for doc in result['documents']], ['README.md'])
        self.assertEqual(len(result['diagnostics']), 2)

    def test_limits_are_disclosed(self):
        self.write('docs/a.md', '0123456789')
        self.write('docs/b.md', 'abc')
        with patch.object(catalog, 'MAX_DOCUMENT_BYTES', 5):
            result = catalog.scan_workspace(self.root)
        self.assertEqual([doc['path'] for doc in result['documents']], ['docs/b.md'])
        self.assertEqual(result['diagnostics'][0]['code'], 'document_size_limit')
        with patch.object(catalog, 'MAX_TOTAL_BYTES', 11):
            result = catalog.scan_workspace(self.root)
        self.assertEqual(result['diagnostics'][0]['code'], 'total_size_limit')
        with patch.object(catalog, 'MAX_DOCUMENTS', 1):
            result = catalog.scan_workspace(self.root)
        self.assertEqual(result['diagnostics'][0]['code'], 'document_count_limit')

    def test_receipt_workspace_fixtures_are_not_project_sources(self):
        self.write('docs/基线回执/check/workspace/docs/任务卡/S99.md', '# S99 假任务')
        self.write('docs/基线回执/check/report.md', '# 验证回执')
        self.write('docs/任务卡/S1.md', '# S1 真任务')
        result = catalog.scan_workspace(self.root)
        self.assertEqual(len(result['documents']), 2)
        self.assertEqual(result['lines'][0]['tasks'][0]['id'], 'S1')
        self.assertEqual(result['diagnostics'][0]['code'], 'fixture_workspace_skipped')

    def test_explicit_task_line_without_focus_is_supported(self):
        self.write('docs/任务卡/S1.md', '# S1\n> task_line：阅读体验升级\n> 状态：待裁决')
        self.write('docs/东合/任务卡/T2.md', '# T2\n```donghe-json\n'
                   '{"id":"T2","task_line":"阅读体验升级","status":"blocked"}\n```')
        result = catalog.scan_workspace(self.root)
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['title'], '阅读体验升级')
        self.assertEqual(result['lines'][0]['status'], 'unknown')
        self.assertEqual(len(result['lines'][0]['tasks']), 2)
        self.assertEqual(result['diagnostics'], [])


if __name__ == '__main__':
    unittest.main()
