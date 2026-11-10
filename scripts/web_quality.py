"""Content-hash checks: opening/subtitle changes stay scoped, delivery gets one full pass."""
import argparse
import json
from pathlib import Path
import os
import re
import subprocess
from artifacts import atomic_json, digest, file_hash, read_json


def fingerprint(project):
    html=(project/'index.html').read_text()
    structural=re.sub(r'<div\b(?=[^>]*\bid="cue-)[\s\S]*?</div>', '',html)
    files={}
    paths=[]
    for parent,dirs,names in os.walk(project):
        dirs[:]=[d for d in dirs if not d.startswith('.') and d not in ('qa','renders','node_modules','template-reference')]
        paths.extend(Path(parent)/n for n in names if not n.startswith('.'))
    for path in sorted(paths):
        rel=path.relative_to(project)
        if any(part.startswith('.') or part in ('qa','renders','node_modules','template-reference') for part in rel.parts):continue
        if path.is_file() and (rel.parts[0] in ('assets','lib','compositions') or path.suffix in ('.html','.css','.js','.mjs')):
            if str(rel)=='index.html':files[str(rel)]=digest(structural)
            elif str(rel) not in ('opening.js',):files[str(rel)]=file_hash(path)
    cues=read_json(project/'caption-display.json')
    code=file_hash(Path(__file__))
    binding=read_json(project/'runtime-binding.json')
    config={k:read_json(project/k,{}) for k in ('project-data.json','asset-manifest.json','package.json','hyperframes.json')}
    core=digest([files,code,binding,config])
    return {'common':core,'opening':file_hash(project/'opening.js'),
            'opening_plan':read_json(project/'opening-plan.json',{}),
            'subtitles':{c['id']:c for c in cues},
            'index_exact':file_hash(project/'index.html'),
            'full':digest({'core':core,'html':html,'opening':file_hash(project/'opening.js'),
                           'cues':cues,'storyboard':read_json(project/'web-storyboard.json',{}),
                           'opening_plan':read_json(project/'opening-plan.json',{})})}


def plan(project, scope='auto'):
    live=fingerprint(project);previous=read_json(project/'qa/last-inputs.json',{})
    if scope=='auto':
        if not previous or previous.get('common')!=live['common']:scope='full'
        elif previous.get('opening')!=live['opening'] or previous.get('opening_plan')!=live['opening_plan']:scope='opening'
        elif previous.get('subtitles')!=live['subtitles']:scope='subtitles'
        else:scope='full' if previous.get('full')!=live['full'] else 'cached'
        if scope=='opening' and previous.get('subtitles')!=live['subtitles']:scope='full'
    if scope in ('opening','subtitles') and previous and previous.get('common')!=live['common']:scope='full'
    if scope=='subtitles' and previous and previous.get('opening')!=live['opening']:scope='full'
    if scope=='opening' and previous and previous.get('subtitles')!=live['subtitles']:scope='full'
    args=[];times=[]
    if scope=='opening':
        opening=live['opening_plan']
        if not opening:raise ValueError('开场局部检查需要opening-plan.json')
        args=['--range',f'{max(0,opening["start"]-.1)},{opening["end"]+.1}','--samples','9']
    elif scope=='subtitles':
        changed=[c for id,c in live['subtitles'].items() if previous.get('subtitles',{}).get(id)!=c]
        changed.extend(c for id,c in previous.get('subtitles',{}).items() if live['subtitles'].get(id)!=c)
        for c in changed:times.extend([max(0,c['start']-.02),(c['start']+c['end'])/2,c['end']+.02])
        if not times:scope='cached'
        else:args=['--at',','.join(map(str,sorted(set(times)))),'--samples','0']
    key=digest({'scope':scope,'inputs':live,'validator':file_hash(Path(__file__))})
    return {'scope':scope,'inputs':live,'args':args,'key':key}


def run(project, scope='auto'):
    project=Path(project).resolve()
    item=plan(project,scope);binding=read_json(project/'runtime-binding.json')
    prior=read_json(project/'qa/last-check.json',{})
    def valid(row):
        return (row.get('ok') and row.get('inputs',{}).get('full')==item['inputs']['full'] and
                Path(row.get('report','')).is_file() and file_hash(Path(row['report']))==row.get('report_sha256'))
    if item['scope']=='cached' and valid(prior):
        return {**prior,'cache_hit':True}
    if item['scope'] in ('cached','full'):
        last_full=read_json(project/'qa/full-check.json',{})
        if valid(last_full):return {**last_full,'cache_hit':True}
        item=plan(project,'full')
    report=project/'qa'/f'check-{item["key"]}.json';report.parent.mkdir(exist_ok=True)
    result=subprocess.run([binding['node'],binding['cli'],'check',str(project),'--json',*item['args']],
                          capture_output=True,text=True,timeout=900)
    (report.with_suffix('.stdout.txt')).write_text(result.stdout)
    (report.with_suffix('.stderr.txt')).write_text(result.stderr)
    try:raw=json.loads(result.stdout)
    except ValueError:raw={'ok':False,'error':'Hyperframes未返回有效JSON，查看stdout/stderr'}
    atomic_json(report,raw)
    warning_count=raw.get('lint',{}).get('warningCount',0)
    record={'ok':result.returncode==0 and raw.get('ok') is True and raw.get('browserSkipped') is False,'scope':item['scope'],'inputs':item['inputs'],
            'report':str(report),'report_sha256':file_hash(report),'exit_code':result.returncode,
            'lint_warning_count':warning_count,'cache_hit':False,
            'note':'完整告警见原始报告；技术检查不替代视觉复核。'}
    if record['ok']:
        atomic_json(project/'qa/last-inputs.json',item['inputs'])
        atomic_json(project/'qa/last-check.json',record)
        if item['scope']=='full':atomic_json(project/'qa/full-check.json',record)
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project-dir',type=Path,required=True)
    parser.add_argument('--scope',choices=['auto','opening','subtitles','full'],default='auto');parser.add_argument('--plan-only',action='store_true')
    args=parser.parse_args();result=plan(args.project_dir,args.scope) if args.plan_only else run(args.project_dir,args.scope)
    print(json.dumps({k:v for k,v in result.items() if k!='inputs'},ensure_ascii=False,indent=2));return 0 if args.plan_only or result['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
