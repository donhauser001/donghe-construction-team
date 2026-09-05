import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import donghe
import donghe_archive as archive
import donghe_records as records


class GovernanceIntegration(unittest.TestCase):
    def test_task_feedback_archive_cold_read_and_restore(self):
        with tempfile.TemporaryDirectory() as root:
            p = donghe.Project(Path(root).resolve())
            p.init()
            (p.root / 'source.txt').write_text('synthetic fixture')
            p.create({'id':'T1','title':'Synthetic acceptance','authorization':'isolated test',
                      'criteria':[{'id':'check','label':'Actual command','command':[sys.executable,'-c','print("verified")'],
                                   'inputs':['source.txt'],'reuse':True}]})
            with p.lock():
                records.create(p, {'id':'I1','kind':'idea','title':'Synthetic idea','body':'Not project history'})
                records.link(p,'I1','task:T1','implements')
            p.verify('T1','check');p.finish('T1')
            source = p.read('docs/东合/资料/idea/I1.md')
            self.assertIn('feedback',source)
            self.assertEqual(p.status()['knowledge']['issues'],[])
            p.finish('T1')
            self.assertEqual(p.read('docs/东合/资料/idea/I1.md'),source)
            class FutureDate(donghe.dt.date):
                @classmethod
                def today(cls): return cls(2099,1,1)
            receipt = p.candidates('T1','check')[0]
            before = {r:p.path(r).read_bytes() for r in archive.logical_files(p,'docs/东合')}
            with patch.object(archive.dt,'date',FutureDate):
                proposed=archive.plan(p)
                self.assertTrue(any(x['source'].endswith('/T1.md') for x in proposed['candidates']))
                archive.apply(p,proposed)
                self.assertEqual(p.receipt_summary(receipt)['integrity'],'valid')
                self.assertEqual(p.status()['tasks'][0]['status'],'completed')
                # New reader, no cached catalog or path mappings.
                q=donghe.Project(Path(root).resolve())
                self.assertEqual(q.status()['decision']['action'],'stop')
                for r,content in before.items(): self.assertEqual(q.path(r).read_bytes(),content)
                month=next(x['target'].split('/档案/')[1][:7] for x in proposed['candidates'])
                archive.restore(q,month)
                for r,content in before.items(): self.assertEqual((q.root/r).read_bytes(),content)

    def test_read_only_status_does_not_archive_and_once_monthly_check(self):
        with tempfile.TemporaryDirectory() as root:
            p=donghe.Project(Path(root).resolve())
            with p.lock():
                records.create(p, {'id':'K1','kind':'knowledge','title':'Synthetic old closed note','body':'Synthetic fixture',
                                   'state':'closed','createdAt':'2020-01-01T00:00:00Z','updatedAt':'2020-01-02T00:00:00Z'})
            logical='docs/东合/资料/knowledge/K1.md'
            self.assertGreater(p.status()['archive']['count'],0)
            self.assertTrue((p.root/logical).exists())
            p.init()
            self.assertFalse((p.root/logical).exists())
            self.assertEqual(p.status()['knowledge']['records'][0]['archived'],True)
            marker=p.read('.donghe/state/maintenance.json')
            p.init()
            self.assertEqual(p.read('.donghe/state/maintenance.json'),marker)

    def test_invalid_metadata_does_not_fail_init_or_accepted_finish(self):
        with tempfile.TemporaryDirectory() as root:
            p=donghe.Project(Path(root).resolve())
            bad='docs/东合/资料/knowledge/BAD.md'
            p.write(bad,'# Broken source\n```donghe-meta\nnot json\n```\n')
            state=p.init()
            self.assertEqual(state['maintenance']['state'],'blocked')
            self.assertTrue(state['knowledge']['issues'])
            marker=p.read('.donghe/state/maintenance.json')
            p.init()
            self.assertEqual(p.read('.donghe/state/maintenance.json'),marker)
            (p.root/'source.txt').write_text('real fixture input')
            p.create({'id':'T1','title':'Unaffected task','authorization':'test',
                      'criteria':[{'id':'check','label':'Actual command','command':[sys.executable,'-c','print("ok")'],
                                   'inputs':['source.txt'],'reuse':True}]})
            p.verify('T1','check')
            # Exercise maintenance during finish, rather than only reuse init's marker.
            p.path('.donghe/state/maintenance.json').unlink()
            state=p.finish('T1')
            self.assertEqual(state['tasks'][0]['status'],'completed')
            self.assertEqual(state['maintenance']['state'],'blocked')
            self.assertEqual(state['decision']['action'],'stop')
            with self.assertRaises(ValueError): archive.apply(p,archive.plan(p))
