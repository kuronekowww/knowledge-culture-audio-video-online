"""Verify what can actually be handed over: playable preview, project, or MP4 + project."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from artifacts import atomic_json, file_hash, read_json, verified_file
import web_quality


def metadata(project):
    ctx=read_json(project/'web-animation-context.json')
    if not ctx or not ctx.get('job_dir'):raise ValueError('需要标准网页入口生成的工程上下文')
    return ctx,Path(ctx['job_dir'])


def evidence(project):
    from opening_review import validate
    ctx,job=metadata(project)
    import title_review
    cfg=read_json(job/'config.json', {})
    title_review.require(job, cfg)
    for name,item in ctx['existing_inputs'].items():
        if not verified_file(Path(item['path']),item['sha256']):raise ValueError('交接依据已变化：'+name)
    data=read_json(project/'project-data.json')
    if data['missing_assets']:raise ValueError('缺少正式视觉资产：'+', '.join(data['missing_assets']))
    if cfg.get('cover_policy_version') == 2 or (job/'web-cover-review.json').exists():
        from cover_policy import validate_web_cover
        cover = read_json(project/'asset-manifest.json', {}).get('cover', {})
        if not cover.get('path'):
            raise ValueError('当前网页工程缺少正式封面')
        validate_web_cover(job, cfg, project/cover['path'])
    # A plan or a template alone is not authored content.
    shots=read_json(project/'web-storyboard.json',{}).get('shots',[])
    units={u['id'] for u in read_json(job/'subtitle_review_request.json')['units']}
    opening=read_json(project/'opening-plan.json',{})
    if not shots or shots[0].get('type')!='opening':raise ValueError('分镜须从真实图像动画开场开始')
    if not math.isclose(shots[0]['start'],opening['start'],abs_tol=.02) or not math.isclose(shots[0]['end'],opening['end'],abs_tol=.02):
        raise ValueError('开场分镜与动作验收的范围不一致')
    cursor=read_json(project/'project-data.json').get('body_start',1.)
    for shot in shots:
        if (not shot.get('source_unit_ids') or not set(shot['source_unit_ids'])<=units or
                abs(shot['start']-cursor)>.05 or shot['end']<=shot['start']):raise ValueError('分镜出现缺口、重叠或无原声依据：'+str(shot.get('id')))
        cursor=shot['end']
    if abs(cursor-data['bodyDuration'])>.12:raise ValueError('分镜没有覆盖完整正文')
    if validate(project,job,project/'opening-review.json')['status']!='passed':
        raise ValueError('尚缺当前开场的动作、自检关键帧、连续播放/跳转和接缝复核')
    return ctx,job,data


def preview(project,url,browser=None):
    binding=read_json(project/'runtime-binding.json')
    browser=browser or binding.get('browser')
    if not browser:raise ValueError('需提供--browser或用包含已验证浏览器的环境准备工程')
    scope=web_quality.fingerprint(project)['full']
    path=project/'qa/preview-probe.json';path.parent.mkdir(exist_ok=True)
    subprocess.run([binding['node'],str(Path(__file__).with_name('preview_probe.mjs')),str(project),url,str(browser),str(path)],check=True,timeout=100)
    result=read_json(path);result.update(input_sha256=scope,browser=str(Path(browser).resolve()))
    if not result['playable']:raise ValueError('预览页尚不可播放')
    atomic_json(path,result);return result


def gates(project):
    ctx,job,data=evidence(project)
    key=web_quality.fingerprint(project)['full']
    play=read_json(project/'qa/preview-probe.json',{})
    if not (play.get('input_sha256')==key and play.get('playable') and play.get('authored')):
        raise ValueError('请为当前已制作工程实测播放入口，确认时长、播放按钮和实际推进')
    check=web_quality.run(project,'full')
    if not check['ok']:raise ValueError('交付前全片检查失败：'+check['report'])
    return ctx,job,data,key,play,check


def export(project):
    ctx,job,data,key,play,check=gates(project)
    from delivery import requested
    if requested(read_json(job/'config.json'))['target']!='mp4':raise ValueError('用户只要预览/工程，无需导出MP4')
    dest=project/'renders'/('final-'+key[:12]+'.mp4');dest.parent.mkdir(exist_ok=True)
    prev=read_json(project/'qa/render-result.json',{})
    if prev.get('input_sha256')==key and prev.get('output')==str(dest) and verified_file(dest,prev.get('output_sha256')):return prev
    binding=read_json(project/'runtime-binding.json')
    # Render in a new directory on the same volume. Never replace a good export with a partial file.
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        output=Path(tmp)/'export.mp4'
        with (project/'qa/render.log').open('w') as log:
            subprocess.run([binding['node'],binding['cli'],'render',str(project),'--output',str(output),'--fps','30'],cwd=project,stdout=log,stderr=subprocess.STDOUT,check=True)
        if not output.is_file() or not output.stat().st_size:raise ValueError('无实际MP4输出')
        if web_quality.fingerprint(project)['full']!=key:raise ValueError('渲染过程中工程已改变，请核对后重导')
        os.replace(output,dest)
    row={'status':'mp4_exported','input_sha256':key,'output':str(dest),'output_sha256':file_hash(dest)}
    atomic_json(project/'qa/render-result.json',row);return row


def inspect_mp4(project,output):
    data=read_json(project/'project-data.json');assets=read_json(project/'asset-manifest.json')
    cues=read_json(project/'caption-display.json')
    times=[(s['start']+s['end'])/2 for s in read_json(project/'web-storyboard.json')['shots']]
    times.extend(read_json(project/'opening-validation.json',{}).get('snapshot_times',[]))
    return inspect_encoded(project,output,assets,data['bodyDuration'],data['duration'],cues,times,web_quality.fingerprint(project)['full'])


def inspect_encoded(project,output,assets,body,length,cues,scene_times,input_sha):
    from verify_openfilm import run,correlate
    import numpy as np
    from PIL import Image,ImageDraw
    sha=file_hash(output)
    probe=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(output)]))
    video=next((s for s in probe['streams'] if s['codec_type']=='video'),{})
    audio=next((s for s in probe['streams'] if s['codec_type']=='audio'),{})
    if (video.get('width'),video.get('height'),video.get('r_frame_rate'))!=(1080,1920,'30/1') or not audio:
        raise ValueError('成片应为1080×1920、30fps且包含原声')
    if abs(float(video['duration'])-length)>.08 or abs(float(audio['duration'])-length)>.12:raise ValueError('导出未覆盖完整工程时长')
    run(['ffmpeg','-v','error','-xerror','-i',str(output),'-f','null','-'])
    points={}
    for name,pct in [('first',0),('middle',.5),('last',1)]:
        cue=cues[round((len(cues)-1)*pct)];t=min(cue['start'],max(0,body-1))
        points[name]=(project/assets['voice']['path'],t,t,min(1.5,body-t))
    if 'outro' in assets:points['outro']=(project/assets['outro']['path'],.25,body+.25,min(2,length-body-.25))
    checks=[]
    def channel(source,at,n,index):
        raw=run(['ffmpeg','-v','error','-i',str(source),'-ss',str(at),'-t',str(n),'-map','0:a:0','-vn',
                 '-af',f'pan=mono|c0=c{index}','-ar','16000','-f','f32le','pipe:1'])
        return np.frombuffer(raw,dtype='<f4').astype(np.float64)
    for name,(source,t,at,n) in points.items():
        # Compare actual channels. FFmpeg's default stereo->mono mix artificially boosts
        # duplicated mono by sqrt(2), a false gain error even when each channel is faithful.
        nsrc=int(run(['ffprobe','-v','error','-select_streams','a:0','-show_entries','stream=channels','-of','default=nw=1:nk=1',str(source)]))
        if nsrc>audio['channels']:raise ValueError('导出丢失原声声道')
        for ch in range(audio['channels']):
            ref=channel(source,t,n,min(ch,nsrc-1));actual=channel(output,at,n,ch)
            check=correlate(ref,actual);checks.append({'section':name,'time':at,'channel':ch,**check})
    if any(row['correlation']<.95 or abs(row['lag_ms'])>33.34 or not .93<=row['rms_gain']<=1.07 for row in checks):
        raise ValueError('原声内容/音量/时码检查未通过：'+str(checks))
    longest=max(cues,key=lambda c:len(c['text']))
    samples=[.5,(longest['start']+longest['end'])/2,max(0,body-.2)]
    samples += scene_times
    if 'outro' in assets:samples.append(body+.5)
    folder=project/'qa'/('export-'+sha[:12]);folder.mkdir(parents=True,exist_ok=True);frames=[];sheets=[]
    times=sorted(set(round(min(length-.04,max(.01,t)),3) for t in samples))
    for i,t in enumerate(times):
        img=folder/f'{i:03}.jpg'
        run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(output),'-frames:v','1','-q:v','2',str(img)])
        frames.append({'at':t,'path':str(img),'sha256':file_hash(img)})
    for start in range(0,len(frames),12):
        group=frames[start:start+12];sheet=Image.new('RGB',(225*min(4,len(group)),420*((len(group)+3)//4)),'#f4f3eb');draw=ImageDraw.Draw(sheet)
        for i,item in enumerate(group):
            with Image.open(item['path']) as im:
                im.thumbnail((225,400));x=(i%4)*225;y=(i//4)*420;sheet.paste(im,(x,y));draw.text((x+8,y+401),str(item['at'])+'s',fill='#202326')
        p=folder/f'contact-{start:03}.jpg';sheet.save(p,quality=91);sheets.append({'path':str(p),'sha256':file_hash(p)})
    return {'status':'technical_verified','input_sha256':input_sha,'output':str(output),'output_sha256':sha,'duration_seconds':float(video['duration']),
            'size':[video['width'],video['height']],'fps':video['r_frame_rate'],'full_decode':'passed',
            'audio_alignment':checks,'review_frames':frames,'review_sheets':sheets}


def finish(project):
    from delivery import requested
    ctx,job,data,key,play,check=gates(project);target=requested(read_json(job/'config.json'))['target']
    result={'target':target,'project_dir':str(project),'input_sha256':key,'preview_url':play['url'],
            'launcher':str(project/'打开预览.command'),'check_report':check['report'],'font_mode':data['fontMode'],
            'lint_warnings':check['lint_warning_count'],'warnings':data['warnings'],'status':'preview_ready' if target=='preview' else 'project_verified'}
    if target=='mp4':
        rendered=export(project);output=Path(rendered['output'])
        tech=read_json(project/'qa/export-verification.json',{})
        if not (tech.get('input_sha256')==key and tech.get('output_sha256')==file_hash(output) and tech.get('status')=='technical_verified' and
                all(verified_file(Path(f['path']),f['sha256']) for f in tech.get('review_sheets',[]))):
            tech=inspect_mp4(project,output);atomic_json(project/'qa/export-verification.json',tech)
        visual=read_json(project/'export-visual-review.json',{})
        if not(visual.get('output_sha256')==tech['output_sha256'] and visual.get('reviewer') and visual.get('observation') and visual.get('approved') is True
               and visual.get('reviewed_sheets')==tech['review_sheets']):
            return {**result,**rendered,'status':'awaiting_export_visual_review','review_sheets':tech['review_sheets'],'review_frames':tech['review_frames']}
        result.update(status='render_verified',output=str(output),output_sha256=tech['output_sha256'])
    if not Path(result['launcher']).is_file():raise ValueError('交付缺少打开方式')
    # A server can disappear while the MP4 renders. Exercise the final link again;
    # this is a short playback probe, not another full-film traversal.
    preview(project,play['url'],play['browser'])
    atomic_json(project/'qa/delivery.json',result);atomic_json(job/'web_delivery_result.json',result)
    return result


def current(job,cfg):
    from delivery import requested
    row=read_json(job/'web_delivery_result.json',{})
    if row.get('target')!=requested(cfg)['target'] or not row.get('project_dir'):return None
    project=Path(row['project_dir'])
    try:
        ctx,source_job=metadata(project)
        if source_job.resolve()!=Path(job).resolve() or any(not verified_file(Path(v['path']),v['sha256']) for v in ctx['existing_inputs'].values()):return None
        if (web_quality.fingerprint(project)['full']!=row.get('input_sha256') or not Path(row['launcher']).is_file()
                or (row['target']=='mp4' and not verified_file(Path(row['output']),row['output_sha256']))):return None
    except (OSError,ValueError,KeyError):return None
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['preview','export','finish'])
    p.add_argument('--project-dir',required=True,type=Path);p.add_argument('--url');p.add_argument('--browser',type=Path)
    a=p.parse_args();project=a.project_dir.resolve()
    if a.action=='preview' and not a.url:p.error('preview需要--url；默认使用预检记录的浏览器')
    result=preview(project,a.url,a.browser) if a.action=='preview' else export(project) if a.action=='export' else finish(project)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 2 if result.get('status')=='awaiting_export_visual_review' else 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError) as exc:print(str(exc),file=sys.stderr);raise SystemExit(1)
