#!/usr/bin/env python3
"""Bounded, read-only pagination over Donghe source-record catalogs."""
from __future__ import annotations

import json

import donghe_records as records


MAX_LIMIT = 100
MAX_QUERY = 256
MAX_ISSUES = 100
MAX_OUTPUT_BYTES = 128 * 1024


class OutputBudgetError(ValueError):
    pass


def _encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _matches(record, text):
    if not text:
        return True
    needle = text.casefold()
    fields = (record.get("id"), record.get("title"), record.get("summary"), record.get("path"), record.get("state"))
    return any(needle in value.casefold() for value in fields if isinstance(value, str))


def _compact_record(record):
    result = {key: record[key] for key in ("id", "kind", "title", "state", "path", "archived") if key in record}
    if "title" in result:
        result["title"] = str(result["title"])[:160]
    if record.get("summary"):
        result["summary"] = record["summary"][:80]
    return result


def query(project, kind=None, text="", offset=0, limit=50):
    """Return one stable catalog page and only the edges incident to that page."""
    if kind is not None and kind not in records.KINDS:
        raise ValueError("unsupported record kind")
    if not isinstance(text, str) or len(text) > MAX_QUERY:
        raise ValueError("text must be a string of at most 256 characters")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be between 1 and 100")

    catalog = records.catalog(project)
    filtered = [record for record in catalog["records"] if (kind is None or record.get("kind") == kind) and _matches(record, text)]
    total = len(filtered)
    page = filtered[offset:offset + limit]
    page_ids = {record.get("id") for record in page}
    edges = [edge for edge in catalog["relations"] if edge.get("source") in page_ids or edge.get("target") in page_ids]
    issue_total = len(catalog["issues"])
    result = {
        "records": page,
        "edges": edges,
        "issues": catalog["issues"][:MAX_ISSUES],
        "issuesTotal": issue_total,
        "issuesTruncated": issue_total > MAX_ISSUES,
        "total": total,
        "offset": offset,
        "limit": limit,
        "hasMore": offset + len(page) < total,
        "nextOffset": offset + len(page) if offset + len(page) < total else None,
        "truncated": False,
    }
    if _encoded_size(result) <= MAX_OUTPUT_BYTES:
        return result

    result["records"] = [_compact_record(record) for record in page]
    result["issues"] = [{"code": issue.get("code"), "message": str(issue.get("message", ""))[:160],
                         **{key: issue[key] for key in ("id", "path", "source", "target") if key in issue}}
                        for issue in result["issues"]]
    result["truncated"] = True
    result["summaryMode"] = True
    if _encoded_size(result) > MAX_OUTPUT_BYTES:
        raise OutputBudgetError("catalog page exceeds 128 KiB without dropping record IDs or relation facts")
    return result
