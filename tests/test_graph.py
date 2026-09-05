import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import donghe_graph as graph


REAL_BINARY = Path("/Users/aiden/.local/bin/codebase-memory-mcp")


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / "tiny-project"
        self.root.mkdir()
        (self.root / "app.py").write_text("def helper(value):\n    return value + 1\n\ndef caller():\n    return helper(4)\n")

    def tearDown(self):
        self.temp.cleanup()

    def fake_current(self, generation="1788608666637461000-1"):
        current = {"generation": generation, "fingerprint": graph.source_fingerprint(self.root),
                   "projectRoot": str(self.root.resolve()), "binaryVersion": graph.PINNED_VERSION,
                   "projectName": "fixture"}
        base = self.root / ".donghe/codegraph/generations" / generation
        (base / "cache").mkdir(parents=True)
        (base / "source").mkdir()
        database = base / "cache/fixture.db"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE projects (name TEXT PRIMARY KEY, indexed_at TEXT NOT NULL, root_path TEXT NOT NULL)")
            connection.execute("CREATE TABLE file_hashes (project TEXT, rel_path TEXT)")
            connection.execute("CREATE TABLE nodes (id INTEGER, project TEXT)")
            connection.execute("CREATE TABLE edges (id INTEGER, project TEXT)")
            connection.execute("INSERT INTO projects VALUES (?, ?, ?)", ("fixture", "now", str(base / "source")))
        current["databases"] = graph._database_inventory(base / "cache")
        graph._atomic_json(base / "generation.json", current)
        graph._atomic_json(self.root / ".donghe/codegraph/current.json", current)
        return current

    def test_fingerprint_tracks_content_deletion_and_excludes_generated_state(self):
        first = graph.source_fingerprint(self.root)
        (self.root / ".donghe/codegraph").mkdir(parents=True)
        (self.root / ".donghe/codegraph/noise").write_text("ignored")
        self.assertEqual(first, graph.source_fingerprint(self.root))
        (self.root / "app.py").unlink()
        self.assertNotEqual(first["sha256"], graph.source_fingerprint(self.root)["sha256"])
        self.assertEqual(first["coverage"], "policy-selected")

    def test_dart_is_policy_selected_and_secret_config_is_not(self):
        (self.root / "sample.dart").write_text("int helperDart() => 1;\n")
        (self.root / ".codebase-memory.json").write_text('{"extra_extensions":{".secret":"python"}}')
        files = {relative.as_posix() for relative, _path, _size in graph._source_files(self.root)[0]}
        self.assertIn("sample.dart", files)
        self.assertNotIn(".codebase-memory.json", files)

    def test_symlink_source_is_rejected(self):
        (self.root / "link.py").symlink_to(self.root / "app.py")
        with self.assertRaisesRegex(ValueError, "symlink"):
            graph.source_fingerprint(self.root)

    def test_failed_refresh_keeps_current_and_records_failure(self):
        current = self.fake_current()
        (self.root / "app.py").write_text((self.root / "app.py").read_text() + "# stale\n")
        with mock.patch.object(graph, "_binary", return_value=Path("/fake")), \
             mock.patch.object(graph, "_invoke", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                graph.refresh(self.root)
        self.assertEqual(json.loads((self.root / ".donghe/codegraph/current.json").read_text())["generation"], current["generation"])
        failures = list((self.root / ".donghe/codegraph/generations").glob("*/failure.json"))
        self.assertEqual(len(failures), 1)

    def test_refresh_rejects_source_drift_and_empty_upstream_cache(self):
        def mutate(_binary, _tool, _arguments, cache, _config, timeout=120):
            (self.root / "app.py").write_text("def changed():\n    pass\n")
            (cache / "fixture.db").write_bytes(b"sqlite")
            return {"project": "snapshot"}
        with mock.patch.object(graph, "_binary", return_value=Path("/fake")), mock.patch.object(graph, "_invoke", side_effect=mutate):
            with self.assertRaisesRegex(RuntimeError, "source changed"):
                graph.refresh(self.root)
        self.assertFalse((self.root / ".donghe/codegraph/current.json").exists())
        with mock.patch.object(graph, "_binary", return_value=Path("/fake")), \
             mock.patch.object(graph, "_invoke", return_value={"project": "snapshot"}):
            with self.assertRaisesRegex(RuntimeError, "no valid graph database"):
                graph.refresh(self.root)

    def test_upstream_exclusion_response_is_preserved_and_not_blindly_retried(self):
        calls = 0
        def excluded(_binary, _tool, arguments, cache, _config, timeout=120):
            nonlocal calls
            calls += 1
            self.assertEqual(arguments["mode"], "full")
            (cache / "fixture.db").write_bytes(b"nonempty")
            return {"project": "snapshot", "excluded": {"count": 1, "dirs": ["vendor"]}}
        with mock.patch.object(graph, "_binary", return_value=Path("/fake")), mock.patch.object(graph, "_invoke", side_effect=excluded):
            with self.assertRaisesRegex(RuntimeError, "excluded part"):
                graph.refresh(self.root)
            with self.assertRaisesRegex(RuntimeError, "already failed"):
                graph.refresh(self.root)
            with self.assertRaisesRegex(RuntimeError, "excluded part"):
                graph.refresh(self.root, retry=True)
        self.assertEqual(calls, 2)
        failure_path = next((self.root / ".donghe/codegraph/generations").glob("*/failure.json"))
        failure = json.loads(failure_path.read_text())
        self.assertEqual(failure["upstreamResponse"]["excluded"]["dirs"], ["vendor"])
        self.assertFalse((self.root / ".donghe/codegraph/current.json").exists())

    def test_retry_parameter_is_strict(self):
        with self.assertRaisesRegex(ValueError, "boolean"):
            graph.refresh(self.root, binary=REAL_BINARY, retry="yes")

    def test_missing_and_stale_search_are_explicit_bounded_fallbacks(self):
        missing = graph.search(self.root, "helper")
        self.assertEqual((missing["mode"], missing["graphState"]), ("fallback", "missing"))
        self.fake_current()
        (self.root / "app.py").write_text((self.root / "app.py").read_text() + "# changed\n")
        stale = graph.search(self.root, "helper")
        self.assertEqual((stale["mode"], stale["graphState"]), ("fallback", "stale"))
        self.assertNotIn("result", stale)

    @unittest.skipUnless(REAL_BINARY.is_file(), "0.8.1 test binary unavailable")
    def test_real_index_search_stale_refresh_and_worktree_isolation(self):
        home = Path(self.temp.name) / "sentinel-home"
        home.mkdir()
        before = sorted(p.relative_to(home).as_posix() for p in home.rglob("*"))
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            try:
                refreshed = graph.refresh(self.root, REAL_BINARY)
            except Exception:
                diagnostic = Path(__file__).parents[1] / "output/graph-failure-diagnostics"
                if diagnostic.exists():
                    shutil.rmtree(diagnostic)
                shutil.copytree(self.root / ".donghe/codegraph", diagnostic / "codegraph")
                (diagnostic / "context.json").write_text(json.dumps({
                    "root": str(self.root), "home": str(home), "cwd": os.getcwd(),
                    "environment": {key: os.environ.get(key) for key in ("CBM_CACHE_DIR", "XDG_CONFIG_HOME", "CBM_DISABLE_LSP_CROSS")},
                }, indent=2))
                raise
            self.assertTrue(refreshed["fresh"])
            found = graph.search(self.root, "helper", binary=REAL_BINARY)
            self.assertEqual(found["mode"], "graph")
            self.assertIn("helper", json.dumps(found["result"]).lower())
            self.assertNotIn("/.donghe/codegraph/generations/", json.dumps(found["result"]))
            self.assertGreater(found["verifiedSourceLocations"], 0)
            old_generation = refreshed["generation"]
            old_db = next((self.root / ".donghe/codegraph/generations" / old_generation / "cache").glob("*.db"))
            original = old_db.read_bytes()
            old_db.write_bytes(b"broken" + original[6:])
            self.assertEqual(graph.status(self.root)["state"], "stale")
            degraded = graph.search(self.root, "helper", binary=REAL_BINARY)
            self.assertEqual(degraded["mode"], "fallback")
            recovered = graph.refresh(self.root, REAL_BINARY)
            self.assertNotEqual(recovered["generation"], old_generation)
            self.assertTrue(old_db.exists(), "failed/corrupt generation remains as evidence")
            traced = graph.trace(self.root, "helper", direction="inbound", binary=REAL_BINARY)
            self.assertEqual(traced["mode"], "graph")
            self.assertIn("caller", json.dumps(traced["result"]).lower())
            reused = graph.refresh(self.root, REAL_BINARY)
            self.assertTrue(reused["reused"])
            self.assertEqual(reused["generation"], recovered["generation"])
            (self.root / "app.py").write_text((self.root / "app.py").read_text() + "\ndef newer():\n    return caller()\n")
            self.assertEqual(graph.status(self.root)["state"], "stale")
            graph.refresh(self.root, REAL_BINARY)
            newer = graph.search(self.root, "newer", binary=REAL_BINARY)
            self.assertEqual(newer["mode"], "graph")
            self.assertIn("newer", json.dumps(newer["result"]).lower())
            sibling = Path(self.temp.name).resolve() / "tiny-worktree"
            shutil.copytree(self.root, sibling, ignore=shutil.ignore_patterns(".donghe"))
            self.assertEqual(graph.status(sibling)["state"], "missing")
            graph.refresh(sibling, REAL_BINARY)
            self.assertNotEqual(graph.status(self.root)["generation"], graph.status(sibling)["generation"])
        self.assertEqual(before, sorted(p.relative_to(home).as_posix() for p in home.rglob("*")))

    def test_tampered_generation_and_state_symlink_are_rejected(self):
        current = self.fake_current()
        current["generation"] = "../../outside"
        graph._atomic_json(self.root / ".donghe/codegraph/current.json", current)
        with self.assertRaisesRegex(ValueError, "invalid current"):
            graph.status(self.root)
        shutil.rmtree(self.root / ".donghe")
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.root / ".donghe").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "state symlink"):
            graph.status(self.root)
        root_link = Path(self.temp.name).resolve() / "root-link"
        root_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "project path symlink"):
            graph.status(root_link)

    def test_nested_and_dangling_state_symlinks_are_rejected(self):
        current = self.fake_current()
        cache = self.root / ".donghe/codegraph/generations" / current["generation"] / "cache"
        shutil.rmtree(cache)
        cache.symlink_to(Path(self.temp.name).resolve() / "missing-cache", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "state symlink"):
            graph.status(self.root)

    def test_missing_binary_on_fresh_graph_degrades_instead_of_raising(self):
        self.fake_current()
        result = graph.search(self.root, "helper", binary=self.root / "missing")
        self.assertEqual((result["mode"], result["graphState"]), ("fallback", "query-failed"))

    def test_uninventoried_or_unbound_database_cannot_make_graph_fresh(self):
        current = self.fake_current()
        cache = self.root / ".donghe/codegraph/generations" / current["generation"] / "cache"
        (cache / "unrelated.db").write_bytes(b"not a graph database")
        self.assertEqual(graph.status(self.root)["state"], "stale")
        (cache / "unrelated.db").unlink()
        metadata_path = self.root / ".donghe/codegraph/current.json"
        metadata = json.loads(metadata_path.read_text())
        metadata.pop("databases")
        generation_path = self.root / ".donghe/codegraph/generations" / current["generation"] / "generation.json"
        graph._atomic_json(metadata_path, metadata)
        graph._atomic_json(generation_path, metadata)
        self.assertEqual(graph.status(self.root)["state"], "stale")

    def test_wal_is_checkpointed_before_database_inventory(self):
        cache = self.root / "cache"
        cache.mkdir()
        database = cache / "wal.db"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE projects (name TEXT PRIMARY KEY, indexed_at TEXT, root_path TEXT)")
            connection.execute("INSERT INTO projects VALUES ('fixture', 'now', '/snapshot')")
        graph._checkpoint_databases(cache)
        wal = Path(str(database) + "-wal")
        self.assertTrue(not wal.exists() or wal.stat().st_size == 0)
        self.assertEqual(sqlite3.connect(database).execute("SELECT name FROM projects").fetchone(), ("fixture",))

    def test_coverage_limit_and_secret_exclusion(self):
        (self.root / ".env").write_text("SECRET=helper")
        original = graph.MAX_SOURCE_FILE_BYTES
        graph.MAX_SOURCE_FILE_BYTES = 4
        try:
            self.assertEqual(graph.status(self.root)["state"], "unsupported")
        finally:
            graph.MAX_SOURCE_FILE_BYTES = original
        (self.root / "app.py").unlink()
        fallback = graph.search(self.root, "helper")
        self.assertEqual(fallback["results"], [])

    def test_fallback_reports_budget_and_file_truncation(self):
        original_bytes, original_files = graph.MAX_SOURCE_FILE_BYTES, graph.MAX_FALLBACK_FILES
        try:
            graph.MAX_SOURCE_FILE_BYTES = 4
            limited = graph.search(self.root, "helper")
            self.assertEqual((limited["searchable"], limited["truncated"]), (False, True))
            graph.MAX_SOURCE_FILE_BYTES = original_bytes
            graph.MAX_FALLBACK_FILES = 0
            sliced = graph.search(self.root, "helper")
            self.assertTrue(sliced["truncated"])
        finally:
            graph.MAX_SOURCE_FILE_BYTES, graph.MAX_FALLBACK_FILES = original_bytes, original_files


if __name__ == "__main__":
    unittest.main()
