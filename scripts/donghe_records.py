"""Project-scoped source records and their explicit relations."""
import datetime as dt
import json
from pathlib import Path
import re


PREFIX = 'docs/东合/资料'
KINDS = {'idea', 'blueprint', 'roadmap', 'next', 'knowledge', 'debt'}
STATES = {'open', 'active', 'closed'}
RELATIONS = {'derived_from', 'implements', 'relates_to', 'supersedes'}
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
TARGET = re.compile(r'(?:task:)?[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
FENCE = re.compile(r'```donghe-meta\n(.*?)\n```', re.S)


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('unsafe record identifier')
    return value


def _path(kind, record_id):
    if kind not in KINDS:
        raise ValueError('unsupported record kind')
    return f'{PREFIX}/{kind}/{_identifier(record_id)}.md'


def _validate_relation(item):
    if not isinstance(item, dict) or set(item) != {'target', 'type'}:
        raise ValueError('relation requires target and type')
    if item['type'] not in RELATIONS:
        raise ValueError('unsupported relation type')
    if not isinstance(item['target'], str) or not TARGET.fullmatch(item['target']):
        raise ValueError('unsafe relation target')
    return {'target': item['target'], 'type': item['type']}


def _validate_meta(meta, expected_path=None):
    if not isinstance(meta, dict) or isinstance(meta.get('schemaVersion'), bool) or meta.get('schemaVersion') != 1:
        raise ValueError('unsupported record schema')
    record_id = _identifier(meta.get('id'))
    kind = meta.get('kind')
    path = _path(kind, record_id)
    if expected_path is not None and path != expected_path:
        raise ValueError('record identity does not match path')
    title = meta.get('title')
    if not isinstance(title, str) or not title.strip() or '\n' in title or '```' in title:
        raise ValueError('record title is required')
    if meta.get('state') not in STATES:
        raise ValueError('unsupported record state')
    for field in ('createdAt', 'updatedAt'):
        if not isinstance(meta.get(field), str) or not meta[field]:
            raise ValueError(field + ' is required')
        try:
            parsed = dt.datetime.fromisoformat(meta[field].replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError(field + ' must be an ISO timestamp') from exc
        if parsed.tzinfo is None:
            raise ValueError(field + ' must include a timezone')
    relations = meta.get('relations')
    if not isinstance(relations, list):
        raise ValueError('relations must be a list')
    clean_relations = [_validate_relation(item) for item in relations]
    feedback = meta.get('feedback', [])
    if not isinstance(feedback, list):
        raise ValueError('feedback must be a list')
    for item in feedback:
        completed_at = item.get('completedAt', item.get('at')) if isinstance(item, dict) else None
        if not isinstance(item, dict) or not isinstance(item.get('taskId'), str) or not isinstance(completed_at, str):
            raise ValueError('invalid feedback entry')
    return {**meta, 'id': record_id, 'kind': kind, 'title': title.strip(),
            'relations': clean_relations, 'feedback': feedback}, path


def _parse(content, path):
    if not isinstance(content, str):
        raise ValueError('record is not readable text')
    blocks = FENCE.findall(content)
    if len(blocks) != 1:
        raise ValueError('record must have exactly one donghe-meta block')
    try:
        meta = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise ValueError('invalid donghe-meta JSON') from exc
    meta, _ = _validate_meta(meta, path)
    body = content[:FENCE.search(content).start()] + content[FENCE.search(content).end():]
    return meta, body.strip()


def _render(meta, body):
    clean = dict(meta)
    if not clean.get('feedback'):
        clean.pop('feedback', None)
    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, indent=2)
    return f'```donghe-meta\n{encoded}\n```\n\n{body.strip()}\n'


def _logical_files(project):
    try:
        from donghe_archive import logical_files
        return sorted(p for p in logical_files(project, PREFIX) if p.endswith('.md'))
    except ImportError:
        base = project.path(PREFIX)
        if not base.exists():
            return []
        return sorted(p.relative_to(project.root).as_posix() for p in base.rglob('*.md') if p.is_file())


def _read_record(project, path):
    """Read a logical record, including a cold archived copy when available."""
    try:
        from donghe_archive import resolve
        physical = resolve(project, path)
        archived = physical.resolve().relative_to(Path(project.root).resolve()).as_posix().startswith('docs/东合/档案/')
        return physical.read_text(), archived
    except ImportError:
        return project.read(path), False


def _issue(code, message, **fields):
    return {'code': code, 'message': message, **fields}


def create(project, spec):
    if not isinstance(spec, dict):
        raise ValueError('record spec must be an object')
    stamp = _now()
    meta = {
        'schemaVersion': 1,
        'id': spec.get('id'),
        'kind': spec.get('kind'),
        'title': spec.get('title'),
        'state': spec.get('state', 'open'),
        'createdAt': spec.get('createdAt', stamp),
        'updatedAt': spec.get('updatedAt', spec.get('createdAt', stamp)),
        'relations': spec.get('relations', []),
    }
    meta, path = _validate_meta(meta)
    body = spec.get('body', '')
    if not isinstance(body, str) or '```donghe-meta' in body:
        raise ValueError('record body must be Markdown without donghe-meta fences')
    index = catalog(project)
    if any(record['id'] == meta['id'] for record in index['records']):
        raise ValueError('record id already exists')
    candidate_relations = [{'source': meta['id'], **relation} for relation in meta['relations']]
    prospective = index['records'] + [_public(meta, path, body)]
    prior_cycle = any(issue['code'] == 'supersedes_cycle' for issue in
                      _relation_issues(project, index['records'], index['relations']))
    errors = _relation_issues(project, prospective, index['relations'] + candidate_relations)
    new_targets = {edge['target'] for edge in candidate_relations}
    errors = [issue for issue in errors if
              (issue.get('source') == meta['id'] and issue.get('target') in new_targets)
              or (issue['code'] == 'supersedes_cycle' and not prior_cycle)]
    if errors:
        raise ValueError(errors[0]['message'])
    project.write(path, _render(meta, body))
    return _public(meta, path, body)


def update(project, spec):
    if not isinstance(spec, dict):
        raise ValueError('record update must be an object')
    record_id = _identifier(spec.get('id'))
    index = catalog(project)
    matches = [record for record in index['records'] if record['id'] == record_id]
    if len(matches) != 1:
        raise ValueError('record does not exist or is duplicated')
    record = matches[0]
    if record.get('archived'):
        raise ValueError('archived record is read-only; restore it before updating')
    meta, body = _parse(project.read(record['path']), record['path'])
    if 'kind' in spec and spec['kind'] != meta['kind']:
        raise ValueError('record kind cannot change')
    for field in ('title', 'state', 'relations'):
        if field in spec:
            meta[field] = spec[field]
    if 'body' in spec:
        if not isinstance(spec['body'], str) or '```donghe-meta' in spec['body']:
            raise ValueError('record body must be Markdown without donghe-meta fences')
        body = spec['body']
    meta['updatedAt'] = _now()
    meta, _ = _validate_meta(meta, record['path'])
    candidate = [edge for edge in index['relations'] if edge['source'] != record_id]
    candidate.extend({'source': record_id, **edge} for edge in meta['relations'])
    errors = _relation_issues(project, index['records'], candidate)
    errors = [issue for issue in errors if issue.get('source') == record_id or issue['code'] == 'supersedes_cycle']
    if errors:
        raise ValueError(errors[0]['message'])
    project.write(record['path'], _render(meta, body))
    return _public(meta, record['path'], body)


def _public(meta, path, body):
    result = {key: value for key, value in meta.items() if key != 'feedback'}
    if meta.get('feedback'):
        result['feedback'] = meta['feedback']
    result.update(path=path, summary=' '.join(body.split())[:240])
    return result


def _relation_issues(project, records, relations):
    issues = []
    ids = {record['id'] for record in records}
    graph = {}
    for edge in relations:
        target = edge['target']
        if target.startswith('task:'):
            task_id = target[5:]
            try:
                project.task(task_id)
            except (ValueError, KeyError, TypeError, OSError):
                issues.append(_issue('missing_task_target', 'relation target task does not exist: ' + target,
                                     source=edge['source'], target=target))
        elif target not in ids:
            issues.append(_issue('missing_record_target', 'relation target record does not exist: ' + target,
                                 source=edge['source'], target=target))
        if edge['type'] == 'supersedes' and target in ids:
            graph.setdefault(edge['source'], set()).add(target)
    def cyclic(node, current, seen):
        if node in current:
            return True
        if node in seen:
            return False
        seen.add(node)
        return any(cyclic(nxt, current | {node}, seen) for nxt in graph.get(node, ()))
    if any(cyclic(node, set(), set()) for node in graph):
        issues.append(_issue('supersedes_cycle', 'supersedes relations contain a cycle'))
    return issues


def _feedback_targets(records, relations, task_id):
    by_id = {record['id']: record for record in records}
    seeds = sorted({edge['source'] for edge in relations
                    if edge['type'] == 'implements' and edge['target'] == 'task:' + task_id})
    parents = {}
    for edge in relations:
        if edge['type'] == 'derived_from' and edge['target'] in by_id:
            parents.setdefault(edge['source'], set()).add(edge['target'])
    targets = {}
    queue = [(seed, [seed], seed) for seed in seeds]
    while queue:
        current, via, source = queue.pop(0)
        if current not in by_id or current in targets:
            continue
        targets[current] = {'record': by_id[current], 'via': via, 'source': source}
        queue.extend((parent, via + [parent], source) for parent in sorted(parents.get(current, ())) if parent not in via)
    return targets


def _feedback_issues(project, records, relations):
    issues = []
    task_ids = sorted({edge['target'][5:] for edge in relations
                       if edge['type'] == 'implements' and edge['target'].startswith('task:')})
    for task_id in task_ids:
        try:
            task = project.task(task_id)
        except (ValueError, KeyError, TypeError, OSError):
            continue
        completed = next((event for event in reversed(task.get('events', [])) if event.get('kind') == 'completed'), None)
        if task.get('status') != 'completed' or completed is None:
            continue
        for record_id, target in _feedback_targets(records, relations, task_id).items():
            if not any(item.get('taskId') == task_id and item.get('completedAt', item.get('at')) == completed.get('at')
                       for item in target['record'].get('feedback', [])):
                issues.append(_issue('missing_feedback', 'completed task relation has no machine feedback: task:' + task_id,
                                     source=record_id, target='task:' + task_id, via=target['via']))
    return issues


def catalog(project):
    records = []
    relations = []
    issues = []
    seen = {}
    for path in _logical_files(project):
        try:
            project.path(path)
            content, archived = _read_record(project, path)
            meta, body = _parse(content, path)
            record = _public(meta, path, body)
            if archived:
                record['archived'] = True
            if meta['id'] in seen:
                issues.append(_issue('duplicate_id', 'duplicate record id: ' + meta['id'], id=meta['id'], paths=[seen[meta['id']], path]))
            else:
                seen[meta['id']] = path
            records.append(record)
            relations.extend({'source': meta['id'], 'target': edge['target'], 'type': edge['type']} for edge in meta['relations'])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            issues.append(_issue('invalid_record', str(exc), path=path))
    issues.extend(_relation_issues(project, records, relations))
    issues.extend(_feedback_issues(project, records, relations))
    return {'records': records, 'relations': relations, 'issues': issues}


def link(project, source_id, target_id, relation):
    _identifier(source_id)
    if relation not in RELATIONS:
        raise ValueError('unsupported relation type')
    if not isinstance(target_id, str) or not TARGET.fullmatch(target_id):
        raise ValueError('unsafe relation target')
    index = catalog(project)
    sources = [record for record in index['records'] if record['id'] == source_id]
    if len(sources) != 1:
        raise ValueError('source record does not exist or is duplicated')
    source = sources[0]
    if source.get('archived'):
        raise ValueError('archived source record is read-only')
    meta, body = _parse(project.read(source['path']), source['path'])
    edge = {'target': target_id, 'type': relation}
    if edge in meta['relations']:
        return {'source': source_id, **edge, 'created': False, 'path': source['path']}
    candidate = index['relations'] + [{'source': source_id, **edge}]
    prior_cycle = any(issue['code'] == 'supersedes_cycle' for issue in
                      _relation_issues(project, index['records'], index['relations']))
    errors = _relation_issues(project, index['records'], candidate)
    errors = [issue for issue in errors if
              (issue.get('source') == source_id and issue.get('target') == target_id)
              or (issue['code'] == 'supersedes_cycle' and not prior_cycle)]
    if errors:
        raise ValueError(errors[0]['message'])
    meta['relations'].append(edge)
    meta['updatedAt'] = _now()
    project.write(source['path'], _render(meta, body))
    return {'source': source_id, **edge, 'created': True, 'path': source['path']}


def feedback(project, task_id):
    _identifier(task_id)
    task = project.task(task_id)
    if task.get('status') != 'completed':
        raise ValueError('task is not declared completed')
    index = catalog(project)
    targets = _feedback_targets(index['records'], index['relations'], task_id)
    if not targets:
        return {'taskId': task_id, 'updated': [], 'unchanged': []}
    events = [event for event in task.get('events', []) if event.get('kind') in {'completed', 'verified'}]
    event = next((item for item in reversed(events) if item.get('kind') == 'completed'), events[-1] if events else None)
    if event is None:
        raise ValueError('completed task has no completion or verification event')
    receipts = [item.get('receiptPath') for item in task.get('events', [])
                if item.get('kind') == 'verified' and item.get('receiptPath')]
    completed_at = event.get('at')
    if not isinstance(completed_at, str):
        raise ValueError('completion event has no timestamp')
    updated, unchanged = [], []
    pending = []
    for record_id, target in targets.items():
        record = target['record']
        existing = any(item.get('taskId') == task_id and item.get('completedAt', item.get('at')) == completed_at
                       for item in record.get('feedback', []))
        if existing:
            unchanged.append(record_id)
            continue
        if record.get('archived'):
            raise ValueError('archived source record needs feedback; restore it before retrying')
        pending.append((record_id, target))
    for record_id, target in pending:
        record = target['record']
        meta, body = _parse(project.read(record['path']), record['path'])
        entry = {'taskId': task_id, 'completedAt': completed_at, 'via': target['via'],
                 'source': target['source'], 'note': 'fact propagated through explicit implements/derived_from relations'}
        if receipts:
            entry['receipts'] = receipts
        meta.setdefault('feedback', []).append(entry)
        meta['updatedAt'] = _now()
        project.write(record['path'], _render(meta, body))
        updated.append(record_id)
    return {'taskId': task_id, 'updated': updated, 'unchanged': unchanged, 'eventAt': completed_at,
            'receipts': receipts}
