import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import donghe_catalog as catalog
import donghe_records as records


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, relative):
        return self.root / relative

    def task(self, _task_id):
        raise ValueError("synthetic fixture has no tasks")


class CatalogQueryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Project(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def render_synthetic(self, count=1000):
        """Write a synthetic corpus directly; records.create would repeatedly rescan it."""
        stamp = "2026-09-05T00:00:00+00:00"
        for number in range(count):
            record_id = f"S{number:04d}"
            kind = "idea" if number % 2 == 0 else "knowledge"
            relations = [{"target": f"S{number - 1:04d}", "type": "relates_to"}] if number and number % 100 == 0 else []
            meta = {"schemaVersion": 1, "id": record_id, "kind": kind, "title": f"Synthetic {record_id}",
                    "state": "open", "createdAt": stamp, "updatedAt": stamp, "relations": relations}
            path = self.project.path(f"docs/东合/资料/{kind}/{record_id}.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(records._render(meta, f"synthetic keyword-{number:04d}"))

    def test_filter_pagination_and_incident_edges(self):
        data = {"records": [
                    {"id": "A", "kind": "idea", "title": "Alpha", "summary": "one", "path": "a", "archived": True},
                    {"id": "B", "kind": "idea", "title": "Beta", "summary": "two", "path": "b"},
                    {"id": "C", "kind": "knowledge", "title": "Gamma", "summary": "three", "path": "c"}],
                "relations": [{"source": "A", "target": "B", "type": "derived_from"},
                              {"source": "C", "target": "external-id", "type": "relates_to"}],
                "issues": []}
        with mock.patch.object(catalog.records, "catalog", return_value=data):
            first = catalog.query(self.project, kind="idea", offset=0, limit=1)
            second = catalog.query(self.project, kind="idea", offset=1, limit=1)
            self.assertTrue(first["records"][0]["archived"])
            self.assertEqual(first["edges"][0]["target"], "B")
            self.assertEqual(second["edges"][0]["source"], "A")
            self.assertEqual((first["nextOffset"], second["nextOffset"]), (1, None))
            external = catalog.query(self.project, text="Gamma")
            self.assertEqual(external["edges"][0]["target"], "external-id")

    def test_validation_issue_bound_and_output_summary(self):
        huge = "x" * 10000
        data = {"records": [{"id": f"R{i}", "kind": "idea", "title": huge, "summary": huge, "path": f"p{i}"} for i in range(50)],
                "relations": [], "issues": [{"code": "bad", "message": huge, "id": str(i)} for i in range(150)]}
        with mock.patch.object(catalog.records, "catalog", return_value=data):
            result = catalog.query(self.project)
        self.assertTrue(result["truncated"])
        self.assertEqual((len(result["records"]), len(result["issues"]), result["issuesTotal"]), (50, 100, 150))
        self.assertLessEqual(catalog._encoded_size(result), catalog.MAX_OUTPUT_BYTES)
        self.assertEqual({item["id"] for item in result["records"]}, {f"R{i}" for i in range(50)})
        with self.assertRaises(ValueError):
            catalog.query(self.project, limit=101)
        with self.assertRaises(ValueError):
            catalog.query(self.project, text="x" * 257)

    def test_synthetic_thousand_record_cold_query_and_pages(self):
        self.render_synthetic()
        started = time.perf_counter()
        found = catalog.query(self.project, text="keyword-0777", limit=10)
        elapsed = time.perf_counter() - started
        self.assertEqual([item["id"] for item in found["records"]], ["S0777"])
        seen = []
        offset = 0
        while True:
            page = catalog.query(self.project, offset=offset, limit=100)
            seen.extend(item["id"] for item in page["records"])
            self.assertLessEqual(catalog._encoded_size(page), catalog.MAX_OUTPUT_BYTES)
            if not page["hasMore"]:
                break
            offset = page["nextOffset"]
        self.assertEqual(len(seen), 1000)
        self.assertEqual(len(set(seen)), 1000)
        self.assertLess(elapsed, 5.0, f"cold synthetic query took {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
