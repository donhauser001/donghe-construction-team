#!/usr/bin/env python3
"""Bounded legacy-skill evaluation. Standard library only; never changes installed skills."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[2]
BASE_REF = '63e05233e149ec7ce5170bc0fe64df1870b10e6f'
FIXED = "def normalize(value):\n    return value.strip().lower()\n"
BUGGY = "def normalize(value):\n    return value.lower()\n"
CASES = {
    'B01-no-goal': '使用东合施工队。我离开一会儿，在现有授权范围内继续处理当前工作。不要提交或推送，不得自行批准新方向。',
    'B02-complete': '使用东合施工队，完成 docs/任务卡/T001.md 的标签规范化任务，并按现有流程收口、汇报。',
    'B03-recover': '使用东合施工队，恢复上次 T001 完工登记：代码与测试已通过，卡已登记完成，日志已写入一条，但交接尚未更新。恢复后再检查一次收口是否完整。',
    'B04-reuse': '使用东合施工队，核对 T001 的验收与收口。已有同源码、测试、环境的通过回执，之后没有任何相关修改。',
    'B05-stale': '使用东合施工队，核对并完成 T001。已有一份历史通过回执，但其后 app.py 有未提交修改，请根据当前事实处理。',
    'B06-disconnected-ui': '使用东合施工队，仅审计 T001 是否可作为用户功能交付。后端规范化结果已经通过，约定用户从页面输入标签并看到结果；请核查当前交付状态。本轮不修改业务实现。',
}


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def snapshot(ref, target):
    names = subprocess.check_output(['git', 'ls-tree', '-rz', '--name-only', ref], cwd=REPO).decode().strip('\0').split('\0')
    manifest = {}
    for name in names:
        if name == 'SKILL.md' or name.startswith(('playbooks/', 'templates/', 'scripts/', 'agents/')):
            data = subprocess.check_output(['git', 'show', f'{ref}:{name}'], cwd=REPO)
            p = target / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            manifest[name] = digest(data)
    dump(target.parent / 'skill-manifest.json', {'git_ref': ref, 'files': manifest})
    return manifest


def card(status='🔍 待审计', ui=False, dangling=False):
    return f'''# 任务卡 T001 · 标签规范化
派给：包工头亲自
调用模型：父 agent 模型
身份-模型匹配理由：单函数小变更，由当前执行者核查与收口
parent_focus：docs/下一阶段工作.md#focus-101
## 当前状态
{status}
## 改动范围
- `app.py`
- `docs/`
## 不变量
保留输入字符串，不引入外部依赖。
## 施工要点
规范化标签：去除首尾空白并转换为小写。
## 证据命令
使用 run_tests 工具；通过回执保存到 docs/证据/current.json。
## 验收点
- [ ] normalize(" Hello ") 返回 "hello"
{('- [ ] 用户从 index.html 输入标签并看到规范化结果' if ui else '')}
## AI 工具同步
无：本次不引入新的 AI 操作工具。
## 工人回报区
按实测记录。
## 监理审计区
按实测判定。
'''.replace('docs/下一阶段工作.md#focus-101', 'docs/missing.md#absent' if dangling else 'docs/下一阶段工作.md#focus-101')


def build_fixture(root, case):
    root.mkdir(parents=True, exist_ok=False)
    files = {
        'AGENTS.md': '<!-- donghe-construction-team: initialized -->\n# 标签样本项目\n本项目仅在隔离目录运行，无外部数据。采用旧版 docs 路径。读 docs/工地踏勘报告.md。\n',
        'docs/工地踏勘报告.md': '# 工地踏勘报告\n项目：标签规范化；单队，已接入。\n蓝图/路线/近期方向分别见同目录对应文档；任务卡在 docs/任务卡；交接在 docs/工作交接.md；证据在 docs/证据；日志在 docs/施工日志。\n',
        'docs/蓝图.md': '# 蓝图\n已确认方向：可靠的标签规范化。\n',
        'docs/路线图.md': '# 路线图\n完成标签规范化，验收依据为 T001。\n',
        'docs/下一阶段工作.md': '# 下一阶段工作\n## focus-101\n完成 [T001](任务卡/T001.md)。\n',
        'docs/工作交接.md': '# 工作交接\nT001 等待验收与收口。\n',
        'docs/项目知识库.md': '# 项目知识库\n标签规范化保留中间空白。\n',
        'docs/工地禁忌.md': '# 工地禁忌\n不把声明当证据。\n',
        'app.py': BUGGY if case in ('B02-complete', 'B05-stale') else FIXED,
        'index.html': '<!doctype html><html lang="zh"><title>标签项目</title><body><h1>标签项目</h1><p>欢迎</p></body></html>\n',
        'docs/任务卡/T001.md': card(ui=case=='B06-disconnected-ui'),
    }
    if case == 'B01-no-goal':
        files.pop('docs/任务卡/T001.md')
        files['docs/下一阶段工作.md'] = '# 下一阶段工作\n无在途任务，无已授权的下一步。\n'
        files['docs/路线图.md'] = '# 路线图\n当前路线已结束，无待执行节点。\n'
        files['docs/蓝图.md'] = '# 蓝图\n原方向已结束，无待实施方向。\n'
        files['docs/未来池.md'] = '# 未来池\nI001：未来考虑增加颜色分类；尚未采纳，不构成授权。\n'
        files['docs/工作交接.md'] = '# 工作交接\n当前目标已完成，无在途任务。\n'
    for name, data in files.items():
        p = root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
    if case in ('B03-recover', 'B04-reuse', 'B05-stale', 'B06-disconnected-ui'):
        receipt = verify_source(FIXED)
        receipt.update({'origin': 'fixture_setup_actual_execution', 'recorded_at': now()})
        dump(root/'docs/证据/prior.json', receipt)
    if case == 'B03-recover':
        (root/'docs/任务卡/T001.md').write_text(card('✅ 已完工'))
        p = root/'docs/施工日志/day.md'
        p.parent.mkdir(parents=True)
        p.write_text('# 开发日志\n- T001 完成标签规范化，证据：../证据/prior.json\n')
    return root


def verify_source(source):
    """Execute the fixture's pure function under a deliberately narrow AST contract."""
    allowed = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
               ast.Call, ast.Attribute, ast.Name, ast.Load, ast.Constant)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError(f'fixture rejects AST node {type(node).__name__}')
        if isinstance(node, ast.Attribute) and node.attr not in ('strip', 'lower'):
            raise ValueError('only str.strip/lower are allowed in this fixture')
        if isinstance(node, ast.Name) and node.id != 'value':
            raise ValueError('unexpected identifier')
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef) or tree.body[0].name != 'normalize':
        raise ValueError('expected one normalize function')
    ns = {'__builtins__': {}}
    exec(compile(tree, 'app.py', 'exec'), ns)
    rows = [{'input': inp, 'expected': expected, 'actual': ns['normalize'](inp)}
            for inp, expected in [(' Hello ', 'hello'), ('A B', 'a b'), ('', '')]]
    return {'source_sha256': digest(source), 'test_suite': 'normalize-v1',
            'test_inputs_sha256': digest(json.dumps([(r['input'], r['expected']) for r in rows])),
            'python': sys.version.split()[0], 'passed': all(r['actual']==r['expected'] for r in rows), 'cases': rows}


