"""Single source of pixel geometry, font binding and deterministic text fitting.

layout-profile.json is authoritative. spec.json contains content/workflow rules;
its legacy-shaped geometry is generated in memory, never maintained separately.
"""
from copy import deepcopy
from functools import lru_cache
import colorsys
import json
import math
from pathlib import Path

from PIL import Image, ImageFont, ImageOps
from artifacts import atomic_json, digest, file_hash, read_json

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / 'layout-profile.json'


def load():
    p = read_json(PROFILE)
    if p.get('schema') != 'knowledge-layout/v1' or p.get('units') != 'canvas_pixels_top_left_xyxy':
        raise ValueError('布局版本或单位不受支持')
    return p


def binding():
    p = load()
    return {'id': p['id'], 'sha256': file_hash(PROFILE)}


def check_assets():
    p = load()
    expected = dict(p['asset_sha256'])
    for f in p['font_files'].values():
        expected[f['asset']] = f['sha256']
        expected[f['coverage']] = f['coverage_sha256']
    for name, sha in expected.items():
        if not (ROOT/name).is_file() or file_hash(ROOT/name) != sha:
            raise ValueError('模板素材/字体缺失或版本不符，禁止回退：' + name)
    return expected


@lru_cache(maxsize=32)
def _font(asset, size, mtime):
    return ImageFont.truetype(str(ROOT/asset), size=size)


@lru_cache(maxsize=8)
def _coverage(path, mtime):
    return read_json(ROOT/path)


@lru_cache(maxsize=512)
def local_font_width(text, name, size):
    """Use the registered macOS font; its hash is checked before building."""
    import ctypes as c
    cf=c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    ct=c.CDLL('/System/Library/Frameworks/CoreText.framework/CoreText')
    cf.CFStringCreateWithCString.argtypes=[c.c_void_p,c.c_char_p,c.c_uint32];cf.CFStringCreateWithCString.restype=c.c_void_p
    ct.CTFontCreateWithName.argtypes=[c.c_void_p,c.c_double,c.c_void_p];ct.CTFontCreateWithName.restype=c.c_void_p
    ct.CTFontGetGlyphsForCharacters.argtypes=[c.c_void_p,c.c_void_p,c.c_void_p,c.c_long];ct.CTFontGetGlyphsForCharacters.restype=c.c_bool
    ct.CTFontGetAdvancesForGlyphs.argtypes=[c.c_void_p,c.c_uint32,c.c_void_p,c.c_void_p,c.c_long];ct.CTFontGetAdvancesForGlyphs.restype=c.c_double
    cf.CFRelease.argtypes=[c.c_void_p]
    family=cf.CFStringCreateWithCString(None,name.encode(),0x08000100);font=ct.CTFontCreateWithName(family,size,None)
    try:
        raw=text.encode('utf-16-le');n=len(raw)//2;chars=(c.c_uint16*n).from_buffer_copy(raw);glyphs=(c.c_uint16*n)()
        if not ct.CTFontGetGlyphsForCharacters(font,chars,glyphs,n):raise ValueError('本机字幕字体缺字')
        return ct.CTFontGetAdvancesForGlyphs(font,0,glyphs,None,n)
    finally:cf.CFRelease(font);cf.CFRelease(family)


def measure(text, role, p=None):
    p = p or load()
    style = p['texts'][role]
    f = p['font_files'][style['font']]
    runs = _coverage(f['coverage'], (ROOT/f['coverage']).stat().st_mtime_ns)
    missing = sorted({c for c in text if c != '\n' and not any(a <= ord(c) <= b for a,b in runs)})
    if missing:
        raise ValueError('指定字体缺字，不能静默回退：' + ''.join(missing))
    # CSS letter-spacing contributes once for each glyph, including the last.
    if f.get('embedding')=='local':
        width=lambda line:local_font_width(line,f['postscript'],style['font_size_px'])
    else:
        font = _font(f['asset'], style['font_size_px'], (ROOT/f['asset']).stat().st_mtime_ns)
        width=font.getlength
    return max((width(line) + len(line)*style['letter_spacing_px']
                for line in text.split('\n')), default=0)


