"""Bounded, evidence-backed adoption of existing project Markdown."""
import hashlib
import json
from pathlib import Path, PurePosixPath

import donghe_records as records


MANIFEST = 'docs/东合/来源.json'
MAX_CANDIDATES = 100
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
EXCLUDED = {'.git', '.donghe', 'node_modules', 'secrets'}


def _relative(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('source path is required')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe source path')
    if any(part.lower() in EXCLUDED for part in path.parts):
        raise ValueError('excluded source path')
    if len(path.parts) >= 2 and path.parts[0] == 'docs' and path.parts[1] == '东合':
        raise ValueError('managed Donghe files cannot be adopted as sources')
    return path.as_posix()


def _file(project, relative, require_markdown=True):
    relative = _relative(relative)
    if require_markdown and Path(relative).suffix.lower() not in {'.md', '.markdown'}:
        raise ValueError('source must be Markdown')
    root = Path(project.root).resolve()
    candidate = root / relative
    cursor = root
    for part in PurePosixPath(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('source path contains a symlink')
    if not candidate.is_file():
        raise ValueError('source history is missing')
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError('source escapes project') from exc
    size = candidate.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError('source exceeds per-file budget')
    data = candidate.read_bytes()
    if len(data) != size:
        raise ValueError('source changed while reading')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('source is not UTF-8 Markdown') from exc
    return relative, data, text


def _summary(relative, text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    heading = next((line.lstrip('#').strip() for line in lines if line.startswith('#')), None)
    title = heading or Path(relative).stem
    prose = next((line for line in lines if not line.startswith(('#', '```'))), '')
    return title[:120], ' '.join(prose.split())[:240]


def scan(project, limit=MAX_CANDIDATES, directories=None):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError('limit must be a positive integer')
    limit = min(limit, MAX_CANDIDATES)
    root = Path(project.root).resolve()
    requested = ['README.md', 'AGENTS.md', 'docs/README.md', 'docs']
    if directories is not None:
        if not isinstance(directories, list):
            raise ValueError('directories must be a list')
        requested.extend(_relative(item) for item in directories)
    paths = set()
    truncated = False
    for relative in requested:
        relative = _relative(relative)
        candidate = root / relative
        if candidate.is_symlink():
            truncated = True
            continue
        if candidate.is_file():
            paths.add(relative)
        elif candidate.is_dir():
            for item in candidate.rglob('*'):
                try:
                    rel = item.relative_to(root).as_posix()
                    _relative(rel)
                except ValueError:
                    continue
                if item.suffix.lower() in {'.md', '.markdown'} and item.is_file() and not item.is_symlink():
                    paths.add(rel)
    candidates = []
    total = 0
    for relative in sorted(paths):
        if len(candidates) >= limit:
            truncated = True
            break
        try:
            relative, data, text = _file(project, relative)
        except ValueError:
            truncated = True
            continue
        if total + len(data) > MAX_TOTAL_BYTES:
            truncated = True
            break
        total += len(data)
        title, snippet = _summary(relative, text)
        candidates.append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest(),
                           'title': title, 'snippet': snippet, 'bytes': len(data)})
    return {'candidates': candidates, 'truncated': truncated, 'bytesRead': total,
            'limit': limit, 'budgets': {'maxCandidates': MAX_CANDIDATES,
                                        'maxFileBytes': MAX_FILE_BYTES,
                                        'maxTotalBytes': MAX_TOTAL_BYTES}}


def _load_manifest(project):
    raw = project.read(MANIFEST)
    if raw is None:
        return {'schemaVersion': 1, 'sources': {}}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError('invalid source manifest') from exc
    if not isinstance(value, dict) or value.get('schemaVersion') != 1 or not isinstance(value.get('sources'), dict):
        raise ValueError('invalid source manifest')
    return value


def _registered(manifest, source_id):
    entry = manifest['sources'].get(source_id)
    digest = entry.get('sha256') if isinstance(entry, dict) else None
    if (not isinstance(entry, dict) or entry.get('recordId') != source_id
            or not isinstance(entry.get('path'), str) or not isinstance(digest, str)
            or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest)):
        raise ValueError('invalid source registration')
    return entry


def _render_body(source_id, snippet, note):
    return (f'来源登记：`{source_id}`\n\n- 用户说明：{note.strip()}\n'
            f'- 引文摘要：{snippet or "（原文无可用正文摘要）"}')


def adopt(project, spec):
    if not isinstance(spec, dict):
        raise ValueError('adoption spec must be an object')
    for key in ('id', 'kind', 'sourcePath', 'userNote'):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise ValueError(key + ' is required')
    relative, data, text = _file(project, spec['sourcePath'])
    digest = hashlib.sha256(data).hexdigest()
    title, snippet = _summary(relative, text)
    manifest = _load_manifest(project)
    existing = manifest['sources'].get(spec['id'])
    if existing is not None:
        existing = _registered(manifest, spec['id'])
    same = existing and existing.get('path') == relative and existing.get('sha256') == digest
    if existing and not same and spec.get('update') is not True:
        raise ValueError('source changed; explicit update is required')
    body = _render_body(spec['id'], snippet, spec['userNote'])
    record_spec = {'id': spec['id'], 'kind': spec['kind'], 'title': spec.get('title') or title,
                   'body': body, 'state': spec.get('state', 'open')}
    matches = [item for item in records.catalog(project)['records'] if item['id'] == spec['id']]
    if same and len(matches) == 1:
        return {'created': False, 'updated': False, 'source': dict(existing), 'record': matches[0]}
    if not existing and len(matches) == 1:
        if matches[0].get('kind') != spec['kind']:
            raise ValueError('record id already exists with another kind')
        content = project.read(matches[0]['path']) or ''
        if body not in content:
            raise ValueError('record id already exists outside this adoption')
        entry = {'recordId': spec['id'], 'path': relative, 'sha256': digest}
        manifest['sources'][spec['id']] = entry
        project.write(MANIFEST, json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        return {'created': False, 'updated': False, 'recovered': True,
                'source': entry, 'record': matches[0]}
    if existing and spec.get('update') is True:
        record = records.update(project, record_spec)
        updated = True
    else:
        record = records.create(project, record_spec)
        updated = False
    entry = {'recordId': spec['id'], 'path': relative, 'sha256': digest}
    manifest['sources'][spec['id']] = entry
    project.write(MANIFEST, json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    return {'created': not updated, 'updated': updated, 'source': entry, 'record': record}


def source(project, source_id):
    manifest = _load_manifest(project)
    if not isinstance(source_id, str) or not source_id or source_id not in manifest['sources']:
        raise ValueError('source is not registered')
    entry = _registered(manifest, source_id)
    relative, data, text = _file(project, entry.get('path'))
    current = hashlib.sha256(data).hexdigest()
    return {'id': source_id, 'path': relative, 'sha256': entry.get('sha256'),
            'currentSha256': current, 'stale': current != entry.get('sha256'), 'content': text}
