#!/usr/bin/env python3
"""Audited gateway for native subagent samples; not an OS sandbox."""
import json
from pathlib import Path
import sys
import time
from baseline import (BASE_REF, CASES, FixtureTools, build_fixture, digest, dump,
                      now, observe, snapshot)


def main():
    mode, directory, *rest = sys.argv[1:]
    dest = Path(directory).resolve()
    if mode == 'prepare':
        dest.mkdir(parents=True, exist_ok=False)
        snapshot(BASE_REF,dest/'skill')
        dump(dest/'run.json',{'legacy_ref':BASE_REF,'host':'Codex native subagent via audited gateway',
            'gateway_sha256':digest(Path(__file__).read_bytes()),
            'harness_sha256':digest((Path(__file__).parent/'baseline.py').read_bytes()),
            'samples_per_case':1,'max_tool_calls':30,'seconds':240,
            'requested_model':'inherited parent; no override','usage':'unavailable',
            'limits':['Native shell remains available; gateway confinement is audited, not OS enforced.',
                      'No browser, delegation, or transaction completion command in fixture.']})
        for case in CASES:
            d=dest/case; d.mkdir()
            build_fixture(d/'workspace',case)
            dump(d/'initial-files.json',{str(p.relative_to(d/'workspace')):digest(p.read_bytes()) for p in (d/'workspace').rglob('*') if p.is_file()})
            dump(d/'request.json',{'case':case,'user_prompt':CASES[case]})
        print(dest);return
    meta = dest/'started.json'
    if not meta.exists(): dump(meta,{'at':now(),'unix':time.time()})
    trace=dest/'tools.jsonl'
    count=len(trace.read_text().splitlines()) if trace.exists() else 0
    if mode=='finish':
        final=json.loads(rest[0])
        if not isinstance(final,dict): raise ValueError('final must be an object')
        if (dest/'receipt.json').exists(): raise ValueError('receipt already finalized')
        dump(dest/'receipt.json',{'case':dest.name,'termination':'completed','final':final,
             'finished_at':now(),'elapsed_seconds':time.time()-json.loads(meta.read_text())['unix'],
             'usage':None,'native_agent_self_report':True})
        dump(dest/'observations.json',observe(dest/'workspace',dest.name,trace,final,'completed'))
        print('receipt recorded, independent review pending');return
    if count>=30 or time.time()-json.loads(meta.read_text())['unix']>240:
        print(json.dumps({'error':'evaluation budget reached; finish with current status'}));return
    tools=FixtureTools(dest/'workspace',dest.parent/'skill',trace)
    print(json.dumps(tools.call(mode,json.loads(rest[0]) if rest else {}),ensure_ascii=False))


if __name__=='__main__':main()
