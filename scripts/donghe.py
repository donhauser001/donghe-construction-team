#!/usr/bin/env python3
"""Small project-scoped completion gate. Evidence covers selected inputs, not all files.

Receipts are append-only by convention and checked for accidental modification;
this is not a security boundary against an adversary controlling the same account.
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import donghe_archive as archive
import donghe_records as records
import donghe_adopt as adoption
import donghe_graph as graph
import donghe_catalog as catalog
import donghe_ui as ui

DOC = 'docs/东合'
STATE = '.donghe/state'
FENCE = re.compile(r'```donghe-json\n(.*?)\n```', re.S)
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('unsafe identifier')
    return value


class Project:
    def __init__(self, root):
        raw = Path(root).absolute()
        if any(p.is_symlink() for p in [raw, *raw.parents]):
            raise ValueError('project symlinks are not allowed')
        self.root = raw.resolve()
        if not self.root.is_dir():
            raise ValueError('project root must exist')

    def path(self, relative):
        if not isinstance(relative, str) or not relative or '\\' in relative:
            raise ValueError('invalid relative path')
        p = Path(relative)
        if p.is_absolute() or '..' in p.parts or str(p) == '.':
            raise ValueError('path must be inside the project')
        target = self.root / p
        for item in [target, *target.parents]:
            if item == self.root:
                break
            if item.is_symlink():
                raise ValueError('symlinks are not allowed: ' + relative)
        if not target.resolve().is_relative_to(self.root):
            raise ValueError('external path')
        return archive.resolve(self, relative)

    @contextlib.contextmanager
    def lock(self):
        path = self.path(STATE + '/lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def write(self, relative, content):
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.donghe-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def read(self, relative):
        p = self.path(relative)
        return p.read_text() if p.exists() else None

    def init(self):
        with self.lock():
            for name in ['任务卡', '开发日志', '证据']:
                self.path(DOC + '/' + name).mkdir(parents=True, exist_ok=True)
            self.path(STATE + '/operations').mkdir(parents=True, exist_ok=True)
            if self.read(DOC + '/工作交接.md') is None:
                self.write(DOC + '/工作交接.md', '# 工作交接\n\n尚无已验收任务。\n')
        self.maintain()
        self.update_graph()
        return self.status()

    def task_path(self, task_id):
        return DOC + '/任务卡/' + identifier(task_id) + '.md'

    def render(self, task):
        return '# ' + task['title'].replace('\n', ' ') + '\n\n```donghe-json\n' + encode(task).rstrip() + '\n```\n'

    def task(self, task_id):
        content = self.read(self.task_path(task_id))
        if content is None:
            raise ValueError('task does not exist')
        blocks = FENCE.findall(content)
        if len(blocks) != 1:
            raise ValueError('task must have exactly one machine contract')
        task = json.loads(blocks[0])
        self.validate_spec(task)
        if task['id'] != task_id or task.get('status') not in ['active', 'completed']:
            raise ValueError('invalid task identity or status')
        return task

    def validate_spec(self, spec):
        identifier(spec['id'])
        if not isinstance(spec.get('title'), str) or not spec['title'].strip() or '```' in spec['title']:
            raise ValueError('title required; code fences forbidden')
        auth = spec.get('authorization')
        if not isinstance(auth, (str, bool)):
            raise ValueError('authorization must be a boolean or explicit text')
        criteria = spec.get('criteria')
        if not isinstance(criteria, list) or not criteria:
            raise ValueError('at least one criterion is required')
        seen = set()
        for c in criteria:
            identifier(c['id'])
            if c['id'] in seen:
                raise ValueError('duplicate criterion')
            seen.add(c['id'])
            if not isinstance(c.get('label'), str) or not c['label']:
                raise ValueError('criterion label required')
            cmd = c.get('command')
            if not isinstance(cmd, list) or not cmd or any(not isinstance(s, str) or '\0' in s for s in cmd) or not cmd[0]:
                raise ValueError('command must be an argv array')
            if not isinstance(c.get('inputs'), list) or not c['inputs']:
                raise ValueError('explicit nonempty input scope required')
            for p in c['inputs']:
                self.path(p)
            timeout = c.get('timeout', 120)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
                raise ValueError('timeout must be greater than 0 and at most 300')
            if not isinstance(c.get('reuse', False), bool):
                raise ValueError('reuse must be boolean')
        if not isinstance(spec.get('issues', []), list) or any(not isinstance(i, str) for i in spec.get('issues', [])):
            raise ValueError('issues must be strings')

    def create(self, spec):
        self.validate_spec(spec)
        task = {k: spec[k] for k in ['id', 'title', 'authorization', 'criteria']}
        task.update(issues=spec.get('issues', []), status='active', events=[{'at': now(), 'label': '任务建立', 'kind': 'created'}])
        with self.lock():
            if self.read(self.task_path(task['id'])) is not None:
                raise ValueError('task already exists')
            self.write(self.task_path(task['id']), self.render(task))
        return task

    def contract_hash(self, task):
        return digest(encode({k: task[k] for k in ['id', 'title', 'authorization', 'criteria', 'issues']}).encode())

    def fingerprints(self, criterion):
        result = {}
        for relative in criterion['inputs']:
            path = self.path(relative)
            if not path.exists():
                raise ValueError('input missing: ' + relative)
            paths = [path]
            if path.is_dir():
                paths += sorted(path.rglob('*'))
            for item in paths:
                rel = item.relative_to(self.root).as_posix()
                safe = self.path(rel)
                if safe.is_dir():
                    result[rel] = 'directory'
                elif safe.is_file():
                    result[rel] = digest(safe.read_bytes())
                else:
                    raise ValueError('inputs must be regular files or directories')
        return result

    def environment(self, criterion):
        cmd = criterion['command'][0]
        executable = str(self.path(cmd)) if '/' in cmd and not Path(cmd).is_absolute() else shutil.which(cmd)
        if not executable:
            raise ValueError('executable unavailable: ' + cmd)
        resolved = Path(executable).resolve()
        return {'executable': str(resolved), 'sha256': digest(resolved.read_bytes()), 'platform': sys.platform,
                'scope': 'Selected input bytes and executable only; other environment and external dependencies are not covered.'}

    def validate_fingerprints(self, value):
        if not isinstance(value, dict) or any(
            not isinstance(k, str) or not k or Path(k).is_absolute() or '..' in Path(k).parts or '\\' in k
            or not isinstance(v, str) or (v != 'directory' and not re.fullmatch('[0-9a-f]{64}', v))
            for k, v in value.items()
        ):
            raise ValueError('invalid fingerprint map')
        return value

    def load_fingerprints(self, reference):
        if not isinstance(reference, str) or not re.fullmatch('[0-9a-f]{64}', reference):
            raise ValueError('invalid fingerprint reference')
        data = self.path(DOC + '/证据/指纹/' + reference + '.json').read_bytes()
        if digest(data) != reference:
            raise ValueError('fingerprint digest mismatch: ' + reference)
        return self.validate_fingerprints(json.loads(data))

    def store_fingerprints(self, value):
        """Publish one immutable, canonical object; never repair an existing object."""
        data = encode(self.validate_fingerprints(value)).encode('utf-8')
        reference = digest(data)
        path = self.path(DOC + '/证据/指纹/' + reference + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.donghe-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            with contextlib.suppress(OSError):
                os.chmod(temp, 0o444)
            try:
                os.link(temp, path)
            except FileExistsError:
                pass
            self.load_fingerprints(reference)
        finally:
            os.unlink(temp)
        return reference

    def compact_receipt(self, record):
        """Encode normalized evidence without modifying its record or old receipts."""
        compact = dict(record)
        for field in ['before', 'after']:
            value = compact.pop(field)
            compact[field + 'Ref'] = None if value is None else self.store_fingerprints(value)
        compact['formatVersion'] = 2
        return compact

    def _load_receipt(self, relative):
        if not isinstance(relative, str) or not relative.startswith(DOC + '/证据/') or not relative.endswith('/receipt.json'):
            raise ValueError('invalid receipt location')
        path = self.path(relative)
        folder = path.parent
        manifest = json.loads(self.path(str(folder.relative_to(self.root) / 'manifest.json')).read_text())
        if not isinstance(manifest, dict) or set(manifest) != {'receipt.json', 'stdout.txt', 'stderr.txt'}:
            raise ValueError('invalid receipt manifest')
        output = {}
        record = None
        for name, checksum in manifest.items():
            file = self.path(str(folder.relative_to(self.root) / name))
            if name == 'receipt.json':
                data = file.read_bytes()
                actual = digest(data)
                record = json.loads(data)
            else:
                hasher, preview, size = hashlib.sha256(), b'', 0
                with file.open('rb') as handle:
                    for chunk in iter(lambda: handle.read(65536), b''):
                        hasher.update(chunk)
                        size += len(chunk)
                        preview += chunk[:max(0, 4096 - len(preview))]
                actual = hasher.hexdigest()
                output[name[:-4]] = {'path': file.relative_to(self.root).as_posix(),
                                     'preview': preview.decode('utf-8', errors='replace').encode('utf-8')[:4096].decode('utf-8', errors='ignore'),
                                     'truncated': size > 4096, 'bytes': size}
            if actual != checksum:
                raise ValueError('receipt digest mismatch')
        if not isinstance(record, dict):
            raise ValueError('invalid receipt record')
        version = record.get('formatVersion', 1)
        if type(version) is not int or version not in [1, 2]:
            raise ValueError('unsupported receipt format version')
        if version == 2:
            if 'before' in record or 'after' in record:
                raise ValueError('v2 receipt must use fingerprint references')
            for field in ['before', 'after']:
                reference = record[field + 'Ref']
                record[field] = None if reference is None else self.load_fingerprints(reference)
        else:
            for field in ['before', 'after']:
                if record[field] is not None:
                    self.validate_fingerprints(record[field])
        for field in ['taskId', 'criterionId', 'startedAt', 'finishedAt', 'exitCode', 'error', 'argv']:
            if field not in record:
                raise ValueError('receipt field missing: ' + field)
        identifier(record['taskId'])
        identifier(record['criterionId'])
        if record['exitCode'] is not None and type(record['exitCode']) is not int:
            raise ValueError('invalid receipt exitCode')
        if record['error'] is not None and not isinstance(record['error'], str):
            raise ValueError('invalid receipt error')
        argv = record['argv']
        if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or '\0' in arg for arg in argv) or not argv[0]:
            raise ValueError('invalid receipt argv')
        for field in ['startedAt', 'finishedAt']:
            if not isinstance(record[field], str):
                raise ValueError('invalid receipt ' + field)
            try:
                dt.datetime.fromisoformat(record[field])
            except ValueError:
                raise ValueError('invalid receipt ' + field)
        contract = record.get('contractHash')
        if not isinstance(contract, str) or not re.fullmatch('[0-9a-f]{64}', contract):
            raise ValueError('invalid receipt contractHash')
        if 'environment' not in record:
            raise ValueError('receipt field missing: environment')
        environment = record['environment']
        if environment is not None and (
            not isinstance(environment, dict)
            or any(not isinstance(environment.get(key), str) or not environment[key]
                   for key in ['executable', 'sha256', 'platform', 'scope'])
            or not re.fullmatch('[0-9a-f]{64}', environment['sha256'])
        ):
            raise ValueError('invalid receipt environment')
        return record, output

    def receipt(self, relative):
        return self._load_receipt(relative)[0]

    def receipt_summary(self, relative):
        result = {'path': relative, 'integrity': 'invalid', 'error': None,
                  'summary': None, 'output': {}, 'rawPath': relative}
        try:
            record, output = self._load_receipt(relative)
            summary = {key: record[key] for key in ['taskId', 'criterionId', 'startedAt', 'finishedAt', 'exitCode', 'error', 'argv']}
            summary.update(formatVersion=record.get('formatVersion', 1),
                           inputCounts={key: len(record[key] or {}) for key in ['before', 'after']},
                           inputChanged=record['before'] != record['after'])
            result.update(integrity='valid', summary=summary, output=output)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            result['error'] = str(exc)
        return result

    def candidates(self, task_id, criterion_id):
        prefix = DOC + '/证据/' + task_id + '--' + criterion_id + '--'
        return sorted(p for p in archive.logical_files(self, DOC + '/证据')
                      if p.startswith(prefix) and p.endswith('/receipt.json'))

    def inspect(self, task, criterion):
        candidates = self.candidates(task['id'], criterion['id'])
        if not candidates:
            return {'id': criterion['id'], 'label': criterion['label'], 'state': 'missing', 'receiptPath': None, 'reused': False}
        rel = candidates[-1]
        result = {'id': criterion['id'], 'label': criterion['label'], 'state': 'invalid', 'receiptPath': rel, 'reused': False}
        try:
            r = self.receipt(rel)
            if r['taskId'] != task['id'] or r['criterionId'] != criterion['id'] or r['argv'] != criterion['command']:
                return result
            if r['contractHash'] != self.contract_hash(task):
                result['state'] = 'stale'
            elif r['before'] != r['after'] or r.get('error'):
                result['state'] = 'invalid'
            elif r['after'] != self.fingerprints(criterion) or r['environment'] != self.environment(criterion):
                result['state'] = 'stale'
            else:
                result['state'] = 'passed' if r['exitCode'] == 0 else 'failed'
            result['reused'] = any(e.get('receiptPath') == rel and e.get('reused') for e in task.get('events', []))
        except (ValueError, OSError, KeyError, TypeError):
            pass
        return result

    def verify(self, task_id, criterion_id):
        identifier(criterion_id)
        with self.lock():
            if self.pending():
                raise ValueError('recover with finish, or abort pending completion first')
            if self.path(self.task_path(task_id)) != self.root / self.task_path(task_id):
                raise ValueError('archived task: restore its archive month before reopening')
            task = self.task(task_id)
            source_before = self.read(self.task_path(task_id))
            if not task['authorization']:
                raise ValueError('verification requires an authorized task')
            criterion = next((c for c in task['criteria'] if c['id'] == criterion_id), None)
            if criterion is None:
                raise ValueError('unknown criterion')
            previous = self.inspect(task, criterion)
            if task['status'] == 'completed' and self.closed(task):
                raise ValueError('task is already completed with current evidence')
            reused = criterion.get('reuse', False) and previous['state'] == 'passed'
        # Never hold the project lock while an authorized command executes: the
        # command may legitimately inspect status or use the read-only server.
        if reused:
            rel = previous['receiptPath']
        else:
            start = now()
            r = {'taskId': task_id, 'criterionId': criterion_id, 'startedAt': start, 'argv': criterion['command'],
                 'contractHash': self.contract_hash(task), 'before': None, 'after': None, 'environment': None,
                 'exitCode': None, 'error': None}
            stdout, stderr = b'', b''
            try:
                r['before'] = self.fingerprints(criterion)
                r['environment'] = self.environment(criterion)
                proc = subprocess.run(criterion['command'], cwd=self.root, shell=False, capture_output=True, timeout=criterion.get('timeout', 120))
                stdout, stderr, r['exitCode'] = proc.stdout, proc.stderr, proc.returncode
                r['after'] = self.fingerprints(criterion)
                if r['before'] != r['after']:
                    r['error'] = 'selected inputs changed during verification'
            except subprocess.TimeoutExpired as exc:
                stdout, stderr = exc.stdout or b'', exc.stderr or b''
                r['error'] = 'verification timed out'
            except (OSError, ValueError) as exc:
                r['error'] = str(exc)
            try:
                r['after'] = self.fingerprints(criterion)
            except (OSError, ValueError) as exc:
                r['error'] = (r['error'] or '') + '; after fingerprint: ' + str(exc)
            r['finishedAt'] = now()
            folder_rel = DOC + '/证据/' + task_id + '--' + criterion_id + '--' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '--' + uuid.uuid4().hex
            folder = self.path(folder_rel)
            folder.mkdir(parents=True, exist_ok=False)
            files = {'receipt.json': encode(self.compact_receipt(r)).encode(), 'stdout.txt': stdout, 'stderr.txt': stderr}
            files['manifest.json'] = encode({k: digest(v) for k, v in files.items()}).encode()
            for name, content in files.items():
                with (folder / name).open('xb') as out:
                    out.write(content)
                    out.flush()
                    os.fsync(out.fileno())
                with contextlib.suppress(OSError):
                    (folder / name).chmod(0o444)
            rel = folder_rel + '/receipt.json'
        with self.lock():
            if self.read(self.task_path(task_id)) != source_before or self.pending():
                raise ValueError('task changed during verification; immutable receipt retained at ' + rel)
            result = self.inspect(task, criterion)
            task['status'] = 'active'
            task['events'].append({'at': now(), 'label': criterion['label'] + ('：复用有效证据' if reused else '：' + result['state']),
                                   'kind': 'verified' if result['state'] == 'passed' else 'failed', 'receiptPath': rel, 'reused': bool(reused)})
            self.write(self.task_path(task_id), self.render(task))
            result['reused'] = bool(reused)
            return result

    def pending(self):
        base = self.path(STATE + '/operations')
        result = []
        if base.exists():
            for file in sorted(base.glob('*.json')):
                rel = file.relative_to(self.root).as_posix()
                op = json.loads(self.path(rel).read_text())
                if op.get('state') not in ['committed', 'aborted']:
                    result.append((rel, op))
        return result

    def snapshot(self, path, after):
        before = self.read(path)
        return {'path': path, 'before': before, 'after': after, 'beforeHash': digest(before.encode()) if before is not None else None, 'afterHash': digest(after.encode())}

    def check_journal(self, op):
        task_id = identifier(op['taskId'])
        if op.get('projectRoot') != str(self.root):
            raise ValueError('journal belongs to a different project')
        expected = [self.task_path(task_id), DOC + '/开发日志/' + op['date'] + '.md', DOC + '/工作交接.md']
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', op['date']) or [s['path'] for s in op['snapshots']] != expected:
            raise ValueError('unsafe journal targets')
        for s in op['snapshots']:
            if digest(s['after'].encode()) != s['afterHash'] or (digest(s['before'].encode()) if s['before'] is not None else None) != s['beforeHash']:
                raise ValueError('journal snapshot hash mismatch')
            if self.read(s['path']) not in [s['before'], s['after']]:
                raise ValueError('completion conflict: ' + s['path'])

    def recover(self, relative, op):
        self.check_journal(op)
        task = self.task(op['taskId'])
        if not task['authorization'] or task['issues'] or any(self.inspect(task, c)['state'] != 'passed' for c in task['criteria']):
            raise ValueError('completion evidence is no longer valid')
        for index, s in enumerate(op['snapshots'], 1):
            if self.read(s['path']) not in [s['before'], s['after']]:
                raise ValueError('completion conflict: ' + s['path'])
            if self.read(s['path']) != s['after']:
                self.write(s['path'], s['after'])
            if os.environ.get('DONGHE_TEST_FAIL_AFTER_WRITE') == str(index):
                raise RuntimeError('injected completion interruption')
        op['state'] = 'committed'
        op['committedAt'] = now()
        self.write(relative, encode(op))

    def abort(self, task_id):
        identifier(task_id)
        with self.lock():
            for rel, op in self.pending():
                if op['taskId'] != task_id:
                    continue
                self.check_journal(op)
                for snapshot in reversed(op['snapshots']):
                    current = self.read(snapshot['path'])
                    if current not in [snapshot['before'], snapshot['after']]:
                        raise ValueError('rollback conflict: ' + snapshot['path'])
                    if snapshot['before'] is None:
                        if current is not None:
                            self.path(snapshot['path']).unlink()
                    elif current != snapshot['before']:
                        self.write(snapshot['path'], snapshot['before'])
                op['state'] = 'aborted'
                op['abortedAt'] = now()
                self.write(rel, encode(op))
        return self.status()

    def closed(self, task):
        if task['status'] != 'completed' or any(self.inspect(task, c)['state'] != 'passed' for c in task['criteria']):
            return False
        operations = self.path(STATE + '/operations')
        committed = []
        if operations.exists():
            for path in operations.glob('*.json'):
                try:
                    op = json.loads(self.path(path.relative_to(self.root).as_posix()).read_text())
                    if op.get('state') == 'committed' and op.get('projectRoot') == str(self.root):
                        committed.append(op)
                except (OSError, ValueError):
                    return False
        for op in committed:
            try:
                if op['taskId'] != task['id']:
                    continue
                snapshots = op['snapshots']
                if [s['path'] for s in snapshots] != [self.task_path(task['id']), DOC + '/开发日志/' + op['date'] + '.md', DOC + '/工作交接.md']:
                    continue
                if any(digest(s['after'].encode()) != s['afterHash'] or (digest(s['before'].encode()) if s['before'] is not None else None) != s['beforeHash'] for s in snapshots):
                    continue
                if self.read(snapshots[0]['path']) != snapshots[0]['after']:
                    continue
                if op.get('receipts') != [self.inspect(task, c)['receiptPath'] for c in task['criteria']]:
                    continue
                log = self.read(snapshots[1]['path'])
                if log is None or not log.startswith(snapshots[1]['after']):
                    continue
                handoff = self.read(DOC + '/工作交接.md')
                if handoff == snapshots[2]['after'] or any(
                    other['createdAt'] > op['createdAt'] and other['snapshots'][2]['path'] == DOC + '/工作交接.md'
                    and digest(other['snapshots'][2]['after'].encode()) == other['snapshots'][2]['afterHash']
                    and handoff == other['snapshots'][2]['after'] for other in committed
                ):
                    return True
            except (KeyError, TypeError, ValueError, OSError, IndexError):
                continue
        return False

    def finish(self, task_id):
        identifier(task_id)
        with self.lock():
            pending = self.pending()
            for rel, op in pending:
                if op['taskId'] != task_id:
                    raise ValueError('another task has a pending completion')
                self.recover(rel, op)
            task = self.task(task_id)
            states = [self.inspect(task, c) for c in task['criteria']]
            if not task['authorization'] or task['issues'] or any(c['state'] != 'passed' for c in states):
                raise ValueError('finish blocked: authorization, issues, or fresh passing evidence missing')
            if not self.closed(task):
                task['status'] = 'completed'
                stamp = now()
                task['events'].append({'at': stamp, 'label': '全部验收点通过，完工回写完成', 'kind': 'completed'})
                day = dt.datetime.now().date().isoformat()
                log = DOC + '/开发日志/' + day + '.md'
                entry = '\n## ' + task_id + ' · ' + task['title'] + '\n\n- 完成时间：' + stamp + '\n' + ''.join('- 证据：' + c['receiptPath'] + '\n' for c in states)
                op = {'id': uuid.uuid4().hex, 'taskId': task_id, 'projectRoot': str(self.root), 'date': day, 'state': 'prepared', 'createdAt': stamp, 'receipts': [c['receiptPath'] for c in states],
                      'snapshots': [self.snapshot(self.task_path(task_id), self.render(task)), self.snapshot(log, (self.read(log) or '# 开发日志\n') + entry),
                                    self.snapshot(DOC + '/工作交接.md', '# 工作交接\n' + entry + '\n下一步：无新的授权任务时停止，不扩大施工范围。\n')]}
                relative = STATE + '/operations/' + op['id'] + '.json'
                self.write(relative, encode(op))
                self.recover(relative, op)
            records.feedback(self, task_id)
        self.maintain()
        self.update_graph()
        return self.status()

    def update_graph(self):
        # Package-owned component only; never install or discover a global tool.
        if not graph.DEFAULT_BINARY.is_file():
            return {'state': 'unavailable', 'reason': 'source checkout has no packaged graph'}
        with self.lock():
            marker = STATE + '/graph-check.json'
            fingerprint = None
            try:
                fingerprint = graph.source_fingerprint(self)
                previous = json.loads(self.read(marker) or '{}')
                if previous.get('failedFingerprint') == fingerprint:
                    return previous
                result = graph.refresh(self)
                result = {k: result[k] for k in ['state', 'fresh', 'reused', 'generation', 'fingerprint'] if k in result}
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                result = {'state': 'unavailable', 'error': str(exc), 'failedFingerprint': fingerprint}
            result['checkedAt'] = now()
            self.write(marker, encode(result))
            return result

    def maintain(self):
        with self.lock():
            if self.pending():
                return {'skipped': 'pending completion'}
            month = dt.date.today().strftime('%Y-%m')
            path = STATE + '/maintenance.json'
            prior = json.loads(self.read(path) or '{}')
            if prior.get('month') == month:
                return prior
            # Optional maintenance must not turn an accepted completion into a
            # failed command. Keep a visible, bounded diagnostic; explicit archive
            # remains strict and is the repair/retry entrypoint.
            try:
                proposed = archive.plan(self)
                if proposed.get('issues'):
                    result = {'state': 'blocked', 'issues': proposed['issues']}
                else:
                    result = {'state': 'checked', 'result': archive.apply(self, proposed)}
            except (ValueError, OSError) as exc:
                result = {'state': 'blocked', 'issues': [{'code': 'maintenance_failed', 'message': str(exc)}]}
            result.update({'month': month, 'checkedAt': now()})
            self.write(path, encode(result))
            return result

    def status(self):
        with self.lock():
            return self._status()

    def _status(self):
        pending = self.pending()
        recoverable_pending = [(rel, op) for rel, op in pending if op.get('projectRoot') == str(self.root)]
        pending_ids = {op['taskId'] for _, op in pending}
        tasks = []
        task_paths = archive.logical_files(self, DOC + '/任务卡')
        if task_paths:
            for relative in sorted(p for p in task_paths if p.endswith('.md')):
                task = self.task(Path(relative).stem)
                criteria = [self.inspect(task, c) for c in task['criteria']]
                states = {c['state'] for c in criteria}
                verification = 'invalid' if 'invalid' in states or 'failed' in states else 'stale' if 'stale' in states else 'pending' if 'missing' in states else 'passed'
                task_status = 'completed' if task['id'] not in pending_ids and self.closed(task) else 'active'
                if task['status'] == 'completed' and task_status != 'completed' and verification == 'passed':
                    verification = 'invalid'
                tasks.append({**{k: task[k] for k in ['id', 'title', 'authorization', 'issues', 'events']},
                              'declaredStatus': task['status'], 'status': task_status,
                              'verification': verification, 'sourcePath': self.task_path(task['id']),
                              'archived': self.path(self.task_path(task['id'])) != self.root / self.task_path(task['id']), 'criteria': criteria})
        active = [t for t in tasks if t['declaredStatus'] == 'active' and t['authorization'] and not t['issues']]
        review = [t for t in tasks if t['declaredStatus'] == 'completed' and t['status'] != 'completed']
        if recoverable_pending:
            decision = {'action': 'recover', 'reason': '完成事务待恢复；执行 finish ' + recoverable_pending[0][1]['taskId']}
        elif pending:
            decision = {'action': 'stop', 'reason': '历史完工事务属于其他项目路径，不能恢复或视为新施工授权；停止并人工复核'}
        elif active:
            decision = {'action': 'work', 'reason': '存在已授权的未完成任务'}
        elif review:
            decision = {'action': 'stop', 'reason': '历史完工记录的当前证据未通过，不能视为新施工授权；停止并显式复核，必要时执行 verify 重开'}
        else:
            decision = {'action': 'stop', 'reason': '没有可施工的已授权任务，或任务存在待解决问题；停止，不运行测试或自行扩展任务'}
        logs = self.path(DOC + '/开发日志')
        archive_status = archive.plan(self)
        archive_status.pop('candidates', None)
        source_registry = {'sources': {}, 'issues': []}
        try:
            source_registry['sources'] = adoption._load_manifest(self)['sources']
        except ValueError as exc:
            source_registry['issues'] = [str(exc)]
        return {'projectRoot': str(self.root), 'generatedAt': now(), 'decision': decision, 'tasks': tasks,
                'handoffPath': DOC + '/工作交接.md', 'logPaths': sorted(p for p in archive.logical_files(self, DOC + '/开发日志') if p.endswith('.md')),
                'knowledge': records.catalog(self), 'archive': archive_status,
                'maintenance': json.loads(self.read(STATE + '/maintenance.json') or '{}'),
                'sourceRegistry': source_registry,
                'uiRunPaths': sorted(path for path in archive.logical_files(self, DOC + '/证据/UI') if path.endswith('/result.json'))[-20:]}


def serve(project, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            try:
                if parsed.path == '/api/state':
                    state = project.status()
                    page = catalog.query(project)
                    state['knowledge'] = {**page, 'relations': page['edges']}
                    content, kind = encode(state).encode(), 'application/json; charset=utf-8'
                elif parsed.path == '/api/catalog':
                    params = parse_qs(parsed.query)
                    page = catalog.query(project, params.get('kind', [None])[0] or None, params.get('q', [''])[0],
                                         int(params.get('offset', ['0'])[0]), int(params.get('limit', ['50'])[0]))
                    content, kind = encode({**page, 'relations': page['edges']}).encode(), 'application/json; charset=utf-8'
                elif parsed.path == '/api/record':
                    record_id = parse_qs(parsed.query).get('id', [''])[0]
                    item = next((r for r in records.catalog(project)['records'] if r['id'] == record_id), None)
                    if item is None:
                        raise ValueError('record does not exist')
                    content, kind = encode(item).encode(), 'application/json; charset=utf-8'
                elif parsed.path == '/api/receipt':
                    rel = parse_qs(parsed.query).get('path', [''])[0]
                    content, kind = encode(project.receipt_summary(rel)).encode(), 'application/json; charset=utf-8'
                elif parsed.path == '/api/registered-source':
                    source_id = parse_qs(parsed.query).get('id', [''])[0]
                    content, kind = encode(adoption.source(project, source_id)).encode(), 'application/json; charset=utf-8'
                elif parsed.path == '/api/asset':
                    rel = parse_qs(parsed.query).get('path', [''])[0]
                    if not rel.startswith(DOC + '/证据/UI/') or not rel.endswith('.png'):
                        raise ValueError('asset outside UI evidence')
                    asset = project.path(rel)
                    if asset.stat().st_size > 20 * 1024 * 1024:
                        raise ValueError('asset exceeds read budget')
                    content, kind = asset.read_bytes(), 'image/png'
                    if not content.startswith(b'\x89PNG\r\n\x1a\n'):
                        raise ValueError('invalid PNG evidence')
                elif parsed.path == '/api/source':
                    rel = parse_qs(parsed.query).get('path', [''])[0]
                    if not rel.startswith(DOC + '/'):
                        raise ValueError('source outside allowed docs')
                    path = project.path(rel)
                    if not path.is_file() or path.suffix not in ['.md', '.json', '.txt']:
                        raise ValueError('source unavailable')
                    content, kind = encode({'path': rel, 'content': path.read_text()}).encode(), 'application/json; charset=utf-8'
                elif parsed.path == '/':
                    content = (Path(__file__).resolve().parent.parent / 'assets/evolution.html').read_bytes()
                    kind = 'text/html; charset=utf-8'
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                content = encode({'error': str(exc)}).encode()
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(encode({'url': 'http://127.0.0.1:' + str(server.server_port)}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ['init', 'status', 'next', 'doctor']:
        sub.add_parser(name)
    record = sub.add_parser('record')
    record.add_argument('--spec', required=True)
    record.add_argument('--update', action='store_true')
    link = sub.add_parser('link')
    link.add_argument('source')
    link.add_argument('target')
    link.add_argument('--relation', default='relates_to')
    catalog_cmd = sub.add_parser('catalog')
    catalog_cmd.add_argument('--kind')
    catalog_cmd.add_argument('--query', default='')
    catalog_cmd.add_argument('--offset', type=int, default=0)
    catalog_cmd.add_argument('--limit', type=int, default=50)
    scan = sub.add_parser('scan')
    scan.add_argument('--directory', action='append')
    scan.add_argument('--limit', type=int, default=100)
    adopt = sub.add_parser('adopt')
    adopt.add_argument('--spec', required=True)
    source = sub.add_parser('source')
    source.add_argument('id')
    web_ui = sub.add_parser('ui')
    web_ui.add_argument('--spec', required=True)
    feedback = sub.add_parser('feedback', help='replay declared completion feedback without rerunning business tests')
    feedback.add_argument('task_id')
    codegraph = sub.add_parser('graph')
    codegraph.add_argument('operation', choices=['status', 'refresh', 'search', 'trace'])
    codegraph.add_argument('query', nargs='?')
    codegraph.add_argument('--limit', type=int, default=20)
    codegraph.add_argument('--depth', type=int, default=3)
    codegraph.add_argument('--direction', choices=['inbound', 'outbound', 'both'], default='both')
    codegraph.add_argument('--retry', action='store_true', help='explicitly retry a previously failed index after investigating the cause')
    maintenance = sub.add_parser('archive')
    maintenance.add_argument('--month')
    maintenance.add_argument('--apply', action='store_true')
    maintenance.add_argument('--restore', action='store_true')
    task = sub.add_parser('task')
    task.add_argument('--spec', required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('task_id')
    verify.add_argument('criterion_id')
    receipt = sub.add_parser('receipt', help='read a bounded, integrity-checked receipt summary')
    receipt.add_argument('path')
    finish = sub.add_parser('finish')
    finish.add_argument('task_id')
    abort = sub.add_parser('abort')
    abort.add_argument('task_id')
    http = sub.add_parser('serve')
    http.add_argument('--port', type=int, default=8765)
    reader = sub.add_parser('reader', help='root HTML reader, independent of CLI governance adoption')
    reader.add_argument('operation', choices=['attach', 'refresh', 'open', 'install-helper'])
    args = parser.parse_args(argv)
    try:
        if args.command == 'reader':
            import donghe_workspace
            donghe_workspace.main([args.operation, '--project', args.project])
            return 0
        p = Project(args.project)
        if args.command == 'serve':
            serve(p, args.port)
            return 0
        if args.command == 'init':
            result = p.init()
        elif args.command == 'doctor':
            import install_release
            package = Path(__file__).resolve().parents[1]
            manifest = install_release.verify(package)
            graph._binary()
            browser = package / 'runtime/browser/chrome-headless-shell-mac-arm64/chrome-headless-shell'
            probe = subprocess.run([str(browser), '--version'], capture_output=True, text=True, timeout=10, check=True)
            if manifest['components']['browser']['version'] not in probe.stdout:
                raise ValueError('browser version differs from package lock')
            result = {'status': 'ready', 'platform': manifest['platform'], 'python': sys.version.split()[0],
                      'graph': graph.PINNED_VERSION, 'browser': probe.stdout.strip(), 'firstUseDownloads': False}
        elif args.command == 'ui':
            result = ui.run(p, json.loads(Path(args.spec).read_text()))
        elif args.command in ['scan', 'adopt', 'source', 'feedback', 'graph']:
            with p.lock():
                if args.command == 'scan':
                    result = adoption.scan(p, args.limit, args.directory)
                elif args.command == 'adopt':
                    result = adoption.adopt(p, json.loads(Path(args.spec).read_text()))
                elif args.command == 'source':
                    result = adoption.source(p, args.id)
                elif args.command == 'feedback':
                    result = records.feedback(p, args.task_id)
                elif args.operation == 'status':
                    result = graph.status(p)
                elif args.operation == 'refresh':
                    result = graph.refresh(p, retry=args.retry)
                    p.write(STATE + '/graph-check.json', encode({'state': result['state'], 'generation': result.get('generation'),
                                                               'fingerprint': result.get('fingerprint'), 'checkedAt': now()}))
                elif args.operation == 'search':
                    result = graph.search(p, args.query, args.limit)
                else:
                    result = graph.trace(p, args.query, args.direction, args.depth)
        elif args.command in ['record', 'link', 'catalog', 'archive']:
            with p.lock():
                if args.command == 'record':
                    result = (records.update if args.update else records.create)(p, json.loads(Path(args.spec).read_text()))
                elif args.command == 'link':
                    result = records.link(p, args.source, args.target, args.relation)
                elif args.command == 'catalog':
                    result = catalog.query(p, args.kind, args.query, args.offset, args.limit)
                elif args.restore:
                    if not args.month or args.apply:
                        raise ValueError('restore requires --month and cannot use --apply')
                    result = archive.restore(p, args.month)
                else:
                    proposed = archive.plan(p, args.month)
                    result = archive.apply(p, proposed) if args.apply else proposed
        elif args.command == 'task':
            result = p.create(json.loads(Path(args.spec).read_text()))
        elif args.command == 'verify':
            result = p.verify(args.task_id, args.criterion_id)
        elif args.command == 'receipt':
            result = p.receipt_summary(args.path)
        elif args.command == 'finish':
            result = p.finish(args.task_id)
        elif args.command == 'abort':
            result = p.abort(args.task_id)
        else:
            result = p.status()
            if args.command == 'next':
                result = result['decision']
        print(encode(result))
        return 1 if (args.command == 'verify' and result['state'] != 'passed') or (args.command == 'receipt' and result['integrity'] != 'valid') or (args.command == 'ui' and result.get('status') != 'passed') else 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(encode({'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
