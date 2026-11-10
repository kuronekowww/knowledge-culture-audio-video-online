"""Bind a real, independently exported Jianying MP4 to its current verified draft."""
import argparse
from pathlib import Path
import sys
from artifacts import atomic_json, digest, file_hash, read_json, verified_file


def inputs(job):
    return {n:file_hash(job/n) for n in ('handoff_plan.json','upload_request.json','draft_result.json')}


def current(job,draft):
    r=read_json(job/'native_export_result.json',{})
    if not draft or r.get('status')!='render_verified':return None
    try:
        if (r['inputs']==inputs(job) and r['draft_dir']==draft['draft_dir']
                and verified_file(Path(r['output']),r['output_sha256'])
                and verified_file(Path(r['review']),r['review_sha256'])):return r
    except (OSError,KeyError,ValueError):pass
    return None


def verify(job,output,review=None):
    import assemble_draft
    from web_delivery import inspect_encoded
    draft=assemble_draft.current(job)
    if not draft:raise ValueError('须先核验当前剪映草稿')
    if output.suffix.lower()!='.mp4' or not output.is_file():raise ValueError('须指定从本条草稿实际导出的MP4')
    bound=inputs(job);key=digest(bound);sha=file_hash(output)
    request=read_json(job/'upload_request.json');plan=read_json(job/'handoff_plan.json')
    line=next(r for r in plan['timelines'] if r['orientation']=='portrait')
    assets={x['asset_id']:x for x in request['assets']}
    selected={'voice':assets[line['audio']['asset_id']]}
    if line.get('outro'):selected['outro']=assets[line['outro']['asset_id']]
    if any(not verified_file(Path(a['path']),a['sha256']) for a in selected.values()):raise ValueError('交接音频/片尾已改变')
    cache=job/'qa'/'native'/key[:12];stored=cache/'verification.json'
    tech=read_json(stored,{})
    if not (tech.get('input_sha256')==key and tech.get('output_sha256')==sha and tech.get('review_sheets') and
            all(verified_file(Path(x['path']),x['sha256']) for x in tech['review_sheets'])):
        cues=[{**c,'start':c['start_us']/1e6,'end':c['end_us']/1e6} for c in line['subtitles']]
        times=[(f['start_us']+f['end_us'])/2e6 for f in line['frames']]
        tech=inspect_encoded(cache,output,selected,line['body_duration_us']/1e6,line['total_duration_us']/1e6,cues,times,key)
        atomic_json(stored,tech)
    visual=read_json(review,{}) if review else {}
    if not (visual.get('approved') is True and visual.get('reviewer') and visual.get('observation')
            and visual.get('output_sha256')==sha and visual.get('reviewed_sheets')==tech['review_sheets']):
        return {'status':'awaiting_export_visual_review','report':str(stored),'review_sheets':tech['review_sheets'],'review_frames':tech['review_frames']}
    if inputs(job)!=bound or file_hash(output)!=sha:raise ValueError('验收期间工程或成片变化')
    r={'status':'render_verified','renderer':'jianying_native','draft_dir':draft['draft_dir'],'output':str(output),
       'output_sha256':sha,'inputs':bound,'review':str(review),'review_sha256':file_hash(review),'report':str(stored)}
    atomic_json(job/'native_export_result.json',r);return r


if __name__=='__main__':
    import json
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--job-dir',type=Path,required=True);a.add_argument('--mp4',type=Path,required=True);a.add_argument('--review',type=Path)
    args=a.parse_args()
    try:
        r=verify(args.job_dir.resolve(),args.mp4.resolve(),args.review.resolve() if args.review else None)
        print(json.dumps(r,ensure_ascii=False,indent=2));sys.exit(0 if r['status']=='render_verified' else 2)
    except (OSError,KeyError,ValueError,RuntimeError) as exc:sys.exit(str(exc))