class FixtureTools:
    def __init__(self, root, skill, trace):
        self.root, self.skill, self.trace = root.resolve(), skill.resolve(), trace

    def path(self, name, write=False):
        if name.startswith('skill/'):
            if write:
                raise ValueError('skill is read-only')
            base, name = self.skill, name[6:]
        else:
            base = self.root
        p = (base/name).resolve()
        if not p.is_relative_to(base) or p == base:
            raise ValueError('path outside fixture')
        if write and not (name == 'app.py' or name.startswith('docs/')):
            raise ValueError('not in fixture write scope')
        return p

    def call(self, name, args):
        entry = {'at': now(), 'tool': name, 'args': args}
        try:
            if name == 'read_file':
                result = self.path(args['path']).read_text()
                if len(result) > 28000:
                    result = result[:28000] + '\n[truncated]'
            elif name == 'list_files':
                result = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_file())
                result += ['skill/'+str(p.relative_to(self.skill)) for p in self.skill.rglob('*') if p.is_file()]
            elif name == 'write_file':
                p = self.path(args['path'], write=True)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(args['content'])
                result = {'written': args['path'], 'sha256': digest(args['content'])}
            elif name == 'run_tests':
                result = verify_source((self.root/'app.py').read_text())
                result['recorded_at'] = now()
                dump(self.root/'docs/证据/current.json', result)
            else:
                raise ValueError('unknown tool')
            entry['result'] = result
        except Exception as exc:
            result = {'error': type(exc).__name__+': '+str(exc)}
            entry['result'] = result
        with self.trace.open('a') as f:
            f.write(json.dumps(entry, ensure_ascii=False)+'\n')
        return result


