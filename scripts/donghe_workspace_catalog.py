"""Read existing Markdown governance without adopting or mutating a project."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_DOCUMENTS = 4000
TASK_ORDER_NOTE = "任务按编号排列；不表示执行依赖，也不代表 Agent 正在运行。"
METADATA_KEYS = ('编号', '签发', '状态', '半径', '规模', '下一步', 'parent_focus',
                 'task_line', '前置', '后继', '验收伞卡', '伞卡', '用户授权',
                 '负责人', '创建时间', '更新时间', '完成时间', '优先级', '写域')
METADATA_KEY_PATTERN = '(?:' + '|'.join(re.escape(key) for key in METADATA_KEYS) + ')'


def _plain(value):
    value = re.sub(r'<[^>]+>', '', value)
    value = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', value)
    return value.replace('**', '').replace('`', '').strip()


def _title(content, fallback):
    found = re.search(r'^#\s+(.+)$', content, re.M)
    return _plain(found.group(1)) if found else fallback


def _field(content, key):
    in_fence = False
    for line in content.splitlines():
        if re.match(r'^\s*(`{3,}|~{3,})', line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = line.strip().replace('**', '').replace('`', '')
        table = re.match(r'^\|\s*' + re.escape(key) + r'\s*\|\s*(.*?)\s*\|\s*$', line)
        if table:
            return table.group(1).strip()
        line = re.sub(r'^(?:>\s*)+', '', line)
        line = re.sub(r'^[-*+]\s+', '', line)
        # Metadata must begin with an explicit known key. A sentence merely
        # mentioning “状态：” is not a task-status declaration.
        if not re.match(r'^' + METADATA_KEY_PATTERN + r'\s*[:：]', line):
            continue
        fields = re.split(r'\s*·\s*(?=' + METADATA_KEY_PATTERN + r'\s*[:：])', line)
        for field in fields:
            found = re.match(re.escape(key) + r'\s*[:：]\s*(.+)', field)
            if found:
                return found.group(1).strip()
    return None


def _focus(value):
    if not isinstance(value, str):
        return None
    found = re.search(r'下一阶段工作\.md#([a-zA-Z][\w-]*)', value)
    if found:
        return found.group(1)
    return value if re.fullmatch(r'[a-zA-Z][\w-]*', value) else None


def _headings(content):
    """Keep explicit anchors only; heading proximity never assigns a task."""
    headings = {}
    current = None
    pending = []
    for line in content.splitlines():
        heading = re.match(r'^#{1,6}\s+(.+)', line)
        anchors = re.findall(r'<a\s+(?:id|name)=[\"\']([^\"\']+)', line)
        if heading:
            current = _plain(heading.group(1))
            for anchor in pending:
                headings[anchor] = current
            pending = []
        for anchor in anchors:
            if current:
                headings[anchor] = current
            else:
                pending.append(anchor)
        if heading:
            focus = re.search(r'\bfocus-\d+\b', current)
            if focus:
                headings.setdefault(focus.group(), current)
    return headings


def _natural(value):
    return [(0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in re.split(r'(\d+)', value)]


def _task(document, diagnostics):
    path, content = document['path'], document['content']
    if (Path(path).parent.name != '任务卡' or Path(path).stem.lower() == 'readme'
            or Path(path).stem.startswith('_')):
        return None
    meta = {}
    block = re.search(r'```donghe-json\s*\n(.*?)\n```', content, re.S)
    if block:
        try:
            meta = json.loads(block.group(1))
            if not isinstance(meta, dict):
                raise ValueError('record is not an object')
        except (ValueError, TypeError):
            diagnostics.append({'code': 'invalid_task_record', 'path': path})
            meta = {}
    title = str(meta.get('title') or document['title'])
    filename = re.sub(r'^\d{4}-\d{2}-\d{2}-', '', Path(path).stem)
    file_id = re.match(r'(?:UVM-P\d+-\d+|[A-Z]+\d+(?:-[A-Z]+\d+)*)\b', filename)
    identifier = meta.get('id') or (file_id.group() if file_id else None) or _field(content, '编号')
    if not identifier:
        found = re.search(r'(?<![A-Za-z0-9])(?:[A-Z]+\d+(?:-[A-Z]+\d+)*|UVM-P\d+-\d+)\b', title)
        identifier = found.group() if found else Path(path).stem
    else:
        identifier = _plain(str(identifier)).split('（')[0].strip()
    status = str(meta.get('status') or _field(content, '状态') or 'unknown')
    parent = _focus(meta.get('parent_focus') or _field(content, 'parent_focus'))
    task_line = meta.get('task_line') or _field(content, 'task_line')
    # A named line is an explicit membership statement, unlike authorization
    # prose or similarities between task titles. Keep its namespace separate.
    line_title = None
    if not parent and isinstance(task_line, str) and task_line.strip():
        line_title = _plain(task_line)[:200]
        parent = 'task-line:' + line_title
    next_step = _field(content, '下一步')
    excerpt = status + ('\n下一步：' + next_step if next_step else '')
    return {'id': identifier, 'title': title, 'status': status, 'path': path,
            'excerpt': excerpt[:800], '_parent': parent, '_lineTitle': line_title}


def _paths(root, diagnostics):
    for name in ('README.md', 'AGENTS.md'):
        if (root / name).exists() or (root / name).is_symlink():
            yield root / name
    docs = root / 'docs'
    if docs.is_symlink():
        diagnostics.append({'code': 'symlink_skipped', 'path': 'docs'})
        return
    for directory, dirs, files in os.walk(docs, followlinks=False):
        relative_directory = Path(directory).relative_to(root).parts
        # Baseline receipts may embed entire synthetic repositories. They are
        # evidence fixtures, never the current project's documents or tasks.
        if '基线回执' in relative_directory and 'workspace' in dirs:
            dirs.remove('workspace')
            diagnostics.append({'code': 'fixture_workspace_skipped',
                                'path': (Path(directory) / 'workspace').relative_to(root).as_posix()})
        for name in sorted(dirs):
            path = Path(directory) / name
            if path.is_symlink():
                diagnostics.append({'code': 'symlink_skipped', 'path': path.relative_to(root).as_posix()})
        dirs[:] = sorted(name for name in dirs if not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            if name.lower().endswith('.md'):
                yield Path(directory) / name


def _read(root, path):
    # Open every component relative to a directory descriptor, refusing symlinks
    # even if a directory is exchanged between the walk and the read.
    parts = path.relative_to(root).parts
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('not a regular file')
            return stream.read(MAX_DOCUMENT_BYTES + 1)
    finally:
        os.close(directory)


def scan_workspace(root: Path) -> dict:
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('project root must be a directory')
    diagnostics, documents = [], []
    total = 0
    for scanned, path in enumerate(_paths(root, diagnostics)):
        relative = path.relative_to(root).as_posix()
        if scanned >= MAX_DOCUMENTS:
            diagnostics.append({'code': 'document_count_limit', 'limit': MAX_DOCUMENTS})
            break
        try:
            data = _read(root, path)
        except (OSError, ValueError) as exc:
            diagnostics.append({'code': 'unreadable_or_symlink', 'path': relative, 'message': str(exc)})
            continue
        if len(data) > MAX_DOCUMENT_BYTES:
            diagnostics.append({'code': 'document_size_limit', 'path': relative, 'limit': MAX_DOCUMENT_BYTES})
            continue
        if total + len(data) > MAX_TOTAL_BYTES:
            diagnostics.append({'code': 'total_size_limit', 'path': relative, 'limit': MAX_TOTAL_BYTES})
            break
        total += len(data)
        try:
            content = data.decode('utf-8')
        except UnicodeDecodeError:
            diagnostics.append({'code': 'invalid_utf8', 'path': relative})
            continue
        archived = any(part in ('档案', '归档', 'archive', 'archives') for part in path.relative_to(root).parts)
        documents.append({'path': relative, 'title': _title(content, path.stem),
                          'content': content, 'archived': archived})
    roadmap = next((doc['content'] for doc in documents if doc['path'] == 'docs/下一阶段工作.md'), '')
    headings = _headings(roadmap)
    lines = {}
    for document in documents:
        if document['archived']:
            continue
        task = _task(document, diagnostics)
        if not task:
            continue
        parent = task.pop('_parent')
        line_title = task.pop('_lineTitle')
        key = parent or 'unclassified'
        if key not in lines:
            lines[key] = {'id': key, 'title': line_title or headings.get(key, key if parent else '待归类'),
                          'status': 'unknown', 'tasks': [], 'orderNote': TASK_ORDER_NOTE}
            if parent and not line_title:
                lines[key]['sourcePath'] = 'docs/下一阶段工作.md#' + parent
                if parent not in headings:
                    diagnostics.append({'code': 'unresolved_focus', 'id': parent})
        lines[key]['tasks'].append(task)
    for line in lines.values():
        line['tasks'].sort(key=lambda task: _natural(task['id']))
    order = {key: index for index, key in enumerate(headings)}
    ordered = sorted(lines.values(), key=lambda line: (line['id'] == 'unclassified',
                     order.get(line['id'], len(order)), _natural(line['id'])))
    return {'project': {'name': root.name, 'root': str(root)},
            'generatedAt': datetime.now(timezone.utc).isoformat(),
            'lines': ordered, 'documents': documents, 'diagnostics': diagnostics}
