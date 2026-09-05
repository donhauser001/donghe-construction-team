import json
from pathlib import Path
import tempfile
import unittest
from baseline import FixtureTools, build_fixture, verify_source, FIXED, BUGGY, observe, CASES, snapshot, BASE_REF


class BaselineTests(unittest.TestCase):
    def test_git_snapshot_preserves_chinese_paths(self):
        with tempfile.TemporaryDirectory() as t:
            target=Path(t)/'skill'
            manifest=snapshot(BASE_REF,target)
            self.assertEqual(len(list((target/'playbooks').glob('*.md'))),8)
            self.assertIn('playbooks/08-无守护模式.md',manifest)
            self.assertTrue((target/'templates/任务卡模板.md').is_file())

    def test_fixture_business_oracle_distinguishes_bug(self):
        self.assertTrue(verify_source(FIXED)['passed'])
        self.assertFalse(verify_source(BUGGY)['passed'])

    def test_fixture_rejects_arbitrary_code(self):
        for code in ['import os\nos.system("id")','def normalize(value):\n return value.__class__\n', 'def normalize(value):\n return open("x")\n']:
            with self.assertRaises(ValueError): verify_source(code)

    def test_all_fixtures_and_stale_receipt(self):
        with tempfile.TemporaryDirectory() as t:
            for case in CASES:
                root=build_fixture(Path(t)/case,case)
                self.assertTrue((root/'AGENTS.md').exists())
            root=Path(t)/'B05-stale'
            self.assertNotEqual(json.loads((root/'docs/证据/prior.json').read_text())['source_sha256'],verify_source((root/'app.py').read_text())['source_sha256'])

    def test_gateway_blocks_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t);root=build_fixture(base/'root','B02-complete')
            skill=base/'skill';skill.mkdir();(skill/'SKILL.md').write_text('frozen')
            (root/'docs/link').symlink_to(skill,target_is_directory=True)
            tools=FixtureTools(root,skill,base/'trace')
            for name in ['../secret','/etc/passwd','docs/link/SKILL.md','skill/SKILL.md','index.html']:
                result=tools.call('write_file',{'path':name,'content':'bad'})
                self.assertIn('error',result)
            self.assertEqual((skill/'SKILL.md').read_text(),'frozen')

    def test_self_report_does_not_pass_incomplete_business(self):
        with tempfile.TemporaryDirectory() as t:
            root=build_fixture(Path(t)/'root','B02-complete')
            result=observe(root,'B02-complete',Path(t)/'trace',{'task_status':'completed'},'completed')
            self.assertFalse(result['mechanical_gate'])

    def test_environment_error_is_not_behavior_failure(self):
        with tempfile.TemporaryDirectory() as t:
            root=build_fixture(Path(t)/'root','B01-no-goal')
            result=observe(root,'B01-no-goal',Path(t)/'trace',{},'environment_error')
            self.assertIsNone(result['mechanical_gate'])


if __name__=='__main__':unittest.main()
