"""Build a portable OpenFilm project from the reviewed local handoff; never re-run media generation."""
from __future__ import annotations

import argparse
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from artifacts import atomic_bytes, atomic_copy, atomic_json, digest, file_hash, file_lock, read_json

VERSION = 3
SCRIPT_DIR = Path(__file__).resolve().parent


def seconds(us):
    return f'{int(us) / 1_000_000:.6f}'.rstrip('0').rstrip('.')


def stamp(us):
    ms = round(int(us) / 1000)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}'


def checked_inputs(job):
    plan = read_json(job / 'handoff_plan.json')
    request = read_json(job / 'upload_request.json')
    if not plan or not request or request['source_version'] != plan['source_version']:
        raise ValueError('需要同一版本的handoff_plan.json和upload_request.json；先完成素材交接')
    if request.get('plan_sha256') != file_hash(job / 'handoff_plan.json'):
        raise ValueError('交接计划已改变，先重新核验交接，不重做媒体')
    if len(plan['timelines']) != 1:
        raise ValueError('仅支持一条竖版时间线')
    line = plan['timelines'][0]
    if line['canvas'] != {'width': 1080, 'height': 1920}:
        raise ValueError('OpenFilm知识模板需要1080×1920')
    assets = {}
    for item in request['assets']:
        aid = item['asset_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', aid) or aid in assets:
            raise ValueError('素材ID无效或重复')
        source = Path(item['path'])
        if not source.is_file() or file_hash(source) != item['sha256']:
            raise ValueError('素材缺失或哈希变化：' + aid)
        assets[aid] = item
    body, total = line['body_duration_us'], line['total_duration_us']
    end = 0
    for clip in line['frames']:
        if clip['start_us'] != end or clip['end_us'] <= end or clip['asset_id'] not in assets:
            raise ValueError('正文画面不连续或缺失')
        end = clip['end_us']
    if end != body or line['frames'][0]['end_us'] != 1_000_000:
        raise ValueError('正文时长或首秒封面错误')
    if line['audio']['start_us'] != 0 or line['audio']['end_us'] != body:
        raise ValueError('原声音轨起止错误')
    if line.get('outro') and (line['outro']['start_us'] != body or total != line['outro']['end_us']):
        raise ValueError('原交接片尾时长不一致')
    if not line.get('outro') and total != body:
        raise ValueError('无品牌片尾时总时长应等于正文时长')
    for cue in line['subtitles']:
        if not 1_000_000 <= cue['start_us'] < cue['end_us'] <= body:
            raise ValueError('字幕时码不符合首秒抑制与正文范围')
    return plan, request, line, assets