def fit(text, role, p=None):
    p = p or load(); s = p['texts'][role]
    if not isinstance(text, str) or not text.strip() or any(c in text for c in '\r\u2028\u2029'):
        raise ValueError('文字为空或含非法换行：' + role)
    limit = s['max_width_px']; stroke = s['stroke_px']
    if role not in ('album','subtitle') and '\n' in text:
        raise ValueError('该文字层必须单行：' + role)
    if role in ('album','subtitle') and '\n' not in text and measure(text, role, p)+stroke > limit:
        candidates=[]
        for i in range(1,len(text)):
            a,b=text[:i],text[i:]
            if b[0] in '，。？！：；、）》」』' or a[-1] in '（《「『':continue
            width=measure(a+'\n'+b,role,p)+stroke
            if width<=limit:
                penalty=0 if a[-1] in '：:｜|—， ' else 20
                candidates.append((width+penalty,abs(len(a)-len(b)),i,a+'\n'+b))
        if not candidates:
            raise ValueError('专辑名需填写copy.album_display_name简称' if role=='album' else '字幕两行仍超宽：必须按原声真实词级时码拆分，禁止改写字幕')
        text=min(candidates)[-1]
    if len(text.split('\n'))>s['max_lines']:
        raise ValueError('行数超过模板限制：'+role)
    width=measure(text,role,p)+stroke
    if width>limit+0.01:
        raise ValueError(f'{role}实测{width:.2f}px，超过{limit}px；改写文案，不减字号或截字')
    sx=min(s['nominal_scale_x'],limit/width)
    return text, {'advance_px':width-stroke,'paint_width_px':width*sx,'scale_x':sx}


def text_box(role, line_count=1, p=None):
    p=p or load();s=p['texts'][role]
    height=s['font_size_px']*s['line_height']*line_count
    pad=s['stroke_px']/2
    if 'left_top_px' in s:
        x,y=s['left_top_px']
        return [x-pad,y-pad,x+s['max_width_px']-pad,y+height+pad]
    cx,cy=s['center_px']
    return [cx-s['max_width_px']/2,cy-height/2-pad,cx+s['max_width_px']/2,cy+height/2+pad]


def raw_position(cx,cy,canvas):
    return 2*cx-canvas['width'], canvas['height']-2*cy


def resolved_style(role, text, color=None, p=None):
    p=p or load();s=deepcopy(p['texts'][role]);text,measurement=fit(text,role,p)
    box=text_box(role,len(text.split('\n')),p)
    # API accepts integer UI sizes. Preserve exact pixel size through derived scale.
    api_size=math.ceil(s['font_size_px']/4)
    scale=s['font_size_px']/(api_size*4)
    width=measurement['paint_width_px']
    cx=(box[0]+box[2])/2 if role!='album' else s['left_top_px'][0]+(width-s['stroke_px'])/2
    cy=(box[1]+box[3])/2
    x,y=raw_position(cx,cy,p['canvas'])
    f=p['font_files'][s['font']]
    return text, {'font_size':api_size,'font':None,'text_color':color or s.get('color','#ffffff'),
        'border_color':s['stroke_color'],'bold':False,'underline':False,'alignment':0 if role=='album' else 1,
        'letter_spacing':s['letter_spacing_px']/s['font_size_px']/.05,
        'line_spacing':(s['line_height']-1-.02)/.05,
        'x':x,'y':y,'scale_x':scale*measurement['scale_x'],'scale_y':scale,'alpha':1,
        'safe_box_px':box, 'font_file':f['asset'],'font_sha256':f['sha256'],
        'font_postscript':f['postscript'], 'layout_profile':binding(),
        'pixel_style':{**s,'color':color or s.get('color','#ffffff'),'resolved_scale_x':measurement['scale_x']},
        'measurement':measurement}


