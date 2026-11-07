"""Preflight, explicit review gates and artifact-only delivery reports."""
import argparse
import json
import subprocess
from pathlib import Path

import production_checks as checks
import production_io
import config
import timing
import subtitle_review
import transcription_cache
from artifacts import atomic_json, digest, file_hash, read_json, file_lock


def local_asr(job,cfg,start,end,full_track_reason=None):
    route=checks.document_route(job,cfg);source=checks.source_audio(job)
    duration=subtitle_review.duration_us(source)/1e6
    start,end=checks.seconds(start),checks.seconds(end)
    if start<0 or end<=start or end>duration+.001:raise ValueError('局部识别范围无效')
    if end-start>120 or (start==0 and end>=duration-.001 and duration>60):
        if not full_track_reason or not full_track_reason.strip() or route['route']!='localize_before_full_track':
            raise ValueError('整轨/大范围识别需要无法局部定位的诊断及--full-track-reason；有粗时轴应先选局部窗口')
    key=digest({'source':file_hash(source),'start':start,'end':end})
    target=job/'local-asr'/key;target.mkdir(parents=True,exist_ok=True)
    with file_lock(target/'.local.lock'):
        wav=target/'window.wav'
        receipt=read_json(target/'window.json',{})
        if not wav.is_file() or receipt.get('sha256')!=file_hash(wav):
            r=subprocess.run(['ffmpeg','-y','-v','error','-ss',str(start),'-t',str(end-start),'-i',str(source),'-vn','-ar','16000','-ac','1',str(wav)],capture_output=True,text=True)
            if r.returncode: wav.unlink(missing_ok=True);raise ValueError(r.stderr[-500:])
            atomic_json(target/'window.json',{'sha256':file_hash(wav)})
        atomic_json(target/'cut_result.json',{'segments':[{'path':str(wav)}]})
        err=transcription_cache.transcribe(target,cfg)
        if err:raise ValueError(err)
        units=subtitle_review.units_from_asr(read_json(target/'final_transcript.json'),subtitle_review.duration_us(wav))
        result={'source_audio':str(source),'source_sha256':file_hash(source),'start_seconds':start,'end_seconds':end,
                'route':route['route'],'full_track_reason':full_track_reason,'units':[
                    {**u,'source_start_us':u['start_us']+round(start*1e6),'source_end_us':u['end_us']+round(start*1e6)} for u in units],
                'note':'真实局部ASR与源音频偏移，仅用于定位/复听；不能覆盖最终剪辑音频的字幕识别。'}
        atomic_json(target/'source_alignment.json',result)
    return {'result':str(target/'source_alignment.json'),'units':len(units)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['confirm-highlights','document-route','local-asr','prepare-cut','check-cut','bind-storyboard','review-album-name','preflight','delivery'])
    p.add_argument('--output',type=Path);p.add_argument('--job-dir',type=Path);p.add_argument('--manifest',type=Path)
    p.add_argument('--reviewer',default='');p.add_argument('--evidence',default='');p.add_argument('--reason',default='')
    p.add_argument('--start',type=float);p.add_argument('--end',type=float);p.add_argument('--full-track-reason')
    a=p.parse_args()
    try:
        if a.command=='confirm-highlights':result=checks.confirm_highlights(a.manifest,a.reviewer,a.evidence)
        elif a.command=='delivery':result=production_io.delivery(a.manifest)
        elif a.command=='preflight':
            jobs=[a.job_dir.resolve()] if a.job_dir else [checks.path_at(a.manifest.resolve().parent,x) for x in read_json(a.manifest)['jobs']]
            result=production_io.preflight(jobs)
            atomic_json((a.manifest.with_suffix('.preflight.json') if a.manifest else jobs[0]/'preflight.json'),result)
        else:
            if not a.job_dir:raise ValueError('需要--job-dir')
            job=a.job_dir.resolve();cfg=read_json(job/'config.json')
            with file_lock(job/'.pipeline.lock',timeout=2), timing.bind(job):
                if a.command=='document-route':result=checks.document_route(job,cfg)
                elif a.command=='local-asr':result=local_asr(job,cfg,a.start,a.end,a.full_track_reason)
                elif a.command=='prepare-cut':result=checks.cut_request(job,cfg)
                elif a.command=='check-cut':result={'ok':checks.cut_ready(job,cfg)}
                elif a.command=='bind-storyboard':result=checks.bind_storyboard(job,a.reviewer,a.evidence)
                else:result=checks.review_album_name(job,cfg,a.reviewer,a.evidence,a.reason)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 1 if result.get('ok') is False or result.get('status')=='incomplete' else 0
    except (ValueError,OSError,KeyError,TypeError) as exc:print('检查失败: '+str(exc));return 2


if __name__=='__main__':raise SystemExit(main())
