#!/usr/bin/env python3
"""Install this verified package; preserve previous skill directories for rollback."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def _mode(path):
    return stat.S_IMODE(path.lstat().st_mode)


def _safe_absolute(path, label):
    if not path.is_absolute() or any(part == '..' for part in path.parts):
        raise ValueError('invalid '+label+': '+str(path))
    for candidate in [path, *path.parents]:
        if candidate.is_symlink():
            raise ValueError('invalid '+label+': '+str(path))
    return path


def _write_receipt(backup, value):
    path = backup/'install.json'
    temp = backup/('.install-'+uuid.uuid4().hex+'.json')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    os.replace(temp, path)


def verify(root):
    manifest = json.loads((root/'manifest.json').read_text())
    if manifest['platform'] != 'darwin-arm64' or platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('this package is verified only for macOS arm64')
    expected = manifest['files']
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    if actual != set(expected) | {'manifest.json'}: raise ValueError('unexpected/missing package files')
    for rel, item in expected.items():
        path = root/rel
        if Path(rel).is_absolute() or '..' in Path(rel).parts or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('unsafe package member: '+rel)
        if 'link' in item:
            if not path.is_symlink() or os.readlink(path) != item['link']: raise ValueError('link mismatch: '+rel)
        else:
            if not isinstance(item.get('mode'), int) or isinstance(item.get('mode'), bool):
                raise ValueError('file mode missing or invalid: '+rel)
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('file checksum mismatch: '+rel)
            if _mode(path) != item['mode']:
                raise ValueError('file mode mismatch: '+rel)
    return manifest


def healthcheck(root):
    env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'}
    subprocess.run([str(root/'bin/donghe'),'--help'],check=True,stdout=subprocess.DEVNULL,env=env)
    subprocess.run([str(root/'runtime/codegraph/codebase-memory-mcp'),'--version'],check=True,
                   stdout=subprocess.DEVNULL,env=env)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('targets',nargs='*',type=Path)
    parser.add_argument('--backup-root',type=Path,default=Path.home()/'.donghe/backups')
    args=parser.parse_args()
    manifest=verify(ROOT)
    targets=args.targets or [Path.home()/'.codex/skills/donghe-construction-team',Path.home()/'.cursor/skills/donghe-construction-team']
    if len(set(targets)) != len(targets): raise ValueError('duplicate install target')
    for target in targets:
        if target.name != 'donghe-construction-team':
            raise ValueError('invalid target: '+str(target))
        _safe_absolute(target,'target')
    backup_root=_safe_absolute(args.backup_root,'backup root')
    for target in targets:
        if backup_root == target or backup_root.is_relative_to(target) or target.is_relative_to(backup_root):
            raise ValueError('backup root and install target must be separate: '+str(target))
    backup=backup_root/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    backup.mkdir(parents=True,exist_ok=False)
    prepared=[]
    owned_stage_roots=[]
    switched=[]
    receipts=[]
    try:
        # Prepare and healthcheck every target before replacing any installation.
        for index,target in enumerate(targets):
            target.parent.mkdir(parents=True,exist_ok=True)
            _safe_absolute(target,'target')
            stage_root=Path(tempfile.mkdtemp(prefix='.donghe-stage-',dir=target.parent))
            owned_stage_roots.append(stage_root)
            stage=stage_root/'donghe-construction-team'
            shutil.copytree(ROOT,stage,symlinks=True)
            verify(stage)
            healthcheck(stage)
            prepared.append((index,target,stage))

        for index,target,stage in prepared:
            previous=backup/(str(index)+'.previous')
            had_previous=target.exists()
            if had_previous: shutil.move(str(target),previous)
            try:
                os.replace(stage,target)
            except BaseException:
                if previous.exists(): shutil.move(str(previous),target)
                raise
            switched.append((index,target,previous,had_previous))
            receipts.append({'target':str(target),'previous':str(previous) if had_previous else None})
            if os.environ.get('DONGHE_TEST_FAIL_AFTER_INSTALL_SWITCH') == str(index+1):
                raise RuntimeError('injected install commit interruption')
        # New complete packages provision the one machine-wide reader bridge.
        # Existing/synthetic packages without this capability keep their old path.
        helper = None
        if (targets[0]/'scripts/donghe_workspace_install.py').is_file():
            invoked = subprocess.run([str(targets[0]/'bin/donghe'), '--project', str(targets[0]),
                                      'reader', 'install-helper'], check=True, capture_output=True, text=True)
            helper = json.loads(invoked.stdout)
        _write_receipt(backup,{'state':'committed','platform':manifest['platform'],'targets':receipts,
                               'readerHelper':helper})
    except BaseException as exc:
        rollback_errors=[]
        rolled_back=[]
        for index,target,previous,had_previous in reversed(switched):
            failed=backup/(str(index)+'.failed')
            try:
                if target.exists(): shutil.move(str(target),failed)
                if had_previous:
                    if not previous.exists(): raise RuntimeError('previous installation missing during rollback')
                    shutil.move(str(previous),target)
                rolled_back.append({'target':str(target),'restoredPrevious':had_previous,
                                    'failedInstall':str(failed) if failed.exists() else None})
            except BaseException as rollback_exc:
                rollback_errors.append({'target':str(target),'error':str(rollback_exc)})
        try:
            _write_receipt(backup,{'state':'rollback_failed' if rollback_errors else 'rolled_back',
                                   'platform':manifest['platform'],'error':str(exc),
                                   'targets':receipts,'rolledBack':rolled_back,'rollbackErrors':rollback_errors})
        except BaseException as receipt_exc:
            rollback_errors.append({'target':str(backup/'install.json'),'error':str(receipt_exc)})
        if rollback_errors:
            raise RuntimeError('install failed and rollback needs recovery: '+json.dumps(rollback_errors)) from exc
        raise
    finally:
        for stage_root in owned_stage_roots:
            if stage_root.exists(): shutil.rmtree(stage_root)
    print(json.dumps({'installed':receipts,'backup':str(backup)},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
