import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import donghe
import donghe_graph as graph

class ProductIntegration(unittest.TestCase):
    def test_old_project_scan_adopt_source_without_new_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); (root/'README.md').write_text('# Existing architecture\nSource-backed fact.')
            spec=root/'adopt.json';spec.write_text(json.dumps({'id':'K1','kind':'knowledge','sourcePath':'README.md','userNote':'Explicit bounded adoption'}))
            def call(*args):
                out=io.StringIO()
                with contextlib.redirect_stdout(out): rc=donghe.main(['--project',str(root),*args])
                self.assertEqual(rc,0)
                return json.loads(out.getvalue())
            self.assertTrue(call('scan')['candidates'])
            call('adopt','--spec',str(spec))
            self.assertFalse(call('source','K1')['stale'])
            (root/'README.md').write_text('# Existing architecture\nChanged fact.')
            self.assertTrue(call('source','K1')['stale'])
            self.assertEqual(call('next')['action'],'stop')
            self.assertEqual(call('graph','search','missing')['mode'],'fallback')

    def test_same_failed_graph_inputs_do_not_loop_and_changes_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'a.py').write_text('def a(): pass\n')
            fake=root/'engine';fake.write_text('fake marker only')
            p=donghe.Project(root)
            with patch.object(graph,'DEFAULT_BINARY',fake),patch.object(graph,'refresh',side_effect=RuntimeError('engine failed')) as refresh:
                first=p.update_graph();second=p.update_graph()
                self.assertEqual(first,second);self.assertEqual(refresh.call_count,1)
                (root/'a.py').write_text('def b(): pass\n')
                p.update_graph();self.assertEqual(refresh.call_count,2)
                self.assertEqual(p.status()['decision']['action'],'stop')

    def test_finished_ui_evidence_archives_with_images_and_recovers(self):
        import donghe_archive as archive
        with tempfile.TemporaryDirectory() as tmp:
            p=donghe.Project(Path(tmp).resolve())
            run='docs/东合/证据/UI/synthetic'
            p.write(run+'/result.json',json.dumps({'status':'failed','finishedAt':'2020-01-02T00:00:00Z'}))
            p.write(run+'/shot.png','synthetic byte preservation fixture, not a real PNG')
            proposed=archive.plan(p,'2020-01')
            self.assertEqual(proposed['count'],2)
            archive.apply(p,proposed)
            self.assertIn('/档案/',str(p.path(run+'/shot.png')))
            self.assertEqual(p.read(run+'/shot.png'),'synthetic byte preservation fixture, not a real PNG')
            archive.restore(p,'2020-01')
            self.assertTrue((p.root/run/'shot.png').exists())
