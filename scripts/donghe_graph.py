#!/usr/bin/env python3
"""Project-local, generation-safe adapter for codebase-memory-mcp 0.8.1."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time


PINNED_VERSION = "0.8.1"
BUILD_POLICY = "source-extension-v2-full"
ISOLATED_CONFIG = b'{"extra_extensions":{}}\n'
STATE = Path(".donghe/codegraph")
DEFAULT_BINARY = Path(__file__).resolve().parents[1] / "runtime/codegraph/codebase-memory-mcp"
EXCLUDED_PARTS = {".git", ".hg", ".svn", ".worktrees", ".donghe", ".codebase-memory", ".idea", ".vs", ".vscode", ".eclipse", ".claude", ".cache", ".eggs", ".env", ".mypy_cache", ".nox", ".pytest_cache", ".ruff_cache", ".tox", ".venv", "__pycache__", "env", "htmlcov", "site-packages", "venv", ".npm", ".nyc_output", ".pnpm-store", ".yarn", "bower_components", "coverage", "node_modules", ".next", ".nuxt", ".svelte-kit", ".angular", ".turbo", ".parcel-cache", ".docusaurus", ".expo", "dist", "obj", "Pods", "target", "temp", "tmp", ".terraform", ".serverless", "bazel-bin", "bazel-out", "bazel-testlogs", ".cargo", ".stack-work", ".dart_tool", "zig-cache", "zig-out", ".metals", ".bloop", ".bsp", ".ccls-cache", ".clangd", "elm-stuff", "_opam", ".cpcache", ".shadow-cljs", ".vercel", ".netlify", ".qdrant_code_embeddings", ".tmp", "vendor", "vendored", "build", "out"}
EXCLUDED_PREFIXES = (("docs", "东合"),)
MAX_FALLBACK_FILES = 5000
MAX_FALLBACK_BYTES = 2 * 1024 * 1024
MAX_SOURCE_FILES = 20000
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024
SOURCE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".go", ".rs", ".py", ".pyi", ".js", ".jsx",
    ".ts", ".tsx", ".java", ".kt", ".kts", ".swift", ".rb", ".php", ".scala", ".cs", ".lua",
    ".sh", ".bash", ".zsh", ".sql", ".proto", ".graphql", ".vue", ".svelte", ".html", ".css",
    ".scss", ".yaml", ".yml", ".toml", ".json", ".json5", ".xml", ".md", ".rst", ".tex", ".dart",
}
SOURCE_NAMES = {"Dockerfile", "Makefile", "CMakeLists.txt", "go.mod", "go.sum", "Cargo.toml", "package.json"}
SECRET_NAMES = {".env", ".env.local", ".env.production", ".npmrc", ".pypirc", "credentials", "credentials.json", ".codebase-memory.json"}
SECRET_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
UPSTREAM_IGNORED_NAMES = {"package.json", "package-lock.json", "tsconfig.json", "jsconfig.json", "composer.json", "composer.lock", "yarn.lock", "openapi.json", "swagger.json", "jest.config.json", ".eslintrc.json", ".prettierrc.json", ".babelrc.json", "tslint.json", "angular.json", "firebase.json", "renovate.json", "lerna.json", "turbo.json", ".stylelintrc.json", "pnpm-lock.json", "deno.json", "biome.json", "devcontainer.json", ".devcontainer.json", "launch.json", "settings.json", "extensions.json", "tasks.json"}
GENERATION = re.compile(r"[0-9]{16,22}-[0-9]{1,12}\Z")


class CoverageError(ValueError):
    pass


def _root(project) -> Path:
    value = project if isinstance(project, (str, os.PathLike)) else getattr(project, "root")
    lexical = Path(value).absolute()
    cursor = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(f"project path symlink is not allowed: {cursor}")
    root = lexical.resolve()
    if not root.is_dir():
        raise ValueError(f"project is not a directory: {root}")
    return root


def _state(project) -> Path:
    root = _root(project)
    cursor = root
    for part in STATE.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"code graph state symlink is not allowed: {cursor}")
    return root / STATE


def _safe_state_path(project, relative: Path) -> Path:
    root, state = _root(project), _state(project)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("invalid code graph state path")
    cursor = state
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(f"code graph state symlink is not allowed: {cursor}")
    if not cursor.resolve(strict=False).is_relative_to(root):
        raise ValueError("code graph state path escapes project")
    return cursor


def _excluded(parts) -> bool:
    return any(part in EXCLUDED_PARTS for part in parts) or any(tuple(parts[:len(prefix)]) == prefix for prefix in EXCLUDED_PREFIXES)


def _is_source(path: Path) -> bool:
    lower = path.name.lower()
    if lower in SECRET_NAMES or path.name in UPSTREAM_IGNORED_NAMES or path.suffix.lower() in SECRET_EXTENSIONS or lower.startswith(".env."):
        return False
    return path.name in SOURCE_NAMES or path.suffix.lower() in SOURCE_EXTENSIONS


def _source_files(project):
    root, selected, total = _root(project), [], 0
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        base_path, rel_base = Path(base), Path(base).relative_to(root)
        kept = []
        for name in sorted(dirs):
            path, parts = base_path / name, (*rel_base.parts, name)
            if _excluded(parts):
                continue
            if path.is_symlink():
                raise ValueError(f"source symlink is not allowed: {path.relative_to(root)}")
            kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            path, relative = base_path / name, (base_path / name).relative_to(root)
            if _excluded(relative.parts) or not _is_source(path):
                continue
            if path.is_symlink():
                raise ValueError(f"source symlink is not allowed: {relative}")
            size = path.stat().st_size
            if size > MAX_SOURCE_FILE_BYTES:
                raise CoverageError(f"source file exceeds {MAX_SOURCE_FILE_BYTES} byte coverage budget: {relative}")
            total += size
            selected.append((relative, path, size))
            if len(selected) > MAX_SOURCE_FILES or total > MAX_SOURCE_BYTES:
                raise CoverageError("source tree exceeds code graph coverage budget")
    return selected, total


def source_fingerprint(project) -> dict:
    """Hash source bytes and relative names, including uncommitted changes/deletions."""
    root = _root(project)
    digest = hashlib.sha256()
    files, total = _source_files(root)
    for relative, path, _size in files:
        digest.update(relative.as_posix().encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return {"sha256": digest.hexdigest(), "files": len(files), "bytes": total,
            "coverage": "policy-selected", "policy": "source-extension-v1"}


def _read_current(project):
    root, path = _root(project), _safe_state_path(project, Path("current.json"))
    try:
        if path.is_symlink():
            raise ValueError("current graph metadata must not be a symlink")
        value = json.loads(path.read_text())
        if (not isinstance(value, dict) or not GENERATION.fullmatch(str(value.get("generation", "")))
                or not value.get("fingerprint", {}).get("sha256") or value.get("projectRoot") != str(root)
                or value.get("binaryVersion") != PINNED_VERSION):
            raise ValueError("invalid current graph metadata")
        return value
    except FileNotFoundError:
        return None


def status(project) -> dict:
    root = _root(project)
    current = _read_current(root)
    try:
        fingerprint = source_fingerprint(root)
    except CoverageError as error:
        return {"state": "unsupported", "fresh": False, "project": str(root), "coverage": "incomplete", "error": str(error)}
    if current is None:
        return {"state": "missing", "fresh": False, "project": str(root), "fingerprint": fingerprint}
    generation_root = _safe_state_path(root, Path("generations") / current["generation"])
    cache = _safe_state_path(root, Path("generations") / current["generation"] / "cache")
    generation_metadata = _safe_state_path(root, Path("generations") / current["generation"] / "generation.json")
    metadata_valid = False
    try:
        metadata_valid = json.loads(generation_metadata.read_text()) == current
    except (OSError, json.JSONDecodeError):
        pass
    database_valid = _validate_database_inventory(cache, current, generation_root / "source")
    fresh = (current["fingerprint"]["sha256"] == fingerprint["sha256"] and metadata_valid and database_valid)
    return {"state": "current" if fresh else "stale", "fresh": fresh, "project": str(root),
            "fingerprint": fingerprint, "indexed": current["fingerprint"], "generation": current["generation"],
            "indexedAt": current.get("indexedAt"), "projectName": current.get("projectName"),
            "diagnostic": current.get("diagnostic")}


def _binary(binary=None) -> Path:
    path = Path(binary).resolve() if binary else DEFAULT_BINARY
    if not path.is_file() or not os.access(path, os.X_OK):
        raise FileNotFoundError(f"bundled code graph binary is unavailable: {path}")
    check = subprocess.run([str(path), "--version"], text=True, capture_output=True, timeout=10)
    if check.returncode or check.stdout.strip() != f"codebase-memory-mcp {PINNED_VERSION}":
        raise RuntimeError(f"code graph binary must be {PINNED_VERSION}: {(check.stdout + check.stderr).strip()}")
    if binary is None:
        manifest_path = path.parent / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text())
            expected = manifest["sha256"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"bundled code graph manifest is invalid: {manifest_path}") from error
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise RuntimeError("bundled code graph manifest sha256 is invalid")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected or manifest.get("version") != PINNED_VERSION:
            raise RuntimeError("bundled code graph binary does not match its pinned manifest")
    return path


def _environment(cache: Path, config: Path) -> dict:
    env = os.environ.copy()
    env.update({"CBM_CACHE_DIR": str(cache), "XDG_CONFIG_HOME": str(config), "CBM_DISABLE_LSP_CROSS": "1"})
    return env


def _invoke(binary: Path, tool: str, arguments: dict, cache: Path, config: Path, timeout=120) -> dict:
    result = subprocess.run([str(binary), "cli", tool, json.dumps(arguments, separators=(",", ":"))],
                            text=True, capture_output=True, env=_environment(cache, config), timeout=timeout,
                            cwd=cache.parent)
    if result.returncode:
        raise RuntimeError(f"{tool} failed ({result.returncode}): {(result.stderr or result.stdout).strip()[-2000:]}")
    text = result.stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".current-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _cache_databases(cache: Path):
    if not cache.is_dir() or cache.is_symlink():
        return []
    return [path for path in cache.glob("*.db")
            if path.is_file() and not path.is_symlink() and path.stat().st_size > 0]


def _file_sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_inventory(cache: Path):
    return [{"path": path.relative_to(cache).as_posix(), "size": path.stat().st_size, "sha256": _file_sha256(path)}
            for path in sorted(_cache_databases(cache))]


def _checkpoint_databases(cache: Path):
    """Make an upstream WAL database stable before hashing its main file."""
    for path in _cache_databases(cache):
        try:
            with sqlite3.connect(path, timeout=5) as connection:
                result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if result is None or result[0] != 0:
                    raise RuntimeError(f"SQLite WAL checkpoint remained busy for {path.name}: {result}")
        except sqlite3.DatabaseError as error:
            raise RuntimeError(f"SQLite WAL checkpoint failed for {path.name}: {error}") from error


def _database_validation(cache: Path, metadata: dict, snapshot: Path):
    detail = {"ok": False, "stage": "metadata"}
    expected = metadata.get("databases")
    if not isinstance(expected, list) or not expected:
        return detail
    try:
        actual = _database_inventory(cache)
        if actual != expected:
            return {**detail, "stage": "inventory", "expected": expected, "actual": actual}
        for item in actual:
            path = cache / item["path"]
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                quick_check = connection.execute("PRAGMA quick_check").fetchone()
                if quick_check != ("ok",):
                    return {**detail, "stage": "quick_check", "database": item["path"], "result": quick_check}
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"projects", "file_hashes", "nodes", "edges"}.issubset(tables):
                    return {**detail, "stage": "schema", "database": item["path"], "tables": sorted(tables)}
                columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
                if not {"name", "root_path"}.issubset(columns):
                    return {**detail, "stage": "project_schema", "database": item["path"], "columns": sorted(columns)}
                projects = connection.execute("SELECT name, root_path FROM projects").fetchall()
                if projects != [(metadata.get("projectName"), str(snapshot))]:
                    return {**detail, "stage": "project_binding", "database": item["path"],
                            "expected": [[metadata.get("projectName"), str(snapshot)]],
                            "actual": [list(row) for row in projects]}
        return {"ok": True, "stage": "complete", "databases": len(actual)}
    except (OSError, ValueError, KeyError, sqlite3.DatabaseError) as error:
        return {**detail, "stage": "exception", "errorType": type(error).__name__, "error": str(error)}


def _validate_database_inventory(cache: Path, metadata: dict, snapshot: Path):
    return _database_validation(cache, metadata, snapshot)["ok"]


def _copy_snapshot(project, destination: Path):
    root = _root(project)
    destination.mkdir()
    for relative, source, _size in _source_files(root)[0]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _record_intrinsic_database_changes(project, metadata: dict):
    """Accept only validated writes made by a successful upstream query process."""
    root = _root(project)
    generation = metadata["generation"]
    generation_root = _safe_state_path(root, Path("generations") / generation)
    cache = _safe_state_path(root, Path("generations") / generation / "cache")
    old_paths = [item.get("path") for item in metadata.get("databases", [])]
    _checkpoint_databases(cache)
    updated = dict(metadata)
    updated["databases"] = _database_inventory(cache)
    if [item.get("path") for item in updated["databases"]] != old_paths:
        raise RuntimeError("successful graph query changed the database file set")
    if source_fingerprint(root) != metadata["fingerprint"]:
        raise RuntimeError("source changed while graph query metadata was refreshed")
    validation = _database_validation(cache, updated, generation_root / "source")
    if not validation["ok"]:
        raise RuntimeError("successful graph query left an invalid database: "
                           + json.dumps(validation, ensure_ascii=False, sort_keys=True))
    current = _read_current(root)
    if current != metadata:
        raise RuntimeError("current graph generation changed during query")
    _atomic_json(_safe_state_path(root, Path("generations") / generation / "generation.json"), updated)
    _atomic_json(_safe_state_path(root, Path("current.json")), updated)
    return updated


def refresh(project, binary=None, retry=False) -> dict:
    if not isinstance(retry, bool):
        raise ValueError("retry must be a boolean")
    root = _root(project)
    graph_root = _state(root)
    executable = _binary(binary)
    existing = status(root)
    if existing["fresh"]:
        return {"state": "current", "fresh": True, "reused": True, "generation": existing["generation"],
                "fingerprint": existing["fingerprint"], "projectName": existing.get("projectName")}
    fingerprint = source_fingerprint(root)
    for failure_path in sorted((graph_root / "generations").glob("*/failure.json"), reverse=True):
        try:
            failure = json.loads(failure_path.read_text())
            if not retry and failure.get("fingerprint") == fingerprint and failure.get("buildPolicy") == BUILD_POLICY:
                raise RuntimeError("unchanged source already failed this code graph build policy: " + failure.get("error", "unknown failure"))
        except (OSError, json.JSONDecodeError):
            continue
    generation = f"{time.time_ns()}-{os.getpid()}"
    generation_root = _safe_state_path(root, Path("generations") / generation)
    cache = _safe_state_path(root, Path("generations") / generation / "cache")
    config = _safe_state_path(root, Path("generations") / generation / "config")
    snapshot = _safe_state_path(root, Path("generations") / generation / "source")
    cache.mkdir(parents=True)
    config.mkdir()
    isolated_config = config / "codebase-memory-mcp/config.json"
    isolated_config.parent.mkdir()
    isolated_config.write_bytes(ISOLATED_CONFIG)
    response = None
    try:
        _copy_snapshot(root, snapshot)
        if source_fingerprint(snapshot) != fingerprint:
            raise RuntimeError("managed source snapshot does not match the requested source generation")
        response = _invoke(executable, "index_repository", {"repo_path": str(snapshot), "mode": "full", "persistence": False}, cache, config, timeout=300)
        # A zero CLI exit is the upstream success contract; retain its response for audit.
        upstream_name = response.get("project") if isinstance(response, dict) else None
        if not isinstance(upstream_name, str) or not upstream_name:
            raise RuntimeError("index_repository returned no project identity")
        after = source_fingerprint(root)
        if after != fingerprint:
            raise RuntimeError("source changed while the code graph generation was built")
        if not _cache_databases(cache):
            raise RuntimeError("index_repository produced no valid graph database")
        excluded = response.get("excluded", {}) if isinstance(response, dict) else {}
        if excluded.get("count", 0):
            raise RuntimeError("index_repository excluded part of the managed source snapshot")
        _checkpoint_databases(cache)
        metadata = {"generation": generation, "fingerprint": fingerprint, "indexedAt": int(time.time()),
                    "projectRoot": str(root), "buildMode": "full-generation",
                    "buildPolicy": BUILD_POLICY, "parseCoverage": "upstream-supported-within-policy",
                    "configPolicy": "isolated-empty", "configSha256": hashlib.sha256(ISOLATED_CONFIG).hexdigest(),
                    "binaryVersion": PINNED_VERSION, "projectName": upstream_name,
                    "databases": _database_inventory(cache), "diagnostic": response}
        database_validation = _database_validation(cache, metadata, snapshot)
        if not database_validation["ok"]:
            raise RuntimeError("index_repository database failed integrity or project-binding validation: "
                               + json.dumps(database_validation, ensure_ascii=False, sort_keys=True))
        _atomic_json(_safe_state_path(root, Path("generations") / generation / "generation.json"), metadata)
        _atomic_json(_safe_state_path(root, Path("current.json")), metadata)
        return {"state": "current", "fresh": True, **metadata}
    except Exception as error:
        failure = {"generation": generation, "failedAt": int(time.time()), "error": str(error),
                   "fingerprint": fingerprint, "buildPolicy": BUILD_POLICY, "upstreamResponse": response}
        if "metadata" in locals():
            failure["databaseValidation"] = _database_validation(cache, metadata, snapshot)
        _atomic_json(_safe_state_path(root, Path("generations") / generation / "failure.json"), failure)
        raise


def _fallback(project, query: str, limit: int) -> dict:
    root = _root(project)
    if not query or len(query) > 256:
        raise ValueError("query must contain 1..256 characters")
    needle = query.casefold()
    results = []
    try:
        all_files = _source_files(root)[0]
    except CoverageError as error:
        return {"mode": "fallback", "reason": str(error), "query": query, "results": [],
                "truncated": True, "searchable": False}
    files, file_limited = all_files[:MAX_FALLBACK_FILES], len(all_files) > MAX_FALLBACK_FILES
    for relative, path, size in files:
        try:
            if size > MAX_FALLBACK_BYTES:
                continue
            for number, line in enumerate(path.read_text(errors="strict").splitlines(), 1):
                if needle in line.casefold():
                    results.append({"file": relative.as_posix(), "line": number, "text": line[:500]})
                    if len(results) >= limit:
                        return {"mode": "fallback", "reason": "graph unavailable or stale", "query": query, "results": results, "truncated": True}
        except (OSError, UnicodeDecodeError):
            continue
    return {"mode": "fallback", "reason": "graph unavailable or stale", "query": query,
            "results": results, "truncated": file_limited, "searchable": True}


def _remap_result(value, snapshot: Path, root: Path):
    snapshot_text, verified = str(snapshot), 0
    source_relatives = {relative.as_posix() for relative, _path, _size in _source_files(root)[0]}

    def visit(item, key=None):
        nonlocal verified
        if isinstance(item, dict):
            return {name: visit(child, name) for name, child in item.items()}
        if isinstance(item, list):
            return [visit(child, key) for child in item]
        if isinstance(item, str):
            mapped = item.replace(snapshot_text, str(root))
            if key in {"file", "file_path", "path"}:
                candidate = Path(mapped)
                relative = candidate.relative_to(root).as_posix() if candidate.is_absolute() and candidate.is_relative_to(root) else mapped
                if relative in source_relatives and (root / relative).is_file():
                    verified += 1
                elif snapshot_text in item or not candidate.is_absolute():
                    raise RuntimeError(f"graph result source is not readable in the project: {item}")
            return mapped
        return item

    return visit(value), verified


def search(project, query, limit=20, binary=None) -> dict:
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    graph_status = status(project)
    if not graph_status["fresh"]:
        result = _fallback(project, query, limit)
        result["graphState"] = graph_status["state"]
        return result
    root = _root(project)
    try:
        executable = _binary(binary)
        current_metadata = _read_current(root)
        generation_root = _safe_state_path(root, Path("generations") / graph_status["generation"])
        response = _invoke(executable, "search_graph", {"project": graph_status.get("projectName", root.name), "query": query, "limit": limit},
                           _safe_state_path(root, Path("generations") / graph_status["generation"] / "cache"),
                           _safe_state_path(root, Path("generations") / graph_status["generation"] / "config"))
        _record_intrinsic_database_changes(root, current_metadata)
        mapped, verified = _remap_result(response, generation_root / "source", root)
        return {"mode": "graph", "graphState": "current", "query": query, "result": mapped,
                "sourceLocation": "static-index-position", "verifiedSourceLocations": verified}
    except Exception as error:
        result = _fallback(root, query, limit)
        result.update({"graphState": "query-failed", "graphError": str(error)})
        return result


def trace(project, function_name, direction="both", depth=3, binary=None) -> dict:
    if not isinstance(function_name, str) or not function_name or len(function_name) > 256:
        raise ValueError("function_name must contain 1..256 characters")
    if direction not in {"inbound", "outbound", "both"}:
        raise ValueError("direction must be inbound, outbound, or both")
    if not isinstance(depth, int) or not 1 <= depth <= 5:
        raise ValueError("depth must be between 1 and 5")
    graph_status = status(project)
    if not graph_status["fresh"]:
        return {"mode": "unavailable", "graphState": graph_status["state"], "function": function_name,
                "reason": "call tracing requires a current graph"}
    try:
        root, executable = _root(project), _binary(binary)
        current_metadata = _read_current(root)
        generation_root = _safe_state_path(root, Path("generations") / graph_status["generation"])
        response = _invoke(executable, "trace_path", {"project": graph_status["projectName"],
                           "function_name": function_name, "direction": direction, "depth": depth,
                           "mode": "calls", "include_tests": False},
                           _safe_state_path(root, Path("generations") / graph_status["generation"] / "cache"),
                           _safe_state_path(root, Path("generations") / graph_status["generation"] / "config"), timeout=60)
        _record_intrinsic_database_changes(root, current_metadata)
        mapped, verified = _remap_result(response, generation_root / "source", root)
        encoded = json.dumps(mapped, ensure_ascii=False)
        if len(encoded) > 200000:
            raise RuntimeError("trace response exceeded the adapter budget")
        return {"mode": "graph", "graphState": "current", "function": function_name, "result": mapped,
                "sourceLocation": "static-index-position", "verifiedSourceLocations": verified}
    except Exception as error:
        return {"mode": "unavailable", "graphState": "query-failed", "function": function_name, "reason": str(error)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "refresh", "search", "trace"))
    parser.add_argument("project")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--direction", choices=("inbound", "outbound", "both"), default="both")
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--binary")
    parser.add_argument("--retry", action="store_true", help="explicitly retry an unchanged failed refresh")
    args = parser.parse_args(argv)
    if args.command == "status":
        value = status(args.project)
    elif args.command == "refresh":
        value = refresh(args.project, args.binary, retry=args.retry)
    elif args.command == "search":
        if args.query is None:
            parser.error("search requires query")
        value = search(args.project, args.query, args.limit, args.binary)
    else:
        if args.query is None:
            parser.error("trace requires function name")
        value = trace(args.project, args.query, args.direction, args.depth, args.binary)
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