def album_color(cover, p=None, explicit=None):
    p=p or load();policy=p['album_color_policy']
    if cover is None:
        import re
        if not isinstance(explicit,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',explicit):
            raise ValueError('无真实专辑封面时须明确copy.album_accent_hex，不得猜测封面调性')
        return {'hex':explicit.lower(),'source':'explicit_no_cover','policy_sha256':digest(policy)}
    with Image.open(cover) as im:
        rgb=ImageOps.exif_transpose(im).convert('RGB').resize(tuple(policy['resize']),Image.Resampling.BOX)
    bins={}
    for r,g,b in (rgb.get_flattened_data() if hasattr(rgb,'get_flattened_data') else rgb.getdata()):
        h,s,v=colorsys.rgb_to_hsv(r/255,g/255,b/255)
        if s<policy['saturation_min'] or v<policy['value_min']:continue
        k=int(h*360//policy['hue_bin_degrees']);weight=s*v
        bucket=bins.setdefault(k,[0,0,0]);bucket[0]+=weight;bucket[1]+=h*weight;bucket[2]+=1
    if not bins:
        result='#ffffff'
    else:
        k=max(sorted(bins),key=lambda n:bins[n][0]);weight,h,_=bins[k]
        rgb=colorsys.hsv_to_rgb(h/weight,policy['output_saturation'],policy['output_value'])
        result='#'+''.join(f'{round(c*255):02x}' for c in rgb)
    return {'hex':result,'cover_sha256':file_hash(Path(cover)),'algorithm':policy['algorithm'],
            'policy_sha256':digest(policy),'source':'deterministic_cover_analysis'}


def compile_spec(base):
    p=load();out=deepcopy(base);w,h=p['canvas'].values()
    safe=deepcopy(out.get('portrait',{}).get('safe_area',{}))
    slots=deepcopy(p['boxes_px'])
    slots.update({r:text_box(r,p['texts'][r]['max_lines'],p) for r in p['texts']})
    slots['story']=[0,0,w,h]
    l,t,r,b=p['cover_safe_rect_px']
    safe.update(profile=p['id'],rect_px=[l,t,r,b],scope='cover_only; video uses slots_px',
        margins_px={'left':l,'top':t,'right':w-r,'bottom':h-b},
        center_px=[(l+r)/2,(t+b)/2],slots_px=slots)
    out['portrait']={'canvas':p['canvas'],'safe_area':safe,'texts':{},'images':{
        'cover':{'width':w,'height':h,'fit':'full_canvas_no_inset','content_safe_rect_px':[l,t,r,b],'layout_review_required':True},
        'background':{'width':w,'height':h,'source':'album_shared_background'},'foreground':p['body_images']}}
    for role in p['texts']:
        out['portrait']['texts'][role]=resolved_style(role,'示例',p=p)[1]
    branding=out.setdefault('branding',{});branding.update(overlay={'portrait':p['brand_assets']['overlay']},
        outro={'portrait':p['brand_assets']['outro']})
    style={k:1 for k in ('scale_x','scale_y','alpha')};style.update(x=0,y=0)
    box=slots['playback'];x,y=raw_position((box[0]+box[2])/2,(box[1]+box[3])/2,p['canvas'])
    scale=(box[2]-box[0])/p['playback']['native_canvas_width_px']
    branding['elements']={'album_cover':{**style,'source':'current_album_reference','slot':'album_cover'},
        'source_label':{**style,'text':'内容来源｜喜马拉雅APP','dark_asset':p['brand_assets']['source_label_dark'],
          'light_asset':p['brand_assets']['source_label_light'],'slot':'source_label','light_background_luma_threshold':145},
        'playback':{**style,'sticker_id':p['playback']['sticker_id'],'x':x,'y':y,'scale_x':scale,'scale_y':scale}}
    out['timing'].update(cover_seconds=p['first_second']['cover_duration_us']/1e6,outro_seconds=p['outro']['duration_us']/1e6)
    out['presentation_policy'].update(text_start_us=p['first_second']['text_start_us'],source_label_start_us=p['first_second']['source_label_start_us'],
        upper_title_max_width_px=p['texts']['title_line1']['max_width_px'])
    out['layout_profile_binding']=binding()
    return out


def asset_row(aid,path,kind,**kwargs):
    path=Path(path).resolve()
    row={'asset_id':aid,'path':str(path),'kind':kind,'sha256':file_hash(path),'bytes':path.stat().st_size,**kwargs}
    if kind=='image':
        with Image.open(path) as im:row.update(width=im.width,height=im.height)
    return row


def apply(job,line,assets,copy_text=None,album_cover=None):
    """Upgrade a reviewed handoff in memory; do not modify user projects/media."""
    from image_layers import for_openfilm
    check_assets();p=load()
    line,assets=for_openfilm(Path(job),line,assets)
    text=copy_text or read_json(Path(job)/'copy.json')
    if album_cover is None:
        cfg=read_json(Path(job)/'config.json',{})
        override=read_json(Path(job)/'presentation-assets.json',{})
        if not override and cfg.get('album_id'):
            import album_visuals
            override=read_json(album_visuals.root(cfg)/'presentation-assets.json',{})
            if override and override.get('album_id')!=str(cfg['album_id']):raise ValueError('共享呈现资产属于其他专辑')
            if override and override.get('base_reference_sha256')!=album_visuals.current(Path(job),cfg).get('reference_sha256'):
                raise ValueError('共享呈现封面绑定的原始封面已变；须明确更新该封面版本')
        if override.get('album_cover'):
            q=override['album_cover'];album_cover=Path(q['path'])
            if file_hash(album_cover)!=q['sha256']:raise ValueError('手选专辑封面哈希变化')
        else:
            import album_visuals
            state=album_visuals.current(Path(job),read_json(Path(job)/'config.json',{}))
            album_cover=album_visuals.album_cover_path(state)
    color=album_color(album_cover,p,text.get('album_accent_hex'))
    spec=compile_spec(read_json(ROOT/'spec.json'))
    line.update(layout_profile=binding(),safe_area=spec['portrait']['safe_area'],presentation_policy=spec['presentation_policy'])
    for row in line['texts']:
        role=row['role'];value=text.get('album_display_name',text['album_name']) if role=='album' else text[role]
        row['text'],row['style_reference']=resolved_style(role,value,color['hex'] if role=='album' else None,p)
    # All cues share one style; measurement remains per cue and rejects overflow/missing glyphs.
    for cue in line['subtitles']:
        source=cue.get('source_text',cue['text'])
        cue['text']=fit(source,'subtitle',p)[0]
        if cue['text']!=source:cue['source_text']=source
    line['subtitle_style_reference']=resolved_style('subtitle',line['subtitles'][0]['text'],p=p)[1]
    line['album_color_analysis']=color
    line['episode_title']=text.get('episode_title',line.get('episode_title'))
    body=line['body_duration_us'];end=body+(p['outro']['duration_us'] if line['branding_enabled'] else 0)
    line['total_duration_us']=end
    line['album_background']['end_us']=end
    if line['branding_enabled']:
        import branding_assets
        elements=[branding_assets.playback_sticker(spec['branding']['elements']['playback'],body)]
        line['album_cover_available']=album_cover is not None
        if album_cover:
            aid='portrait__album_cover';assets[aid]=asset_row(aid,album_cover,'image')
            elements.append({'role':'album_cover','kind':'image','asset_id':aid,'start_us':p['first_second']['text_start_us'],'end_us':body,'box_px':p['boxes_px']['album_cover'],'style_reference':{'x':0,'y':0,'scale_x':1,'scale_y':1,'alpha':1}})
        import safe_layout
        theme=safe_layout.label_theme(assets[line['album_background']['asset_id']]['path'],spec['branding']['elements']['source_label'],(p['canvas']['width'],p['canvas']['height']),p['boxes_px']['source_label'])
        aid='portrait__source_label';assets[aid]=asset_row(aid,ROOT/p['brand_assets']['source_label_'+theme],'image')
        elements.append({'role':'source_label','kind':'image','asset_id':aid,'start_us':p['first_second']['source_label_start_us'],'end_us':body,'box_px':p['boxes_px']['source_label'],'style_reference':{'x':0,'y':0,'scale_x':1,'scale_y':1,'alpha':1}})
        line['branding_elements']=elements;line['source_label_theme']=theme
        line['overlay']['end_us']=end
        import subprocess
        media=ROOT/p['brand_assets']['outro']
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(media)]))
        video=next(s for s in probe['streams'] if s['codec_type']=='video')
        if abs(round(float(probe['format']['duration'])*1e6)-p['outro']['duration_us'])>1000:raise ValueError('片尾实际时长与唯一配置不一致')
        aid='portrait__outro';assets[aid]=asset_row(aid,media,'video',width=video['width'],height=video['height'],duration_us=p['outro']['duration_us'])
        line['outro']={'asset_id':aid,'start_us':body,'end_us':end,'source_start_us':0,'volume':1,'preserve_original_audio':True,'box_px':p['boxes_px']['outro']}
    return line,assets


