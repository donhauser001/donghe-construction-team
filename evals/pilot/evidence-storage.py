#!/usr/bin/env python3
"""Read-only legacy comparison. Re-encoding experiment, never a verification rerun."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

module_path = Path(__file__).resolve().parents[2] / 'scripts/donghe.py'
spec = importlib.util.spec_from_file_location('donghe', module_path)
dh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dh)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('project')
parser.add_argument('--output', required=True)
args = parser.parse_args()
source = dh.Project(args.project)
root = Path(args.project).resolve()
receipts = sorted((root/dh.DOC/'证据').glob('*/receipt.json'))
assert receipts, 'No legacy receipts'
def source_hashes():
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root/dh.DOC).rglob('*') if p.is_file()}
before = source_hashes()
old_total = sum(p.stat().st_size for r in receipts for p in r.parent.iterdir() if p.is_file())
summaries = []
with tempfile.TemporaryDirectory(prefix='donghe-reencoding-') as folder:
    target = dh.Project(Path(folder).resolve())
    for old in receipts:
        relative = str(old.relative_to(root))
        record = source.receipt(relative)  # Verify original manifests before reading.
        compact = target.compact_receipt(record)
        new_dir = target.path(relative).parent
        new_dir.mkdir(parents=True)
        data = {'receipt.json': dh.encode(compact).encode(),
                'stdout.txt': (old.parent/'stdout.txt').read_bytes(),
                'stderr.txt': (old.parent/'stderr.txt').read_bytes()}
        data['manifest.json'] = dh.encode({n: dh.digest(v) for n,v in data.items()}).encode()
        for name, value in data.items(): (new_dir/name).write_bytes(value)
        roundtrip = target.receipt(relative)
        for name in ['taskId','criterionId','argv','exitCode','error','before','after','environment','contractHash']:
            assert record[name] == roundtrip[name], name
        old_summary = source.receipt_summary(relative)
        new_summary = target.receipt_summary(relative)
        assert old_summary['integrity'] == new_summary['integrity'] == 'valid'
        assert old_summary['output'] == new_summary['output']
        summaries.append({'receipt':relative,'exitCode':record['exitCode'],
                          'oldReceiptBytes':old.stat().st_size,
                          'compactReceiptBytes':(new_dir/'receipt.json').stat().st_size,
                          'summaryBytes':len(dh.encode(old_summary).encode())})
    files = list((target.root/dh.DOC/'证据').rglob('*'))
    new_total = sum(p.stat().st_size for p in files if p.is_file())
    blobs = list((target.root/dh.DOC/'证据'/'指纹').glob('*.json'))
    result = {'experiment':'same-data re-encoding; no commands rerun; no source rewritten',
              'receiptCount':len(receipts),'oldBytes':old_total,'newBytesIncludingSharedInputs':new_total,
              'reductionPercent':round((1-new_total/old_total)*100,2),'uniqueInputObjects':len(blobs),
              'roundtripEquivalent':True,'outputBytesPreserved':True,'receipts':summaries}
assert source_hashes()==before, 'Source changed during read-only experiment'
result['sourceUnchanged']=True
out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='receipts'},ensure_ascii=False,indent=2))
