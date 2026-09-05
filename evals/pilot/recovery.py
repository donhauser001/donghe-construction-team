"""Exercise recovery once on an authorized active, verified pilot task."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

script, root, task_id, output = sys.argv[1:]
root = Path(root)
command = [sys.executable, script, '--project', str(root)]
def call(args, **options):
    proc = subprocess.run(command + args, capture_output=True, text=True, **options)
    return {'argv': args, 'exit': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}
def state():
    return json.loads(call(['status'])['stdout'])
def files():
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'docs/东合').rglob('*') if p.is_file()}
assert state()['tasks'][0]['status']=='active'
result = {'initial':state()}
receipts = sorted(str(p) for p in (root/'docs/东合/证据').rglob('receipt.json'))
result['reuse'] = call(['verify', task_id, 'behavior'])
assert result['reuse']['exit']==0 and json.loads(result['reuse']['stdout'])['reused'] is True
assert receipts==sorted(str(p) for p in (root/'docs/东合/证据').rglob('receipt.json'))
result['interruption'] = call(['finish',task_id],env={**os.environ,'DONGHE_TEST_FAIL_AFTER_WRITE':'1'})
assert result['interruption']['exit']==1
result['pending']=state()
assert result['pending']['decision']['action']=='recover'
assert result['pending']['tasks'][0]['status']=='active'
result['recovery']=call(['finish',task_id])
assert result['recovery']['exit']==0
before=files()
result['duplicate']=call(['finish',task_id])
assert result['duplicate']['exit']==0 and before==files()
result['final']=state()
assert result['final']['decision']['action']=='stop'
assert result['final']['tasks'][0]['status']=='completed'
result['checks']=['valid receipt reused without subprocess receipt','injected mid-finish interruption','partial closure never complete','resume finishes all three sources','duplicate finish changes no documents','empty authorized queue stops']
Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':True,'checks':result['checks'],'output':output},ensure_ascii=False))
