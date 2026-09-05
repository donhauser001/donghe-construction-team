#!/usr/bin/env python3
"""Cold, byte-preserving monthly storage for Donghe project records."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


DOC = "docs/东合"
ARCHIVE = DOC + "/档案"
STATE = ".donghe/state/archive"
JSON_FENCE = re.compile(r"```donghe-json\n(.*?)\n```", re.S)
META_FENCE = re.compile(r"```donghe-meta\n(.*?)\n```", re.S)
MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z")
DAY = re.compile(r"(\d{4}-\d{2})-\d{2}\.md\Z")


def _hash(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("invalid relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or str(path) == ".":
        raise ValueError("path must be inside the project")
    return path


def _safe(project, relative, archive_only=False):
    relative = _relative(relative)
    root = Path(project.root).resolve()
    target = root / relative
    for item in [target, *target.parents]:
        if item == root:
            break
        if item.is_symlink():
            raise ValueError("symlinks are not allowed: " + relative.as_posix())
    resolved = target.resolve()
    boundary = (root / ARCHIVE).resolve() if archive_only else root
    if not resolved.is_relative_to(boundary):
        raise ValueError("archive path escapes its boundary" if archive_only else "external path")
    return target


def _archive_matches(project, relative):
    base = _safe(project, ARCHIVE, archive_only=True)
    if not base.exists():
        return []
    result = []
    for month in sorted(base.iterdir()):
        if month.is_symlink():
            raise ValueError("symlinks are not allowed in archive")
        if month.is_dir() and MONTH.fullmatch(month.name):
            candidate = _safe(project, f"{ARCHIVE}/{month.name}/{relative}", archive_only=True)
            if candidate.exists():
                result.append(candidate)
    return result


def resolve(project, relative):
    """Resolve a logical path from live or cold storage without Project.path recursion."""
    logical = _relative(relative).as_posix()
    live = _safe(project, logical)
    # Physical archive and internal state paths are not logical Donghe records.
    if logical == ARCHIVE or logical.startswith(ARCHIVE + "/") or not (logical == DOC or logical.startswith(DOC + "/")):
        return live
    matches = _archive_matches(project, logical)
    if live.is_dir() and all(match.is_dir() for match in matches):
        return live
    if live.exists() and matches:
        raise ValueError("logical path exists in live storage and archive: " + logical)
    if len(matches) > 1:
        raise ValueError("logical path has multiple archived copies: " + logical)
    return live if live.exists() or not matches else matches[0]


def logical_files(project, prefix):
    """List logical file paths below prefix across live and cold storage."""
    prefix = _relative(prefix).as_posix().rstrip("/")
    if not (prefix == DOC or prefix.startswith(DOC + "/")) or prefix == ARCHIVE or prefix.startswith(ARCHIVE + "/"):
        raise ValueError("logical scan must stay in docs/东合 outside 档案")
    found = {}

    def collect(base, logical_base, live_scan=False):
        if not base.exists():
            return
        paths = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in paths:
            if path.is_symlink():
                raise ValueError("symlinks are not allowed in logical records")
            if not path.is_file():
                continue
            # A live-tree scan can cross the physical cold-store directory when
            # prefix is docs/东合. Those files are collected below under their
            # original logical names instead.
            if live_scan and path.resolve().is_relative_to((Path(project.root) / ARCHIVE).resolve()):
                continue
            logical = logical_base if base.is_file() else f"{logical_base}/{path.relative_to(base).as_posix()}"
            if logical in found:
                raise ValueError("logical path has multiple physical copies: " + logical)
            found[logical] = path

    collect(_safe(project, prefix), prefix, live_scan=True)
    archive = _safe(project, ARCHIVE, archive_only=True)
    if archive.exists():
        for month in sorted(archive.iterdir()):
            if month.is_symlink():
                raise ValueError("symlinks are not allowed in archive")
            if not month.is_dir() or not MONTH.fullmatch(month.name):
                continue
            collect(_safe(project, f"{ARCHIVE}/{month.name}/{prefix}", archive_only=True), prefix)
    return sorted(found)


def _json_fence(path, pattern):
    try:
        blocks = pattern.findall(path.read_text())
        return json.loads(blocks[0]) if len(blocks) == 1 else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None


def _month(value):
    if not isinstance(value, str):
        return None
    match = re.match(r"(\d{4}-(?:0[1-9]|1[0-2]))(?:-|T)", value)
    return match.group(1) if match else None


def _candidate_month(relative, path):
    if relative.startswith(DOC + "/资料/") and relative.endswith(".md"):
        meta = _json_fence(path, META_FENCE)
        if isinstance(meta, dict) and meta.get("state") == "closed":
            return _month(meta.get("updatedAt"))
    if relative.startswith(DOC + "/任务卡/") and relative.endswith(".md"):
        task = _json_fence(path, JSON_FENCE)
        if isinstance(task, dict) and task.get("status") == "completed":
            completed = [_month(e.get("at")) for e in task.get("events", [])
                         if isinstance(e, dict) and e.get("kind") == "completed"]
            return next((value for value in reversed(completed) if value), None)
    if relative.startswith(DOC + "/开发日志/"):
        match = DAY.fullmatch(Path(relative).name)
        return match.group(1) if match else None
    return None


def _validate_requested_month(month, current):
    if month is not None and (not isinstance(month, str) or not MONTH.fullmatch(month)):
        raise ValueError("month must be YYYY-MM")
    if month is not None and month >= current:
        raise ValueError("archive month must be earlier than the current month")


def plan(project, month=None):
    """Build a read-only archive plan for closed records older than this month."""
    current = dt.date.today().strftime("%Y-%m")
    _validate_requested_month(month, current)
    candidates, issues = [], []
    tasks = {}
    for relative in logical_files(project, DOC):
        path = resolve(project, relative)
        # Already archived logical records are part of cold reads, not new moves.
        if path.relative_to(Path(project.root)).as_posix().startswith(ARCHIVE + "/"):
            continue
        record_month = _candidate_month(relative, path)
        if not record_month or record_month >= current or (month is not None and record_month != month):
            continue
        tasks[relative] = record_month
    for relative, record_month in sorted(tasks.items()):
        candidates.append({"source": relative, "target": f"{ARCHIVE}/{record_month}/{relative}", "sha256": _hash(_safe(project, relative))})
        if relative.startswith(DOC + "/任务卡/"):
            task_id = Path(relative).stem
            receipt_prefix = DOC + "/证据/" + task_id + "--"
            for receipt in logical_files(project, DOC + "/证据"):
                if receipt.startswith(receipt_prefix) and "/指纹/" not in receipt:
                    receipt_path = resolve(project, receipt)
                    if receipt_path.relative_to(Path(project.root)).as_posix().startswith(ARCHIVE + "/"):
                        continue
                    candidates.append({"source": receipt, "target": f"{ARCHIVE}/{record_month}/{receipt}", "sha256": _hash(receipt_path)})
    # A malformed closed-looking file is left live and reported, never guessed.
    for prefix, pattern in [(DOC + "/资料", META_FENCE), (DOC + "/任务卡", JSON_FENCE)]:
        for relative in logical_files(project, prefix):
            path = resolve(project, relative)
            if path.relative_to(Path(project.root)).as_posix().startswith(ARCHIVE + "/"):
                continue
            text = path.read_text(errors="replace")
            if "```donghe-" in text and _json_fence(path, pattern) is None:
                issues.append("invalid metadata fence: " + relative)
    candidates.sort(key=lambda item: item["source"])
    sources = [item["source"] for item in candidates]
    if len(sources) != len(set(sources)):
        raise ValueError("archive plan contains duplicate source paths")
    return {"month": month, "candidates": candidates, "count": len(candidates), "issues": sorted(set(issues)),
            "due": bool(candidates)}


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".donghe-archive-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _operation(project, kind, month, candidates):
    canonical = json.dumps({"kind": kind, "month": month, "candidates": candidates}, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")).encode()
    op_id = hashlib.sha256(canonical).hexdigest()
    path = _safe(project, f"{STATE}/{kind}-{op_id}.json")
    if path.exists():
        op = json.loads(path.read_text())
        if op.get("candidates") != candidates or op.get("kind") != kind:
            raise ValueError("archive operation identity conflict")
    else:
        op = {"id": op_id, "kind": kind, "month": month, "state": "prepared", "candidates": candidates, "moved": []}
        _write_json(path, op)
    return path, op


def _validate_operation(project, op_path, op):
    if not isinstance(op, dict) or op.get("kind") not in {"archive", "restore"} or op.get("state") not in {"prepared", "committed"}:
        raise ValueError("invalid archive operation record: " + op_path.name)
    kind, month, candidates = op["kind"], op.get("month"), op.get("candidates")
    current = dt.date.today().strftime("%Y-%m")
    if month is not None:
        _validate_requested_month(month, current)
    if not isinstance(candidates, list) or not isinstance(op.get("moved"), list):
        raise ValueError("invalid archive operation record: " + op_path.name)
    canonical = json.dumps({"kind": kind, "month": month, "candidates": candidates}, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")).encode()
    expected_id = hashlib.sha256(canonical).hexdigest()
    if op.get("id") != expected_id or op_path.name != f"{kind}-{expected_id}.json":
        raise ValueError("archive operation identity mismatch: " + op_path.name)
    sources = []
    for item in candidates:
        if not isinstance(item, dict) or set(item) != {"source", "target", "sha256"} or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")):
            raise ValueError("invalid archive operation candidate")
        source, target = _relative(item["source"]).as_posix(), _relative(item["target"]).as_posix()
        physical, logical = (target, source) if kind == "archive" else (source, target)
        if not logical.startswith(DOC + "/") or logical.startswith(ARCHIVE + "/"):
            raise ValueError("archive operation escapes governed documents")
        suffix = physical[len(ARCHIVE) + 1:].split("/", 1) if physical.startswith(ARCHIVE + "/") else []
        if len(suffix) != 2 or not MONTH.fullmatch(suffix[0]) or suffix[0] >= current or suffix[1] != logical:
            raise ValueError("archive operation path mapping is invalid")
        if month is not None and suffix[0] != month:
            raise ValueError("archive operation month mismatch")
        sources.append(source)
    if len(sources) != len(set(sources)) or any(value not in sources for value in op["moved"]):
        raise ValueError("archive operation has duplicate or unknown moved paths")


def _move(project, op_path, op):
    _validate_operation(project, op_path, op)
    moved = set(op.get("moved", []))
    for index, item in enumerate(op["candidates"], 1):
        source, target = _safe(project, item["source"]), _safe(project, item["target"], archive_only=op["kind"] == "archive")
        if item["source"] in moved:
            if source.exists() or not target.is_file() or _hash(target) != item["sha256"]:
                raise ValueError("recorded move no longer matches storage: " + item["source"])
            continue
        if source.exists() and target.exists():
            raise ValueError("source and target both exist: " + item["source"])
        if target.exists():
            # Accepted only as recovery of this already-prepared operation.
            if source.exists() or not target.is_file() or _hash(target) != item["sha256"]:
                raise ValueError("archive target conflict: " + item["target"])
        else:
            if not source.is_file() or _hash(source) != item["sha256"]:
                raise ValueError("archive source missing or changed: " + item["source"])
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
        if _hash(target) != item["sha256"]:
            raise ValueError("archive hash changed during move: " + item["source"])
        op["moved"].append(item["source"])
        _write_json(op_path, op)
        if os.environ.get("DONGHE_TEST_FAIL_AFTER_ARCHIVE_MOVE") == str(index):
            raise RuntimeError("injected archive interruption")
    op["state"] = "committed"
    op["committedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _write_json(op_path, op)
    result = {"operationId": op["id"], "state": op["state"], "month": op["month"], "count": len(op["candidates"])}
    if op["kind"] == "restore":
        recorded = _archive_provenance(project, op["month"])
        unverified = [item["target"] for item in op["candidates"] if recorded.get(item["source"]) != item["sha256"]]
        result.update(integrity="archive-journal" if not unverified else "transport-only", unverified=unverified)
    return result


def _pending(project, kind, month=None):
    state = _safe(project, STATE)
    pending = []
    if state.exists():
        for path in sorted(state.glob(kind + "-*.json")):
            try:
                op = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                raise ValueError("invalid archive operation record: " + path.name)
            _validate_operation(project, path, op)
            if op.get("kind") == kind and op.get("state") == "prepared" and (month is None or op.get("month") == month):
                pending.append((path, op))
    if len(pending) > 1:
        raise ValueError("multiple pending archive operations require review")
    return pending[0] if pending else None


def _archive_provenance(project, month):
    recorded = {}
    state = _safe(project, STATE)
    if not state.exists():
        return recorded
    for op_path in sorted(state.glob("archive-*.json")):
        try:
            archived = json.loads(op_path.read_text())
        except (OSError, json.JSONDecodeError):
            raise ValueError("invalid archive operation record: " + op_path.name)
        _validate_operation(project, op_path, archived)
        if archived.get("state") == "committed":
            for item in archived["candidates"]:
                if item["target"].startswith(f"{ARCHIVE}/{month}/"):
                    if item["target"] in recorded and recorded[item["target"]] != item["sha256"]:
                        raise ValueError("conflicting archive provenance: " + item["target"])
                    recorded[item["target"]] = item["sha256"]
    return recorded


def apply(project, archive_plan):
    if not isinstance(archive_plan, dict) or archive_plan.get("issues"):
        raise ValueError("cannot apply an invalid archive plan")
    candidates = archive_plan.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("invalid archive plan")
    current = dt.date.today().strftime("%Y-%m")
    _validate_requested_month(archive_plan.get("month"), current)
    for item in candidates:
        if not isinstance(item, dict) or set(item) != {"source", "target", "sha256"}:
            raise ValueError("invalid archive candidate")
        source, target = _relative(item["source"]).as_posix(), _relative(item["target"]).as_posix()
        if not source.startswith(DOC + "/") or source.startswith(ARCHIVE + "/") or not target.startswith(ARCHIVE + "/"):
            raise ValueError("archive candidate escapes allowed roots")
        suffix = target[len(ARCHIVE) + 1:].split("/", 1)
        if len(suffix) != 2 or not MONTH.fullmatch(suffix[0]) or suffix[0] >= current or suffix[1] != source:
            raise ValueError("archive target does not preserve its logical path")
        if archive_plan.get("month") is not None and suffix[0] != archive_plan["month"]:
            raise ValueError("archive candidate belongs to a different month")
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("invalid archive hash")
    pending = _pending(project, "archive")
    if pending:
        return _move(project, pending[0], pending[1])
    if not candidates:
        return {"operationId": None, "state": "committed", "month": archive_plan.get("month"), "count": 0}
    op_path, op = _operation(project, "archive", archive_plan.get("month"), candidates)
    return _move(project, op_path, op)


def restore(project, month):
    current = dt.date.today().strftime("%Y-%m")
    _validate_requested_month(month, current)
    pending = _pending(project, "restore", month)
    if pending:
        return _move(project, pending[0], pending[1])
    base = _safe(project, f"{ARCHIVE}/{month}", archive_only=True)
    candidates = []
    recorded = _archive_provenance(project, month)
    if base.exists():
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                raise ValueError("symlinks are not allowed in archive")
            if path.is_file():
                logical = path.relative_to(base).as_posix()
                if not logical.startswith(DOC + "/") or logical.startswith(ARCHIVE + "/"):
                    raise ValueError("archived record escapes logical root")
                physical = path.relative_to(Path(project.root)).as_posix()
                actual = _hash(path)
                if physical in recorded and recorded[physical] != actual:
                    raise ValueError("archived record digest mismatch: " + physical)
                candidates.append({"source": physical, "target": logical, "sha256": recorded.get(physical, actual)})
    if not candidates:
        return {"operationId": None, "state": "committed", "month": month, "count": 0,
                "integrity": "archive-journal", "unverified": []}
    op_path, op = _operation(project, "restore", month, candidates)
    return _move(project, op_path, op)
