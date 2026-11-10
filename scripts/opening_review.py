"""Validate real word anchors and require evidence of actual opening inspection."""
import argparse
import json
from pathlib import Path
from artifacts import atomic_json, digest, file_hash, read_json


def basis(project, job):
    plan=read_json(project/'opening-plan.json')
    anchor_ids={k for a in plan['actions'] for k in a['anchor']['unit_ids']}
    request=read_json(job/'subtitle_review_request.json')
    import re
    html=(project/'index.html').read_text()
    assets=read_json(project/'asset-manifest.json',{})
    deps=[record['path'] for record in assets.values()]+plan.get('asset_paths',[])
    deps.extend(str(p.relative_to(project)) for p in (project/'lib').glob('*.js'))
    # Visual dependencies include graphics/font bytes; a change to an unrelated subtitle
    # need not invalidate a reviewed word anchor. A shared layout change always does.
    return {name:file_hash(project/name) for name in ('opening.js','layout.css','runtime.js')} | {
        'shared_visuals':digest({'assets':{n:file_hash(project/n) for n in deps},
                                'stage':re.findall(r'<[^>]+(?:data-composition-id="knowledge-root"|id="scene-stage")[^>]*>',html),
                                'layout':read_json(project/'project-data.json',{}).get('layout_binding')}),
        'plan':file_hash(project/'opening-plan.json'),
        'source_words':digest([u for u in request['units'] if u['id'] in anchor_ids])}


def validate(project, job, review_path=None):
    plan=read_json(project/'opening-plan.json'); request=read_json(job/'subtitle_review_request.json')
    units={u['id']:u for u in request['units']}; times=set(); errors=[];windows=[]
    order={u['id']:i for i,u in enumerate(request['units'])}
    for key in ('message','style','style_signals','transition_to_body'):
        if not plan.get(key):errors.append('开场缺少'+key)
    start,end=plan['start'],plan['end']
    if not 0<=start<end:errors.append('开场范围无效')
    actions=plan.get('actions',[]);ids=[a.get('id') for a in actions]
    if not actions or len(set(ids))!=len(ids) or not all(ids):errors.append('动作ID为空/重复')
    for action in actions:
        aid=action.get('id','?')
        for key in ('subject','initial_state','contact','operation','result_state','constraints'):
            if not action.get(key):errors.append(aid+'缺少'+key)
        anchor=action.get('anchor',{});selected=[units[k] for k in anchor.get('unit_ids',[]) if k in units]
        if not selected or len(selected)!=len(anchor.get('unit_ids',[])):
            errors.append(aid+'锚点不是当前真实unit');continue
        positions=[order[u['id']] for u in selected]
        if any(u.get('timing_source')!='word' for u in selected) or positions!=list(range(positions[0],positions[-1]+1)):
            errors.append(aid+'锚点必须是连续且按原顺序的词级时码')
        a=min(u['start_us'] for u in selected);b=max(u['end_us'] for u in selected)
        windows.append((a/1e6,b/1e6))
        if (anchor.get('start_us')!=a or anchor.get('end_us')!=b
                or anchor.get('text')!=''.join(u['text'] for u in selected)):
            errors.append(aid+'锚点字词/时码未逐项对应真实unit')
        if not start<=a/1e6<b/1e6<=end:errors.append(aid+'锚点不在开场范围')
        times.update([a/1e6,(a+b)/2e6,b/1e6])
    times.update([start,(start+end)/2,max(start,end-.05),end+.05])
    current={} if errors else basis(project,job)
    review=read_json(review_path,{}) if review_path else {}
    visual_ok=bool(review) and review.get('basis')==current and bool(review.get('reviewer'))
    for key in ('continuous_playback','random_seek','transition','style_structure'):
        visual_ok=visual_ok and review.get('checks',{}).get(key) is True
    rows=review.get('actions',[])
    visual_ok=visual_ok and [r.get('id') for r in rows]==ids
    for row in rows:
        visual_ok=visual_ok and all(row.get(k) is True for k in ('initial_state','contact','operation','result_state','constraints')) and bool(row.get('observation'))
    frames=review.get('frames',[])
    visual_ok=visual_ok and bool(frames)
    for frame in frames:
        path=Path(frame['path']);path=path if path.is_absolute() else project/path
        visual_ok=visual_ok and path.is_file() and file_hash(path)==frame.get('sha256')
    inspected=[f['at'] for f in frames if isinstance(f.get('at'),(int,float))]
    visual_ok=visual_ok and all(any(a-.04<=t<=b+.04 for t in inspected) for a,b in windows)
    visual_ok=visual_ok and any(end-.15<=t<end for t in inspected) and any(end<=t<=end+.15 for t in inspected)
    result={'status':'passed' if not errors and visual_ok else 'needs_visual_review' if not errors else 'invalid',
            'basis':current,'errors':errors,'snapshot_times':sorted(times), 'visual_review_valid':bool(visual_ok),
            'note':'字段通过不代表画面满意；检查者须实际查看动作接触、枢轴、连续播放、随机跳转和接缝。'}
    atomic_json(project/'opening-validation.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project-dir',type=Path,required=True)
    p.add_argument('--job-dir',type=Path,required=True);p.add_argument('--review',type=Path)
    a=p.parse_args();r=validate(a.project_dir,a.job_dir,a.review);print(json.dumps(r,ensure_ascii=False,indent=2))
    return 0 if r['status']=='passed' else 2


if __name__=='__main__':raise SystemExit(main())
