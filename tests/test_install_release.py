import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_release', ROOT/'scripts/install_release.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


class InstallReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/Users/aiden/dev')
        self.base = Path(self.temp.name).resolve()
        self.package = self.base/'package'
        (self.package/'bin').mkdir(parents=True)
        (self.package/'runtime/codegraph').mkdir(parents=True)
        self.write_executable('bin/donghe', '#!/bin/sh\nexit 0\n')
        self.write_executable('runtime/codegraph/codebase-memory-mcp', '#!/bin/sh\nexit 0\n')
        self.write('payload.txt', 'release payload\n')
        self.manifest()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, content):
        path = self.package/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def write_executable(self, relative, content):
        path = self.write(relative, content)
        path.chmod(0o755)
        return path

    def manifest(self):
        files = {}
        for path in sorted(self.package.rglob('*')):
            if path.is_file() and path.name != 'manifest.json':
                files[path.relative_to(self.package).as_posix()] = {
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                    'mode': stat.S_IMODE(path.stat().st_mode),
                }
        (self.package/'manifest.json').write_text(json.dumps({
            'schemaVersion': 1, 'platform': 'darwin-arm64', 'files': files,
        }))

    def run_main(self, targets, backup, fail_after=None):
        argv = ['install_release.py', '--backup-root', str(backup), *map(str, targets)]
        environment = {} if fail_after is None else {'DONGHE_TEST_FAIL_AFTER_INSTALL_SWITCH': str(fail_after)}
        with mock.patch.object(install, 'ROOT', self.package), \
             mock.patch.object(install.platform, 'system', return_value='Darwin'), \
             mock.patch.object(install.platform, 'machine', return_value='arm64'), \
             mock.patch('sys.argv', argv), mock.patch.dict(os.environ, environment, clear=False):
            install.main()

    def test_verify_rejects_executable_mode_drift(self):
        graph = self.package/'runtime/codegraph/codebase-memory-mcp'
        graph.chmod(0o644)
        with mock.patch.object(install.platform, 'system', return_value='Darwin'), \
             mock.patch.object(install.platform, 'machine', return_value='arm64'):
            with self.assertRaisesRegex(ValueError, 'mode mismatch'):
                install.verify(self.package)

    def test_all_targets_are_prepared_before_any_switch(self):
        first = self.base/'one/donghe-construction-team'
        blocker = self.base/'blocked'
        blocker.write_text('not a directory')
        second = blocker/'donghe-construction-team'
        with self.assertRaises(FileExistsError):
            self.run_main([first, second], self.base/'backups')
        self.assertFalse(first.exists())

    def test_second_commit_failure_rolls_back_all_targets_and_keeps_failed_installs(self):
        first = self.base/'one/donghe-construction-team'
        second = self.base/'two/donghe-construction-team'
        first.mkdir(parents=True)
        (first/'OLD').write_text('previous bytes\n')

        with self.assertRaisesRegex(RuntimeError, 'injected install commit interruption'):
            self.run_main([first, second], self.base/'backups', fail_after=2)

        self.assertEqual((first/'OLD').read_text(), 'previous bytes\n')
        self.assertFalse(second.exists())
        receipts = list((self.base/'backups').glob('*/install.json'))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        self.assertEqual(receipt['state'], 'rolled_back')
        self.assertEqual(len(receipt['rolledBack']), 2)
        for item in receipt['rolledBack']:
            self.assertTrue(Path(item['failedInstall']).is_dir())
        self.assertTrue(any((path/'payload.txt').is_file()
                            for path in receipts[0].parent.glob('*.failed')))

    def test_success_preserves_previous_for_manual_recovery(self):
        target = self.base/'one/donghe-construction-team'
        target.mkdir(parents=True)
        (target/'OLD').write_text('previous bytes\n')
        self.run_main([target], self.base/'backups')
        receipt_path = next((self.base/'backups').glob('*/install.json'))
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt['state'], 'committed')
        previous = Path(receipt['targets'][0]['previous'])
        self.assertEqual((previous/'OLD').read_text(), 'previous bytes\n')
        self.assertEqual((target/'payload.txt').read_text(), 'release payload\n')

    def test_backup_root_symlink_and_overlap_are_rejected(self):
        target = self.base/'one/donghe-construction-team'
        real = self.base/'real-backups'
        real.mkdir()
        linked = self.base/'linked-backups'
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'invalid backup root'):
            self.run_main([target], linked)
        with self.assertRaisesRegex(ValueError, 'must be separate'):
            self.run_main([target], target/'backups')


if __name__ == '__main__':
    unittest.main()
