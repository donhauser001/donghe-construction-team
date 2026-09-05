import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import donghe_archive as archive


class FakeProject:
    def __init__(self, root):
        self.root = Path(root).resolve()


def fenced(kind, value):
    return f"# record\n\n```{kind}\n{json.dumps(value)}\n```\n"


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = FakeProject(self.temp.name)
        self.old = "2020-03"
        self.task = {
            "id": "T1", "status": "completed",
            "events": [{"kind": "completed", "at": "2020-03-20T10:00:00+00:00"}],
        }
        self.write("docs/东合/任务卡/T1.md", fenced("donghe-json", self.task))
        self.write("docs/东合/证据/T1--backend--one/receipt.json", "receipt bytes\n")
        self.write("docs/东合/证据/T1--backend--one/stdout.txt", "stdout\n")
        self.write("docs/东合/证据/指纹/shared.json", "shared\n")
        self.write("docs/东合/资料/idea/I1.md", fenced("donghe-meta", {"state": "closed", "updatedAt": "2020-03-03"}))
        self.write("docs/东合/资料/idea/ACTIVE.md", fenced("donghe-meta", {"state": "active", "updatedAt": "2020-03-03"}))
        self.write("docs/东合/开发日志/2020-03-20.md", "journal exact\n")
        self.write("docs/东合/工作交接.md", "handoff\n")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, content):
        path = self.project.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_plan_is_read_only_and_selects_closed_records_and_receipts(self):
        before = sorted(p.relative_to(self.project.root).as_posix() for p in self.project.root.rglob("*"))
        result = archive.plan(self.project)
        sources = {c["source"] for c in result["candidates"]}
        self.assertEqual(result["count"], 5)
        self.assertIn("docs/东合/任务卡/T1.md", sources)
        self.assertIn("docs/东合/资料/idea/I1.md", sources)
        self.assertIn("docs/东合/开发日志/2020-03-20.md", sources)
        self.assertIn("docs/东合/证据/T1--backend--one/receipt.json", sources)
        self.assertIn("docs/东合/证据/T1--backend--one/stdout.txt", sources)
        self.assertNotIn("docs/东合/证据/指纹/shared.json", sources)
        self.assertNotIn("docs/东合/工作交接.md", sources)
        self.assertNotIn("docs/东合/资料/idea/ACTIVE.md", sources)
        self.assertEqual(before, sorted(p.relative_to(self.project.root).as_posix() for p in self.project.root.rglob("*")))

    def test_apply_preserves_bytes_cold_reads_and_is_idempotent(self):
        expected = (self.project.root / "docs/东合/任务卡/T1.md").read_bytes()
        result = archive.apply(self.project, archive.plan(self.project, self.old))
        self.assertEqual(result["state"], "committed")
        logical = "docs/东合/任务卡/T1.md"
        self.assertFalse((self.project.root / logical).exists())
        self.assertEqual(archive.resolve(self.project, logical).read_bytes(), expected)
        self.assertIn(logical, archive.logical_files(self.project, "docs/东合/任务卡"))
        state_count = len(list((self.project.root / ".donghe/state/archive").glob("*.json")))
        self.assertEqual(archive.apply(self.project, archive.plan(self.project, self.old))["count"], 0)
        self.assertEqual(len(list((self.project.root / ".donghe/state/archive").glob("*.json"))), state_count)

    def test_interrupted_apply_retries_only_the_same_prepared_operation(self):
        planned = archive.plan(self.project, self.old)
        with mock.patch.dict(os.environ, {"DONGHE_TEST_FAIL_AFTER_ARCHIVE_MOVE": "2"}):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                archive.apply(self.project, planned)
        # A new plan sees only the still-live subset. apply must recover the
        # durable prepared operation rather than create a second operation.
        result = archive.apply(self.project, archive.plan(self.project, self.old))
        self.assertEqual(result["state"], "committed")
        self.assertEqual(result["count"], len(planned["candidates"]))

    def test_conflict_and_changed_source_are_rejected_without_overwrite(self):
        planned = archive.plan(self.project, self.old)
        first = planned["candidates"][0]
        target = self.write(first["target"], "collision\n")
        with self.assertRaisesRegex(ValueError, "both exist"):
            archive.apply(self.project, planned)
        self.assertEqual(target.read_text(), "collision\n")
        target.unlink()
        source = self.project.root / first["source"]
        source.write_text("changed\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            archive.apply(self.project, planned)

    def test_restore_reverses_month_and_keeps_operation_evidence(self):
        planned = archive.plan(self.project, self.old)
        original = {c["source"]: (self.project.root / c["source"]).read_bytes() for c in planned["candidates"]}
        archive.apply(self.project, planned)
        result = archive.restore(self.project, self.old)
        self.assertEqual(result["state"], "committed")
        for relative, data in original.items():
            self.assertEqual((self.project.root / relative).read_bytes(), data)
        states = list((self.project.root / ".donghe/state/archive").glob("*.json"))
        self.assertEqual(len(states), 2)

    def test_interrupted_restore_resumes_prepared_operation(self):
        archive.apply(self.project, archive.plan(self.project, self.old))
        with mock.patch.dict(os.environ, {"DONGHE_TEST_FAIL_AFTER_ARCHIVE_MOVE": "2"}):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                archive.restore(self.project, self.old)
        result = archive.restore(self.project, self.old)
        self.assertEqual(result["state"], "committed")
        self.assertTrue((self.project.root / "docs/东合/任务卡/T1.md").exists())

    def test_restore_refuses_live_conflict(self):
        archive.apply(self.project, archive.plan(self.project, self.old))
        live = self.write("docs/东合/任务卡/T1.md", "new live copy\n")
        with self.assertRaisesRegex(ValueError, "both exist"):
            archive.restore(self.project, self.old)
        self.assertEqual(live.read_text(), "new live copy\n")

    def test_duplicate_logical_path_and_symlink_escape_are_rejected(self):
        logical = "docs/东合/任务卡/T1.md"
        self.write(f"docs/东合/档案/2020-02/{logical}", "duplicate\n")
        with self.assertRaisesRegex(ValueError, "live storage and archive"):
            archive.resolve(self.project, logical)
        outside = Path(self.temp.name).parent / "donghe-outside"
        outside.mkdir(exist_ok=True)
        link = self.project.root / "docs/东合/档案/2020-01"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            archive.logical_files(self.project, "docs/东合/任务卡")
        link.unlink()
        outside.rmdir()

    def test_live_and_archived_directories_merge_without_directory_conflict(self):
        self.write("docs/东合/档案/2020-02/docs/东合/任务卡/OLD.md", "old\n")
        resolved = archive.resolve(self.project, "docs/东合/任务卡")
        self.assertEqual(resolved, self.project.root / "docs/东合/任务卡")
        self.assertEqual(archive.logical_files(self.project, "docs/东合/任务卡"), [
            "docs/东合/任务卡/OLD.md", "docs/东合/任务卡/T1.md"])

    def test_restore_rejects_archive_bytes_changed_since_recorded_move(self):
        archive.apply(self.project, archive.plan(self.project, self.old))
        cold = self.project.root / f"docs/东合/档案/{self.old}/docs/东合/任务卡/T1.md"
        cold.write_text("tampered\n")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            archive.restore(self.project, self.old)

    def test_cold_restore_without_journal_reports_transport_only(self):
        logical = "docs/东合/开发日志/2019-01-02.md"
        cold = f"docs/东合/档案/2019-01/{logical}"
        self.write(cold, "cold bytes\n")
        result = archive.restore(self.project, "2019-01")
        self.assertEqual(result["integrity"], "transport-only")
        self.assertEqual(result["unverified"], [logical])
        self.assertEqual((self.project.root / logical).read_text(), "cold bytes\n")

    def test_corrupt_pending_journal_cannot_move_ungoverned_file(self):
        state = self.project.root / ".donghe/state/archive"
        state.mkdir(parents=True)
        candidate = {"source": "victim.txt", "target": f"{archive.ARCHIVE}/{self.old}/victim.txt", "sha256": "0" * 64}
        op = {"id": "0" * 64, "kind": "archive", "month": self.old, "state": "prepared", "candidates": [candidate], "moved": []}
        (state / ("archive-" + "0" * 64 + ".json")).write_text(json.dumps(op))
        victim = self.write("victim.txt", "keep\n")
        with self.assertRaisesRegex(ValueError, "identity mismatch|escapes"):
            archive.apply(self.project, archive.plan(self.project, self.old))
        self.assertEqual(victim.read_text(), "keep\n")

    def test_future_and_current_month_are_rejected(self):
        current = dt.date.today().strftime("%Y-%m")
        with self.assertRaisesRegex(ValueError, "earlier"):
            archive.plan(self.project, current)
        with self.assertRaisesRegex(ValueError, "earlier"):
            archive.restore(self.project, "9999-12")


if __name__ == "__main__":
    unittest.main()