def media_transform(item,assets,canvas):
    box=item.get('box_px')
    if not box:return {'alpha':1,'scale_x':1,'scale_y':1,'transform_x':0,'transform_y':0}
    a=assets[item['asset_id']]
    width,height=a.get('width'),a.get('height')
    if not width or not height:
        with Image.open(a['path']) as im:width,height=im.size
    natural=min(canvas['width']/width,canvas['height']/height)
    fit_fn=max if item.get('fit')=='cover' else min
    shown=fit_fn((box[2]-box[0])/width,(box[3]-box[1])/height)
    x,y=raw_position((box[0]+box[2])/2,(box[1]+box[3])/2,canvas)
    return {'alpha':1,'scale_x':shown/natural,'scale_y':shown/natural,'transform_x':x,'transform_y':y}


def api_media_transform(item,assets,canvas):
    value=media_transform(item,assets,canvas)
    # add_images schema requires integers. The local finalizer restores subpixels.
    return {**value,'transform_x':round(value['transform_x']),'transform_y':round(value['transform_y'])}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--copy',type=Path)
    parser.add_argument('--resolved-spec',type=Path)
    args=parser.parse_args();check_assets();result={'profile':binding(),'assets':'verified'}
    if args.copy:
        value=read_json(args.copy);result['text_fit']={r:fit(value['album_name' if r=='album' else r],r) for r in ('album','title_line1','title_line2')}
    if args.resolved_spec:atomic_json(args.resolved_spec,compile_spec(read_json(ROOT/'spec.json')))
    print(json.dumps(result,ensure_ascii=False))
