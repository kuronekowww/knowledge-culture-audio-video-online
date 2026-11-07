"""Decode and inspect an exported OpenFilm video; bind visual review to its real bytes."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw
from artifacts import atomic_json, file_hash, read_json


def run(args, **kw):
    result = subprocess.run(args, capture_output=True, **kw)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-1500:] if isinstance(result.stderr,bytes) else result.stderr[-1500:])
    return result.stdout


def pcm(file, start, duration=2):
    # Decode from the beginning before seeking: input-side AAC seeks can expose
    # differing packet preroll and falsely report a sub-frame sync error.
    raw=run(['ffmpeg','-v','error','-i',str(file),'-ss',str(start),'-t',str(duration),
             '-vn','-ac','1','-ar','16000','-f','f32le','pipe:1'])
    return np.frombuffer(raw,dtype='<f4').astype(np.float64)


def correlate(expected, actual):
    n=min(len(expected),len(actual));expected=expected[:n];actual=actual[:n]
    best=(-1,0)
    for lag in range(-640,641):
        a,b=(expected[-lag:],actual[:n+lag]) if lag<0 else (expected[:n-lag],actual[lag:])
        denominator=np.sqrt(np.dot(a,a)*np.dot(b,b))
        score=float(np.dot(a,b)/denominator) if denominator else 0
        if score>best[0]:best=(score,lag)
    gain=float(np.sqrt(np.dot(actual,actual)/np.dot(expected,expected))) if np.dot(expected,expected) else 0
    return {'correlation':best[0],'lag_ms':best[1]/16,'rms_gain':gain}


def extract(project, result):
    output=Path(result['output']); manifest=read_json(project/'knowledge-project.json')
    line=read_json(project/'knowledge-timeline.json')
    folder=project/'.knowledge'/'export-review'/result['output_sha256'][:16]
    folder.mkdir(parents=True,exist_ok=True)
    points=[('cover',0.5)]+[(x.get('shot_id','cover'),(x['start_us']+x['end_us'])/2e6)
                             for x in line['frames'] if x.get('role')=='story']
    if line['subtitles']:
        longest=max(line['subtitles'],key=lambda cue:len(cue['text']))
        points.append(('longest-subtitle',(longest['start_us']+longest['end_us'])/2e6))
    points.append(('last-body',manifest['body_duration_seconds']-.2))
    if line.get('outro'):
        points.append(('outro',manifest['body_duration_seconds']+1.5))
    samples=[]
    for name,t in points:
        path=folder/(name+'.jpg')
        if not path.exists():
            run(['ffmpeg','-y','-v','error','-ss',str(t),'-i',str(output),'-frames:v','1','-q:v','2',str(path)])
        samples.append({'name':name,'time':t,'path':str(path),'sha256':file_hash(path)})
    sheets=[]
    for start in range(0,len(samples),12):
        group=samples[start:start+12]
        canvas=Image.new('RGB',(1080,3*510),'#eeeeee');draw=ImageDraw.Draw(canvas)
        for i,item in enumerate(group):
            image=Image.open(item['path']);image.thumbnail((270,480))
            x,y=(i%4)*270,(i//4)*510
            canvas.paste(image,(x,y));draw.text((x+5,y+483),item['name']+' '+str(round(item['time'],2))+'s',fill='black')
        dest=folder/f'contact-{start//12+1:02}.jpg';canvas.save(dest,quality=91);sheets.append(str(dest))
    receipt={'output_sha256':file_hash(output),'samples':samples,'contact_sheets':sheets}
    atomic_json(folder/'review-index.json',receipt)
    return receipt,folder


def verify(project,visual_review=None):
    result=read_json(project/'render-result.json');manifest=read_json(project/'knowledge-project.json')
    if not result or result.get('status') not in ('mp4_exported','render_verified'):
        raise ValueError('完整MP4尚未导出')
    output=Path(result['output'])
    if file_hash(output)!=result['output_sha256']:raise ValueError('输出字节变化')
    for name,sha in result['input_sha256'].items():
        if file_hash(project/name)!=sha:raise ValueError('导出后工程已修改，需要重新导出：'+name)
    p=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(output)]))
    video=next(s for s in p['streams'] if s['codec_type']=='video')
    audio=next(s for s in p['streams'] if s['codec_type']=='audio')
    if (video['width'],video['height'],int(video['nb_frames']),video['r_frame_rate'])!=(1080,1920,round(manifest['duration_seconds']*30),'30/1'):
        raise ValueError('画布、帧率或帧数不符')
    if abs(float(audio['duration'])-manifest['duration_seconds'])>.06:raise ValueError('音轨时长偏差')
    run(['ffmpeg','-v','error','-i',str(output),'-f','null','-'])
    line=read_json(project/'knowledge-timeline.json')
    source=project/manifest['source_assets'][line['audio']['asset_id']]['project_path']
    body=manifest['body_duration_seconds'];checks=[]
    for t in sorted(set([.5,body/2,max(0,body-3)])):
        checks.append({'section':'body','time':t,**correlate(pcm(source,t),pcm(output,t))})
    if line.get('outro'):
        source=project/manifest['source_assets'][line['outro']['asset_id']]['project_path']
        checks.append({'section':'outro','time':body+.25,**correlate(pcm(source,.25),pcm(output,body+.25))})
    if any(x['correlation']<.96 or abs(x['lag_ms'])>33.34 or not .95<=x['rms_gain']<=1.05 for x in checks):
        raise ValueError('原声对齐/内容检查未通过：'+str(checks))
    review,folder=extract(project,result)
    report={'status':'technical_verified','output':str(output),'output_sha256':result['output_sha256'],
            'full_decode':'passed','video':video,'audio':audio,'audio_alignment':checks,
            'visual_review':'pending','review_index':str(folder/'review-index.json')}
    if visual_review:
        visual=read_json(visual_review)
        if visual.get('output_sha256')!=result['output_sha256'] or visual.get('approved') is not True:
            raise ValueError('视觉复核需绑定当前输出哈希并明确通过')
        if not visual.get('reviewed_images') or not visual.get('reviewer') or not visual.get('notes'):
            raise ValueError('视觉复核需记录审阅者、实际查看图像及发现')
        for entry in visual['reviewed_images']:
            if file_hash(Path(entry['path']))!=entry['sha256']:raise ValueError('复核图像已变化')
        reviewed={str(Path(entry['path']).resolve()) for entry in visual['reviewed_images']}
        if not {str(Path(p).resolve()) for p in review['contact_sheets']} <= reviewed:
            raise ValueError('视觉复核必须覆盖当前输出的全部分镜联系表')
        report.update(status='render_verified',visual_review=visual)
        result.update(status='render_verified',visual_review=visual,verification_report=str(project/'export-verification.json'))
        atomic_json(project/'render-result.json',result)
        job=Path(manifest['source_job'])
        atomic_json(job/'openfilm_result.json',{**result,'request_sha256':file_hash(job/'upload_request.json'),
                                               'plan_sha256':file_hash(job/'handoff_plan.json')})
    atomic_json(project/'export-verification.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir',type=Path,required=True)
    parser.add_argument('--visual-review',type=Path)
    args=parser.parse_args()
    try:
        result=verify(args.project_dir.resolve(),args.visual_review)
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as exc:print(str(exc),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