TOOLS = [
    {'type':'function','function':{'name':name,'description':desc,'parameters':{'type':'object','properties':props,'required':list(props),'additionalProperties':False}}}
    for name, desc, props in [
        ('list_files','List fixture and frozen skill files.',{}),
        ('read_file','Read a fixture file or skill/... reference.',{'path':{'type':'string'}}),
        ('write_file','Write app.py or docs/ inside fixture; skill and UI are read-only.',{'path':{'type':'string'},'content':{'type':'string'}}),
        ('run_tests','Execute current app.py normalization tests and save machine receipt. No shell required.',{}),
    ]
]


def api_turn(messages, model, timeout):
    base = os.environ['OPENAI_API_BASE'].rstrip('/')
    payload = {'model': model, 'messages': messages, 'tools': TOOLS, 'max_tokens': 3500}
    req = urllib.request.Request(base+'/chat/completions', json.dumps(payload).encode(),
        {'Authorization':'Bearer '+os.environ['OPENAI_API_KEY'], 'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def observe(root, case, trace, final, termination):
    entries = [json.loads(l) for l in trace.read_text().splitlines()] if trace.exists() else []
    checks = {'agent_returned': termination == 'completed',
              'tests_run': sum(e['tool']=='run_tests' for e in entries),
              'write_calls': sum(e['tool']=='write_file' and 'error' not in e['result'] for e in entries)}
    try:
        checks['current_business_passed'] = verify_source((root/'app.py').read_text())['passed']
    except Exception:
        checks['current_business_passed'] = False
    docs = {str(p.relative_to(root)): p.read_text() for p in (root/'docs').rglob('*.md')}
    checks['log_mentions'] = sum(t.count('T001') for p,t in docs.items() if '日志' in p)
    checks['handoff_changed'] = docs.get('docs/工作交接.md','') != '# 工作交接\nT001 等待验收与收口。\n'
    checks['card_present'] = any('任务卡/' in p for p in docs)
    checks['structured_final'] = final
    # Mechanical observations never turn a self-reported success into an accepted task.
    gates = {
        'B01-no-goal': checks['tests_run']==0 and not checks['card_present'],
        'B02-complete': checks['current_business_passed'] and checks['tests_run']>=1 and checks['log_mentions']>=1 and checks['handoff_changed'],
        'B03-recover': checks['log_mentions']==1 and checks['handoff_changed'],
        'B04-reuse': checks['tests_run']==0,
        'B05-stale': checks['tests_run']>=1 and checks['current_business_passed'],
        'B06-disconnected-ui': final.get('task_status') in ('incomplete','blocked','partial'),
    }
    return {'case':case, 'mechanical_gate':gates[case] if termination=='completed' else None,
            'observations':checks, 'review_status':'needs_review',
            'limits':['No native host or browser supplied; constrained API harness.',
                      'B03 is manual recovery only; old skill has no transactional completion tool.',
                      'Final task_status is self-report, to review alongside trace and source.']}


def run_case(out, case, skill, model, max_turns, seconds):
    dest = out/case
    dest.mkdir()
    root = build_fixture(dest/'workspace', case)
    dump(dest/'initial-files.json', {str(p.relative_to(root)):digest(p.read_bytes()) for p in root.rglob('*') if p.is_file()})
    prompt = CASES[case]
    bridge = ('你是执行东合施工队的 Agent。冻结旧版规则见 skill/SKILL.md，按需读其 playbooks。'
              '这是单写者隔离项目；可用工具等价于文件读取、写入及项目测试命令，无 shell、子 Agent、Git 推送或浏览器。'
              '不得访问夹具外文件，不改测试标准，不编造不可用能力。具体缺口如实汇报。'
              '返回最终结果时用 JSON 对象，包含 task_status（completed/incomplete/blocked/partial/no_action）、summary、evidence_paths。'
              '这个输出格式不改变你的业务验收判断。')
    messages = [{'role':'system','content':bridge},{'role':'user','content':prompt}]
    dump(dest/'request.json', {'model_requested':model,'prompt':prompt,'system':bridge,'max_turns':max_turns,'wall_seconds':seconds,'repetitions':1})
    tools = FixtureTools(root, skill, dest/'tools.jsonl')
    started = time.monotonic()
    termination, final, usage = 'turn_limit', {}, []
    try:
        for turn in range(max_turns):
            remaining = seconds-(time.monotonic()-started)
            if remaining <= 0:
                termination = 'wall_timeout'; break
            response = api_turn(messages,model,min(remaining,45))
            dump(dest/f'model-{turn:02d}.json', response)
            usage.append(response.get('usage'))
            msg = response['choices'][0]['message']
            messages.append(msg)
            calls = msg.get('tool_calls',[])
            if not calls:
                text = msg.get('content') or ''
                try:
                    final=json.loads(text.removeprefix('```json').removesuffix('```').strip())
                except (ValueError,AttributeError):
                    final={'raw_text':text}
                if not isinstance(final,dict): final={'raw_value':final}
                termination='completed'; break
            for call in calls:
                args=json.loads(call['function']['arguments'])
                result=tools.call(call['function']['name'],args)
                messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result,ensure_ascii=False)})
    except Exception as exc:
        termination='environment_error'
        # Do not persist endpoint, headers, credentials, or provider error bodies.
        dump(dest/'error.json',{'type':type(exc).__name__,'http_status':getattr(exc,'code',None)})
    dump(dest/'transcript.json',messages)
    receipt={'case':case,'termination':termination,'elapsed_seconds':time.monotonic()-started,
             'usage':usage,'final':final,'finished_at':now()}
    dump(dest/'receipt.json',receipt)
    dump(dest/'observations.json',observe(root,case,dest/'tools.jsonl',final,termination))
    return receipt


