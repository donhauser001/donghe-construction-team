#!/usr/bin/env python3
"""Build a pinned, offline-at-first-use skill package. Author-side tool only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ['SKILL.md', 'CHANGELOG.md', 'LICENSE', 'README.md', 'README.en.md',
           'agents', 'references', 'playbooks', 'templates', 'scripts', 'assets', 'bin', 'packaging']


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''): h.update(part)
    return h.hexdigest()


def extract(archive, target):
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            path = target / member.name
            if not path.resolve().is_relative_to(target.resolve()) or Path(member.name).is_absolute():
                raise ValueError('unsafe archive path')
            if member.isdev() or member.isfifo(): raise ValueError('unsupported archive member')
            if member.issym() or member.islnk():
                link = path.parent / member.linkname if member.issym() else target / member.linkname
                if not link.resolve().is_relative_to(target.resolve()): raise ValueError('unsafe archive link')
        tf.extractall(target)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--allow-dirty', action='store_true', help='author smoke builds only; never publish a dirty package')
    args = ap.parse_args()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    if dirty and not args.allow_dirty:
        raise ValueError('commit and validate source before release build; --allow-dirty is only for author smoke builds')
    source_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    lock = json.loads((ROOT / 'packaging/components.lock.json').read_text())
    args.cache.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        package = scratch / 'donghe-construction-team'
        package.mkdir()
        for item in PAYLOAD:
            src = ROOT / item
            if src.is_dir(): shutil.copytree(src, package/item, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            elif src.exists(): shutil.copy2(src, package/item)
        for name in ['python', 'codegraph', 'browser']:
            spec = lock[name]
            archive = args.cache / (name + ('.zip' if name == 'browser' else '.tar.gz'))
            if not archive.exists():
                tmp = archive.with_suffix('.download')
                urllib.request.urlretrieve(spec['url'], tmp)
                if sha(tmp) != spec['sha256']: raise ValueError('download checksum mismatch: ' + name)
                os.replace(tmp, archive)
            if sha(archive) != spec['sha256']: raise ValueError('checksum mismatch: ' + name)
            dest = scratch / name
            if name == 'browser':
                dest.mkdir()
                with zipfile.ZipFile(archive) as zf:
                    for entry in zf.infolist():
                        path = dest/entry.filename
                        if not path.resolve().is_relative_to(dest.resolve()): raise ValueError('unsafe zip member')
                    zf.extractall(dest)
                (dest/'chrome-headless-shell-mac-arm64/chrome-headless-shell').chmod(0o755)
                shutil.move(str(dest), package/'runtime/browser')
                continue
            extract(archive, dest)
            if name == 'python':
                (package/'runtime').mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest/'python'), package/'runtime/python')
            else:
                (package/'runtime/codegraph').mkdir(parents=True)
                matches = list(dest.rglob('codebase-memory-mcp'))
                if len(matches) != 1: raise ValueError('unexpected graph asset layout')
                shutil.copy2(matches[0], package/'runtime/codegraph/codebase-memory-mcp')
                (package/'runtime/codegraph/manifest.json').write_text(json.dumps({'version':spec['version'],'sha256':sha(matches[0])})+'\n')
                for entry in dest.rglob('*'):
                    if entry.is_file() and ('LICENSE' in entry.name or 'THIRD_PARTY' in entry.name):
                        shutil.copy2(entry, package/'runtime/codegraph'/entry.name)
        for cache in package.rglob('__pycache__'): shutil.rmtree(cache)
        manifest = {'schemaVersion':1, 'platform':lock['platform'], 'sourceCommit':source_commit, 'dirty':dirty, 'components':lock, 'files':{}}
        for path in sorted(package.rglob('*')):
            rel = path.relative_to(package).as_posix()
            if path.is_symlink():
                if not path.resolve().is_relative_to(package.resolve()): raise ValueError('external package link')
                manifest['files'][rel] = {'link':os.readlink(path)}
            elif path.is_file(): manifest['files'][rel] = {'sha256':sha(path), 'mode':path.stat().st_mode & 0o777}
        (package/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        subprocess.run([str(package/'bin/donghe'),'--help'],check=True,stdout=subprocess.DEVNULL,
                       env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'})
        # CPython may create bytecode before manifest if isolated mode ignores env.
        for cache in package.rglob('__pycache__'): shutil.rmtree(cache)
        version = next(line.split(' · ')[1] for line in (ROOT/'SKILL.md').read_text().splitlines() if line.startswith('# 东合施工队 · '))
        output = args.out / ('donghe-construction-team-' + version + '-' + lock['platform'] + '.tar.gz')
        with tarfile.open(output,'w:gz') as tf: tf.add(package,arcname=package.name)
        (output.with_suffix(output.suffix+'.sha256')).write_text(sha(output)+'  '+output.name+'\n')
        print(json.dumps({'package':str(output),'sha256':sha(output),'bytes':output.stat().st_size}))

if __name__ == '__main__': main()