def sprite(source, target):
    from PIL import Image
    with Image.open(source) as im:
        w, h = im.size
        n = im.n_frames
        columns = math.ceil(math.sqrt(n))
        rows = math.ceil(n / columns)
        sheet = Image.new('RGBA', (w * columns, h * rows))
        durations = []
        bounds = []
        for i in range(n):
            im.seek(i)
            rgba = im.convert('RGBA')
            sheet.alpha_composite(rgba, ((i % columns) * w, (i // columns) * h))
            durations.append(max(10, int(im.info.get('duration', 100))))
            bounds.append(rgba.getchannel('A').getbbox())
        sheet.save(target)
    return {'width': w, 'height': h, 'columns': columns, 'rows': rows,
            'durations_ms': durations, 'alpha_bounds': bounds, 'source_sha256': file_hash(source)}


def title_page(text, style, font):
    from layout_html import text_page
    return text_page(text,style,font)


def playback_page(info):
    if info is None:
        return '''<!doctype html><style>html,body{margin:0;background:transparent}svg{display:block}</style>
<svg width="280" height="280" viewBox="0 0 280 280"><g id="disc" transform="rotate(0 140 140)"><circle cx="140" cy="140" r="128" fill="#171717" stroke="#565656" stroke-width="3"/><circle cx="140" cy="140" r="86" fill="none" stroke="#333" stroke-width="20"/><path d="M30 116L125 132L125 148L30 164Z" fill="#aaa" opacity=".5"/><circle cx="140" cy="140" r="42" fill="#e22"/><circle cx="140" cy="140" r="15" fill="#111"/></g></svg>
<script>window.film={width:280,height:280,frame(t){document.getElementById('disc').setAttribute('transform',`rotate(${(t%3)*120} 140 140)`);}};</script>'''
    w, h = info['width'], info['height']
    return f'''<!doctype html><style>html,body{{margin:0;background:transparent}}#sprite{{width:{w}px;height:{h}px;background:url('assets/playback-sprite.png');background-size:{w*info['columns']}px {h*info['rows']}px}}</style><div id="sprite"></div>
<script>const durations={json.dumps(info['durations_ms'])},total=durations.reduce((a,b)=>a+b,0);window.film={{width:{w},height:{h},ready:new Promise((resolve,reject)=>{{const i=new Image();i.onload=resolve;i.onerror=()=>reject(new Error('playback sprite failed to load'));i.src='assets/playback-sprite.png';}}),frame(t){{let ms=((t*1000)%total+total)%total,i=0;while(i<durations.length-1&&ms>=durations[i]){{ms-=durations[i++];}}document.getElementById('sprite').style.backgroundPosition=`${{-(i%{info['columns']})*{w}}}px ${{-Math.floor(i/{info['columns']})*{h}}}px`;}}}};</script>'''


def captions(line, font_name):
    box = line['safe_area']['slots_px']['subtitle']
    style = line['subtitle_style_reference']
    from layout_html import caption_css
    css = caption_css(style,font_name)
    lines = ['WEBVTT', '', 'NOTE Reviewed source timings. First second suppressed without shifting.', '', 'STYLE', css, '']
    for i, cue in enumerate(line['subtitles'], 1):
        settings = f"line:{(box[1]+box[3])/2/1920*100:.6f}%,center position:50%,center size:{(box[2]-box[0])/1080*100:.6f}% align:center"
        lines.extend([f'S{i:03}', f"{stamp(cue['start_us'])} --> {stamp(cue['end_us'])} {settings}", html.escape(cue['text'], quote=False), ''])
    return '\n'.join(lines) + '\n'


def build(job, project, options):
    plan, request, line, assets = checked_inputs(job)
    import layout_profile
    if options.get('body_image_mode','layered') != 'layered':
        raise ValueError('当前唯一布局要求原图独立分层，不能选择precomposed')
    mode='layered'
    line, assets = layout_profile.apply(job,line,assets)
    import caption_api
    line['caption_api']=caption_api.compile_timeline(line)
    profile=layout_profile.load()
    fonts={k:dict(v,project_name=Path(v['asset']).name) for k,v in profile['font_files'].items()}
    font=layout_profile.ROOT/fonts['display']['asset']
    sticker=layout_profile.ROOT/profile['playback']['asset'] if line['branding_enabled'] else None
    signature = digest({'version': VERSION, 'builder_sha256': file_hash(Path(__file__)),
                        'plan': file_hash(job/'handoff_plan.json'), 'request': file_hash(job/'upload_request.json'),
                        'layout_profile':layout_profile.binding(), 'compiler_sha256':file_hash(SCRIPT_DIR/'layout_profile.py'), 'html_sha256':file_hash(SCRIPT_DIR/'layout_html.py'), 'fonts':fonts, 'font': file_hash(font), 'sticker': file_hash(sticker) if sticker else None,
                        'equivalent': options.get('allow_equivalent_playback', False),
                        'body_image_mode': mode, 'layer_helper': file_hash(SCRIPT_DIR/'image_layers.py'),
                        'selected_assets': {k: v['sha256'] for k, v in assets.items()}})
    manifest_path = project/'knowledge-project.json'
    old = read_json(manifest_path)
    if old:
        if old['input_signature'] != signature:
            raise ValueError('输入或模板版本改变。使用新的--project-dir建立独立版本，保留现有工程及用户编辑')
        changed = [name for name, sha in old['generated_sha256'].items()
                   if not (project/name).is_file() or file_hash(project/name) != sha]
        return {**old, 'status': 'project_edited' if changed else 'project_created', 'changed_files': changed}
    if (project/'film.html').exists():
        raise ValueError('目标存在未由本后端管理的时间线，使用新的工程目录')
    (project/'assets').mkdir(parents=True, exist_ok=True)
    copied = {}
    for aid, item in assets.items():
        name = aid + Path(item['path']).suffix.lower()
        atomic_copy(Path(item['path']), project/'assets'/name)
        copied[aid] = 'assets/' + name
    generated=set(copied.values())
    for f in fonts.values():
        if f.get('embedding')!='local':
            atomic_copy(layout_profile.ROOT/f['asset'],project/'assets'/f['project_name'])
            generated.add('assets/'+f['project_name'])
    info = None
    if sticker:
        info = sprite(sticker, project/'assets/playback-sprite.png')
        generated.add('assets/playback-sprite.png')
    tracks = []
    def track(name, clips):
        tracks.append(f'<section id="track-{name}">\n' + '\n'.join(clips) + '\n</section>')
    def clip(tag, aid, start, end, box=None, src=None, extra='', fit='contain'):
        duration = end - start
        rect = box or [0,0,1080,1920]
        style = f'left:{rect[0]}px;top:{rect[1]}px;width:{rect[2]-rect[0]}px;height:{rect[3]-rect[1]}px'
        if tag in ('img','video'): style += ';object-fit:' + fit
        attrs = f'id="{aid}" src="{src or copied[aid]}#t=0,{seconds(duration)}" at="{seconds(start)}"'
        if tag != 'audio': attrs += f' style="{style}"'
        return f'<{tag} {attrs}{extra}>' + ('' if tag == 'img' else f'</{tag}>')
    track('captions', ['<track kind="captions" src="captions.vtt" srclang="zh-CN" label="原声字幕">'])
    for text in line['texts']:
        name = text['role'] + '.html'
        atomic_bytes(project/name, title_page(text['text'],text['style_reference'],fonts[text['style_reference']['pixel_style']['font']]).encode())
        generated.add(name)
        track(text['role'], [clip('iframe', text['role'], text['start_us'],text['end_us'],text['style_reference']['safe_box_px'],name)])
    elements = line.get('branding_elements', [])
    # Top-first: source above small album cover; small cover overlaps the rotating record as before.
    for role in ('source_label','album_cover'):
        for item in elements:
            if item['role'] == role:
                track(role,[clip('img',item['asset_id'],item['start_us'],item['end_us'],item.get('box_px'))])
    for item in elements:
        if item['kind'] == 'sticker':
            atomic_bytes(project/'playback.html',playback_page(info).encode());generated.add('playback.html')
            track('playback',[clip('iframe','playback',item['start_us'],item['end_us'],line['safe_area']['slots_px']['playback'],'playback.html')])
    if line.get('overlay'):
        item=line['overlay'];track('brand',[clip('img',item['asset_id'],item['start_us'],item['end_us'],item.get('box_px'))])
    pictures=[clip('img',x['asset_id'],x['start_us'],x['end_us'],x.get('box_px')) for x in line['frames']]
    if line.get('outro'):
        x=line['outro'];pictures.append(clip('video',x['asset_id'],x['start_us'],x['end_us'],x.get('box_px'),extra=' volume="1"'))
    track('pictures',pictures)
    if mode == 'layered':
        bg = line['album_background']
        track('background', [clip('img',bg['asset_id'],bg['start_us'],bg['end_us'],bg['box_px'],fit='cover')])
    item=line['audio'];track('original-audio',[clip('audio',item['asset_id'],0,item['end_us'],extra=' volume="1" speed="1"')])
    film='<!doctype html>\n<html><head><meta charset="utf-8"><meta name="viewport" content="width=1080, height=1920"></head><body>\n'+'\n'.join(tracks)+'\n</body></html>\n'
    atomic_bytes(project/'film.html',film.encode())
    atomic_bytes(project/'captions.vtt',captions(line,fonts['subtitle']).encode())
    generated.update(('film.html','captions.vtt'))
    atomic_json(project/'knowledge-timeline.json',line);generated.add('knowledge-timeline.json')
    manifest={'schema':'knowledge-openfilm/v1','status':'project_created','project_dir':str(project),
              'source_job':str(job),'source_version':plan['source_version'],'input_signature':signature,
              'draft_name':line['draft_name'],'duration_seconds':line['total_duration_us']/1e6,
              'body_duration_seconds':line['body_duration_us']/1e6,'fps':30,'canvas':line['canvas'],
              'safe_area':line['safe_area'],'subtitle_count':len(line['subtitles']),
              'body_image_mode':mode,'ignored_legacy_options':[k for k in ('font_file','sticker_gif','allow_equivalent_playback') if k in options],
              'source_assets':{k:{'source':v['path'],'sha256':v['sha256'],'project_path':copied[k]} for k,v in assets.items()},
              'fonts':fonts,'layout_profile':layout_profile.binding(),
              'playback':{'mode':('original_cached_frames' if sticker else 'equivalent_animation') if any(x['kind']=='sticker' for x in line.get('branding_elements',[])) else 'disabled',
                          'source':str(sticker) if sticker else None,'sprite':info},
              'generated_sha256':{name:file_hash(project/name) for name in sorted(generated)}}
    atomic_json(manifest_path,manifest)
    return manifest


def runtime(options):
    raw = options.get('project_dir')
    if not raw: raise ValueError('配置openfilm.project_dir为本机OpenFilm源码目录')
    root=Path(raw).expanduser().resolve()
    package=root/'packages/openfilm'
    if not (package/'src/render.mjs').is_file():
        if (root/'src/render.mjs').is_file():package=root
        else:raise ValueError('OpenFilm安装目录无效')
    env=os.environ.copy()
    if options.get('browser_cache'):env['PLAYWRIGHT_BROWSERS_PATH']=str(Path(options['browser_cache']).expanduser().resolve())
    if options.get('studio_home'):env['OPENFILM_HOME']=str(Path(options['studio_home']).expanduser().resolve())
    env['OPENFILM_NO_BROWSER']='1'
    return package,env


def invoke(project,options,action,extra=()):
    package,env=runtime(options)
    command=[options.get('node','node'),str(SCRIPT_DIR/'openfilm_runtime.mjs'),action,str(package),str(project),*map(str,extra)]
    result=subprocess.run(command,env=env,cwd=project)
    if result.returncode:raise RuntimeError(f'OpenFilm {action}失败（退出码{result.returncode}），保留工程和成功分段')


def current(job):
    result=read_json(job/'openfilm_result.json',{})
    if result.get('status') != 'render_verified':return None
    try:
        project=Path(result['project_dir']);receipt=read_json(project/'render-result.json')
        verification=read_json(project/'export-verification.json',{})
        if receipt.get('status') != 'render_verified' or verification.get('status') != 'render_verified':return None
        if result.get('output_sha256') != receipt.get('output_sha256') or verification.get('output_sha256') != receipt.get('output_sha256'):return None
        if result.get('request_sha256') != file_hash(job/'upload_request.json'):return None
        if result.get('plan_sha256') != file_hash(job/'handoff_plan.json'):return None
        if receipt['output_sha256'] != file_hash(Path(receipt['output'])):return None
        if any(file_hash(project/n)!=sha for n,sha in receipt['input_sha256'].items()):return None
        return result
    except (OSError,KeyError,ValueError,TypeError):return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir',type=Path,required=True)
    parser.add_argument('--project-dir',type=Path,required=True)
    parser.add_argument('--options',type=Path,help='JSON object of OpenFilm runtime/media options')
    parser.add_argument('--action',choices=['build','inspect','render','all'],default='build')
    parser.add_argument('--range',nargs=2,type=float,metavar=('FROM','TO'))
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--chunk-seconds',type=int,default=60)
    args=parser.parse_args();job=args.job_dir.resolve();project=args.project_dir.resolve()
    options=read_json(args.options,{}) if args.options else read_json(job/'config.json',{}).get('openfilm',{})
    if not 1<=args.workers<=4 or not 1<=args.chunk_seconds<=120:parser.error('workers须1～4，chunk-seconds须1～120')
    try:
        with file_lock(project/'.knowledge-build.lock',timeout=2):
            if args.action in ('build','all'):result=build(job,project,options)
            if args.action in ('inspect','all'):invoke(project,options,'inspect')
            if args.action in ('render','all'):
                extra=[args.workers,args.chunk_seconds]
                if args.range:extra+=args.range
                invoke(project,options,'render',extra)
                result=read_json(project/('sample-result.json' if args.range else 'render-result.json'))
            if args.action=='inspect':result=read_json(project/'inspection.json')
            result={**result,'backend':'openfilm','project_dir':str(project),
                    'request_sha256':file_hash(job/'upload_request.json'),'plan_sha256':file_hash(job/'handoff_plan.json')}
            atomic_json(job/'openfilm_result.json',result)
        print(json.dumps({k:result.get(k) for k in ('status','project_dir','output','backend')},ensure_ascii=False));return 0
    except Exception as exc:
        print(str(exc),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
