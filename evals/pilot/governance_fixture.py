#!/usr/bin/env python3
"""Create clearly synthetic governance UI fixtures; never backfill project history."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import donghe
import donghe_records as records
root=Path(sys.argv[1]).resolve()
root.mkdir(parents=True,exist_ok=True)
p=donghe.Project(root)
p.init()
(root/'fixture.txt').write_text('Synthetic governance acceptance only.\n')
p.create({'id':'GOV001','title':'验证资料到任务的来源闭环（合成验收）','authorization':'用户授权资料关联与归档研发；这是隔离夹具，不是业务项目历史',
          'criteria':[{'id':'read','label':'真实读取合成源文件','command':[sys.executable,'-c','from pathlib import Path; assert "Synthetic" in Path("fixture.txt").read_text(); print("source-read-ok")'],'inputs':['fixture.txt'],'reuse':True}]})
with p.lock():
    records.create(p,{'id':'I001','kind':'idea','title':'减少重复整理（合成想法）','body':'合成验收：只记录想法，不自动派工。'})
    records.create(p,{'id':'B001','kind':'blueprint','title':'让施工自然留下证据（合成方向）','body':'合成验收：当前方向仅覆盖本次任务。','relations':[{'type':'derived_from','target':'I001'}]})
    records.link(p,'B001','task:GOV001','implements')
    records.create(p,{'id':'K001','kind':'knowledge','title':'历史来源不能因归档消失（合成历史）','state':'closed','createdAt':'2026-08-01T00:00:00Z','updatedAt':'2026-08-02T00:00:00Z','body':'仅用于归档验收的合成历史，不是实际项目发生的事件。','relations':[{'type':'relates_to','target':'I001'}]})
p.verify('GOV001','read')
p.finish('GOV001')
print(json.dumps({'root':str(root),'decision':p.status()['decision']},ensure_ascii=False))