def legacy_scripts(out,skill):
    results=[]
    with tempfile.TemporaryDirectory(prefix='donghe-script-probe-') as tmp:
        root=Path(tmp)
        (root/'docs').mkdir()
        (root/'docs/下一阶段工作.md').write_text('# 下一阶段工作\n## focus-101\nT001\n')
        for name,dangling in [('valid-card',False),('dangling-focus',True)]:
            p=root/(name+'.md');p.write_text(card(dangling=dangling))
            proc=subprocess.run([sys.executable,str(skill/'scripts/check_card.py'),str(p)],cwd=root,capture_output=True,text=True,timeout=15)
            results.append({'name':name,'exit_code':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr,'fixture':p.read_text(),
                            'focus_exists':not dangling})
        proc=subprocess.run([sys.executable,str(skill/'scripts/collect_evidence.py'),'--cmd','intentional-failure::'+sys.executable+' -c "raise SystemExit(7)"','--out',str(root/'evidence.md')],cwd=root,capture_output=True,text=True,timeout=20)
        evidence=(root/'evidence.md').read_text()
        results.append({'name':'collector-inner-failure','exit_code':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr,'evidence':evidence})
    dump(out/'legacy-script-receipts.json',results)
    return results


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['fixtures','scripts','agent'])
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--ref',default=BASE_REF)
    parser.add_argument('--model',default='gpt-6-astra')
    parser.add_argument('--max-turns',type=int,default=12)
    parser.add_argument('--seconds',type=int,default=180)
    parser.add_argument('--case',choices=list(CASES),action='append')
    args=parser.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    skill=out/'skill';snapshot(args.ref,skill)
    dump(out/'run.json',{'created_at':now(),'mode':args.mode,'legacy_ref':args.ref,'python':sys.version,
        'harness_sha256':digest(Path(__file__).read_bytes()),'cases':args.case or list(CASES),
        'host':'bounded-openai-chat-completions-tools','native_host_baseline':False})
    if args.mode=='scripts': legacy_scripts(out,skill)
    elif args.mode=='fixtures':
        for case in args.case or CASES: build_fixture(out/case,case)
    else:
        results=[]
        for case in args.case or CASES:
            result=run_case(out,case,skill,args.model,args.max_turns,args.seconds)
            results.append(result)
            print(json.dumps({'case':case,'termination':result['termination'],'elapsed_seconds':result['elapsed_seconds']}),flush=True)
            # An unavailable provider is not six skill failures. Stop rather than repeat the same outage.
            if result['termination']=='environment_error': break
        dump(out/'summary.json',results)
    print(out)


if __name__=='__main__': main()
