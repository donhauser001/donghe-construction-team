"""Root HTML snapshot and registered-project reader; never runs task commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

from donghe_workspace_catalog import scan_workspace

PACKAGE = Path(__file__).resolve().parents[1]
ENTRY = '东合项目进化史.html'
MARKER = '<!-- donghe-workspace-entry:v1 -->'


def home():
    directory = Path.home() / '.donghe' / 'reader'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def atomic(path, content):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('refusing symlink output: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.donghe-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def registration(project_id):
    if len(project_id) != 32 or any(c not in '0123456789abcdef' for c in project_id):
        raise ValueError('invalid project identity')
    item = json.loads((home() / 'projects' / (project_id + '.json')).read_text())
    root = Path(item['root'])
    if not root.is_dir() or str(root.resolve()) != item['root']:
        raise ValueError('registered project moved or unavailable; reconnect from skill')
    return item


def render(snapshot, bridge=None):
    data = dict(snapshot)
    if bridge:
        data['bridge'] = bridge
    encoded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    html = (PACKAGE / 'assets/workspace-reader.html').read_text()
    html = html.replace('<!--DONGHE_STYLE-->', '<style>' + (PACKAGE / 'assets/workspace-reader.css').read_text() + '</style>')
    html = html.replace('<!--DONGHE_SCRIPT-->', '<script>' + (PACKAGE / 'assets/workspace-reader.js').read_text() + '</script>')
    html = html.replace('<!--DONGHE_DATA-->', '<script type="application/json" id="workspace-data">' + encoded + '</script>')
    return MARKER + '\n' + html


def snapshot(item):
    root = Path(item['root'])
    target = root / ENTRY
    if target.exists() and (target.is_symlink() or not target.read_text().startswith(MARKER)):
        raise ValueError('root entry exists and is not owned by Donghe; preserved')
    data = scan_workspace(root)
    data['project']['id'] = item['id']
    atomic(target, render(data))
    atomic(home() / 'snapshots' / (item['id'] + '.json'), json.dumps(data, ensure_ascii=False))
    return data


def attach(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor) or root == Path.home():
        raise ValueError('select a project directory, not a filesystem or home root')
    project_id = hashlib.sha256(str(root).encode()).hexdigest()[:32]
    item = {'id': project_id, 'root': str(root)}
    # No init, task import, archive, graph refresh or source mutation.
    data = snapshot(item)
    atomic(home() / 'projects' / (project_id + '.json'), json.dumps(item))
    return {'entry': str(root / ENTRY), 'projectId': project_id,
            'documents': len(data['documents']), 'lines': len(data['lines'])}


def resolve_url(url):
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
    if parsed.scheme != 'donghe' or parsed.netloc != 'workspace' or parsed.path not in ['', '/'] or parsed.fragment:
        raise ValueError('unsupported reader URL')
    if set(query) != {'id'} or len(query['id']) != 1:
        raise ValueError('reader URL must contain one registered project ID')
    return registration(query['id'][0])


def active(item):
    try:
        state = json.loads((home() / 'sessions' / (item['id'] + '.json')).read_text())
        port = state['port']
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            return None
        request = urllib.request.Request('http://127.0.0.1:%d/api/health' % port,
                                         headers={'X-Donghe-Token': state['token']})
        with urllib.request.urlopen(request, timeout=1) as response:
            if json.load(response).get('id') == item['id']:
                return state
    except (OSError, ValueError, KeyError):
        pass
    return None


def open_registered(item):
    state = active(item)
    if state is None:
        log_path = home() / 'reader.log'
        with log_path.open('ab') as log:
            subprocess.Popen([sys.executable, '-I', '-B', str(PACKAGE / 'scripts/workspace_entry.py'),
                              'serve', '--id', item['id']], stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log, start_new_session=True)
        for _ in range(100):
            time.sleep(.1)
            state = active(item)
            if state:
                break
    if not state:
        raise RuntimeError('reader could not start; see ~/.donghe/reader/reader.log')
    url = 'http://127.0.0.1:%d/?token=%s' % (state['port'], state['token'])
    subprocess.run(['/usr/bin/open', url], check=True)
    return {'opened': True, 'projectId': item['id']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['attach', 'refresh', 'open', 'url', 'serve', 'install-helper'])
    parser.add_argument('--project')
    parser.add_argument('--id')
    parser.add_argument('--url')
    args = parser.parse_args(argv)
    if args.action == 'install-helper':
        from donghe_workspace_install import install_helper
        result = install_helper(PACKAGE)
    elif args.action in ['attach', 'refresh', 'open']:
        if not args.project:
            parser.error('--project is required')
        result = attach(args.project)
        if args.action == 'open':
            result = open_registered(registration(result['projectId']))
    elif args.action == 'url':
        result = open_registered(resolve_url(args.url or ''))
    else:
        from donghe_workspace_server import serve
        serve(registration(args.id or ''))
        return
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
