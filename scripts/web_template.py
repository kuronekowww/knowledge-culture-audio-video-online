"""Compile locked brand/layout/captions into the versioned Hyperframes foundation."""
import csv
import html
import json
from pathlib import Path
import shutil
from PIL import Image
import layout_profile
from artifacts import atomic_json, file_hash, read_json

ROOT = Path(__file__).resolve().parent.parent


def compile_project(job, project, cfg, runtime):
    import subtitle_review
    p=layout_profile.load(); copy=read_json(job/'copy.json'); body=subtitle_review.duration_us(subtitle_review.audio_path(job))/1e6
    brand=cfg.get('creative_options',{}).get('branding_enabled',True)
    duration=body+(p['outro']['duration_us']/1e6 if brand else 0)
    font_mode=cfg.get('web_fonts',{}).get('mode','local')
    if font_mode not in ('local','portable'): raise ValueError('web_fonts.mode须为local或portable')
    if font_mode == 'portable':
        raise ValueError('当前模板保留两种用户原字体。网页项目请用本机字体模式，并在接收端安装相同字体。')
    from team_fonts import resolve_all
    resolve_all()
    assets=project/'assets';assets.mkdir(exist_ok=True)
    stage_rect=read_json(ROOT/'assets/knowledge-templates/config/layout-binding.json')['content_rect']
    records={};warnings=[];missing=[]
    def asset(name, path):
        path=Path(path).expanduser()
        if not path.is_absolute(): path=job/path
        target=assets/(name+path.suffix)
        shutil.copy2(path,target)
        records[name]={'path':str(target.relative_to(project)), 'sha256':file_hash(target)}
        return records[name]['path']
    voice=asset('voice',subtitle_review.audio_path(job))
    configured=dict(cfg.get('web_assets',{}))
    if cfg.get('cover_policy_version') == 2:
        from cover_policy import validate_web_cover
        generated = job/'images/generated/cover_portrait.png'
        if not configured.get('cover') and generated.is_file():
            configured['cover'] = str(generated)
        if configured.get('cover'):
            validate_web_cover(job, cfg, job/Path(configured['cover']).expanduser())
    visual={}
    for key in ('cover','album_cover'):
        if configured.get(key): visual[key]=asset(key,configured[key])
        elif key!='album_cover': missing.append(key)
    if 'album_cover' not in visual: warnings.append('没有专辑小封面，保留专辑名和来源标识')
    import solid_frame
    stage_color = configured.get('stage_color',p['body_frame']['web_stage_color'])
    sources = configured.get('scene_samples', [])
    if not isinstance(sources,list):
        raise ValueError('web_assets.scene_samples 须为正文图片路径列表')
    scene_samples = [{'id':f'web-{i+1}','path':job/Path(value)} for i,value in enumerate(sources)]
    surround = solid_frame.materialize(job,scene_samples,[stage_color])
    visual['background'] = asset('background',surround['path'])
    css=['html,body{margin:0;width:1080px;height:1920px;overflow:hidden}',
         '#knowledge-root{position:relative;width:1080px;height:1920px;overflow:hidden;background:#e1e8eb}',
         '.clip{position:absolute;box-sizing:border-box;visibility:hidden}',
         '.locked-text{font-synthesis:none;white-space:pre;text-align:center}',
         '#brand-overlay{z-index:20;pointer-events:none}',
         '#scene-stage{overflow:hidden;font-size:40px;color:#142e42;display:grid;place-items:center;background:'+stage_color+'}',
         '.subtitle{display:flex;align-items:center;justify-content:center;text-align:center;background:transparent}',
         '.subtitle span{display:block;white-space:pre;text-align:center}']
    samples={};font_descriptors=[]
    for role,font in p['font_files'].items():
        family='Knowledge'+role.title()
        source=f'local({json.dumps(font["postscript"])})'
        warnings.append('接收电脑须安装原版字体：'+font['postscript'])
        css.append(f'@font-face{{font-family:{family};src:{source};font-weight:{font["weight"]};font-style:normal}}')
        samples[role]=''
        font_descriptors.append({'family':family,'weight':font['weight'],'role':role,'postscript':font['postscript']})
    layers=[]
    def layer(tag, id, start, end, attrs='', text='', classes=''):
        layers.append(f'<{tag} id="{id}" class="clip {classes}" data-start="{start}" data-duration="{end-start}" data-track-index="{0 if tag=="audio" else 2}" {attrs}>{text}</{tag}>')
    def image(id,src,start,end,style):
        layer('div',id,start,end,f'style="{style}"',f'<img id="{id}-image" src="{html.escape(src)}" style="width:100%;height:100%;object-fit:contain">')
    def box(box):
        x,y,r,b=box;return f'left:{x}px;top:{y}px;width:{r-x}px;height:{b-y}px;'
    if 'background' in visual:image('background',visual['background'],0,duration,'inset:0;width:1080px;height:1920px')
    layer('div','scene-stage',p['first_second']['text_start_us']/1e6,body,f'style="{box(stage_rect)}"')
    for role in ('title_line1','title_line2','album'):
        if role=='album' and not brand:continue
        value=layout_profile.album_display(copy) if role=='album' else copy[role]
        value,measurement=layout_profile.fit(value,role,p)
        style=p['texts'][role];samples[style['font']]+=value.replace('\n','')
        bounds=layout_profile.text_box(role,len(value.split('\n')),p)
        color=style.get('color') or layout_profile.album_color(Path(job)/configured['album_cover'] if configured.get('album_cover') else None,p,copy.get('album_accent_hex'))['hex']
        css.append(f'#{role}{{'+box(bounds)+f'font-family:Knowledge{style["font"].title()};font-weight:{style["weight"]};font-size:{style["font_size_px"]}px;line-height:{style["line_height"]};letter-spacing:{style["letter_spacing_px"]}px;color:{color};-webkit-text-stroke:{style["stroke_px"]}px {style["stroke_color"]};paint-order:stroke fill;text-align:{"left" if role=="album" else "center"};'+ '}')
        layer('div',role,p['first_second']['text_start_us']/1e6,body,f'data-max-width="{style["max_width_px"]}"',html.escape(value),'locked-text')
    with (job/'subtitles.csv').open(encoding='utf-8-sig',newline='') as stream: cues=list(csv.DictReader(stream))
    overrides=read_json(job/'subtitle_display.json',{});published=[]
    for cue in cues:
        a=subtitle_review.timestamp(cue['start'])/1e6;b=subtitle_review.timestamp(cue['end'])/1e6
        if a<0 or b<=a or b>body+.12:raise ValueError('字幕时码越界：'+cue['id'])
        text=overrides.get(cue['id'],cue['text'])
        if not isinstance(text,str) or text.replace('\n','')!=cue['text'].replace('\n',''):
            raise ValueError('展示换行不能改变字幕字词：'+cue['id'])
        text,_=layout_profile.fit(text,'subtitle',p)
        style=p['texts']['subtitle'];samples[style['font']]+=text.replace('\n','')
        shown=max(a,p['first_second']['text_start_us']/1e6)
        if shown<b:layer('div','cue-'+cue['id'],shown,b,'',f'<span data-max-width="{style["max_width_px"]}">{html.escape(text)}</span>','subtitle locked-text')
        published.append({'id':cue['id'],'start':a,'end':b,'text':cue['text'],'display_text':text})
    style=p['texts']['subtitle'];bounds=layout_profile.text_box('subtitle',2,p)
    css.append('.subtitle{'+box(bounds)+f'font-family:KnowledgeSubtitle;font-weight:{style["weight"]};font-size:{style["font_size_px"]}px;line-height:{style["line_height"]};letter-spacing:{style["letter_spacing_px"]}px;color:{style["color"]};-webkit-text-stroke:{style["stroke_px"]}px {style["stroke_color"]};paint-order:stroke fill;'+'}')
    if brand:
        import safe_layout
        overlay=assets/'brand-overlay.png'
        safe_layout.transparent_canvas(ROOT/p['brand_assets']['overlay'], overlay,
                                      (p['canvas']['width'],p['canvas']['height']), p['boxes_px']['overlay'],crop_alpha=True)
        records['brand-overlay']={'path':'assets/brand-overlay.png','sha256':file_hash(overlay)}
        image('brand-overlay','assets/brand-overlay.png',0,duration,'inset:0;width:1080px;height:1920px')
        theme=safe_layout.label_theme(project/visual['background'],{'light_background_luma_threshold':145},(p['canvas']['width'],p['canvas']['height']),p['boxes_px']['source_label']) if 'background' in visual else 'light'
        source_key='source_label_'+theme
        image('brand-source',asset('brand-source',ROOT/p['brand_assets'][source_key]),p['first_second']['source_label_start_us']/1e6,body,box(p['boxes_px']['source_label']))
        image('playback',asset('playback',ROOT/p['playback']['asset']),p['first_second']['text_start_us']/1e6,body,box(p['boxes_px']['playback']))
        if 'album_cover' in visual:image('album-cover',visual['album_cover'],p['first_second']['text_start_us']/1e6,body,box(p['boxes_px']['album_cover']))
        layer('video','brand-outro',body,duration,f'src="{asset("outro",ROOT/p["brand_assets"]["outro"])}" data-has-audio="true" preload="auto" style="{box(p["boxes_px"]["outro"])}"')
    if 'cover' in visual:image('cover',visual['cover'],0,p['first_second']['cover_duration_us']/1e6,'inset:0;width:1080px;height:1920px')
    layer('audio','voice',0,body,f'src="{voice}" preload="auto" data-volume="1"')
    for font in font_descriptors:
        role=font.pop('role');font['sample']=''.join(dict.fromkeys(samples[role])) or '字体'
    for item in ROOT.joinpath('assets/hyperframes-production').iterdir():
        if item.is_file():shutil.copy2(item,project/item.name)
    (project/'lib').mkdir();shutil.copy2(runtime['gsap_file'],project/'lib/gsap.min.js')
    for lib in ('hyperframes-player.global.js','hyperframe.runtime.iife.js'):
        shutil.copy2(Path(runtime['runtime_dir'])/'node_modules/hyperframes/dist'/lib,project/'lib'/lib)
    (project/'打开预览.command').chmod(0o755)
    data={'duration':duration,'bodyDuration':body,'body_start':p['first_second']['text_start_us']/1e6,'fontMode':font_mode,'fonts':font_descriptors,
          'warnings':warnings,'missing_assets':missing,'layout_binding':layout_profile.binding(),
          'body_frame':{'mode':'episode_solid','color':surround['color'],'stage_color':stage_color},
          'playback':{'mode':'static_png'} if brand else None}
    template=(project/'index.html').read_text()
    (project/'index.html').write_text(template.replace('__TITLE__',html.escape(copy.get('episode_title','知识原声视频'))).replace('__DURATION__',str(duration)).replace('__LAYERS__','\n'.join(layers)))
    (project/'layout.css').write_text('\n'.join(css));(project/'project-data.js').write_text('window.KNOWLEDGE_DATA='+json.dumps(data,ensure_ascii=False)+';\n')
    atomic_json(project/'project-data.json',data);atomic_json(project/'caption-display.json',published)
    atomic_json(project/'asset-manifest.json',records)
    atomic_json(project/'runtime-binding.json',{'version':'0.9.19','node':runtime['checks']['node']['executable'],'cli':runtime['hyperframes_cli'],'runtime_dir':runtime['runtime_dir'],'browser':(runtime['checks'].get('browser') or {}).get('executable')})
    return {'warnings':warnings,'missing_assets':missing,'body_seconds':body,'duration':duration,'font_mode':font_mode}
