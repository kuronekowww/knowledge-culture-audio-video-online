"""Read-only verification of downloaded, saved draft JSON and its actual media."""
import argparse
import json
import math
import subprocess
from pathlib import Path

from PIL import Image
from artifacts import atomic_json, digest, file_hash, read_json


def near(a, b, tolerance=1e-6):
    return (isinstance(a,(int,float)) and not isinstance(a,bool)
            and math.isfinite(a) and abs(a-b)<=tolerance)


def color(hex_value):
    return [int(hex_value[i:i+2],16)/255 for i in (1,3,5)]


def rgb_equal(a, b):
    return isinstance(a,list) and len(a)==3 and all(near(x,y,.005) for x,y in zip(a,b))


def load_draft(path):
    path=Path(path).resolve()
    candidates=[path] if path.is_file() else [path/'draft_content.json',path/'draft_info.json']
    available=[]
    for candidate in candidates:
        if candidate.is_file():
            try:
                data=read_json(candidate)
                if not isinstance(data,dict) or not isinstance(data.get('tracks'),list):
                    raise ValueError('缺少tracks')
                available.append((candidate,data))
            except (ValueError,UnicodeError):
                raise ValueError('草稿为内部/加密格式或无效JSON；需取得保存接口的原始JSON，不改写源文件') from None
    if not available:raise ValueError('缺少保存后的草稿JSON')
    if any(data!=available[0][1] for _,data in available[1:]):
        raise ValueError('draft_content与draft_info不一致，请明确最新保存产物')
    return available[0]


def local_media(raw, root):
    # Server paths are data, not permission to open arbitrary worker files.
    value=Path(raw)
    direct=value if value.is_absolute() else root/value
    direct=direct.resolve()
    if direct.is_relative_to(root) and direct.is_file():return direct
    basename=raw.replace('\\','/').rsplit('/',1)[-1]
    matches=[p.resolve() for p in root.rglob('*') if p.is_file() and p.name==basename
             and p.resolve().is_relative_to(root)]
    if len(matches)!=1:raise ValueError('媒体缺失或同名歧义：'+basename)
    return matches[0]


def verify(timeline, assets, draft_path):
    path,draft=load_draft(draft_path)
    root=path.parent
    hashes={}
    def sha_of(p):
        key=str(p)
        if key not in hashes:hashes[key]=file_hash(Path(p))
        return hashes[key]
    errors=[];evidence={}
    def check(ok, message):
        if not ok:errors.append(message)
    canvas=timeline['canvas']
    check(all(draft.get('canvas_config',{}).get(k)==v for k,v in canvas.items()),'画布尺寸与交接不一致')
    check(near(draft.get('duration'),timeline['total_duration_us'],1000),'草稿总时长与交接不一致')
    materials={}
    for rows in draft.get('materials',{}).values():
        if isinstance(rows,list):
            for row in rows:
                if isinstance(row,dict) and 'id' in row:materials[row['id']]=row
    actual=[]
    for track in draft['tracks']:
        for seg in track.get('segments',[]):
            mat=materials.get(seg.get('material_id'),{})
            kind=track.get('type')
            item={'segment':seg,'material':mat,'track':track,'kind':kind}
            check(not track.get('mute',False) and not track.get('is_muted',False)
                  and not (int(track.get('attribute',0)) & 1),'存在静音轨道')
            if kind=='text':
                try:item['content']=json.loads(mat['content'])
                except (ValueError,KeyError,TypeError):item['content']={};errors.append('文字内容不是可读JSON')
            elif kind=='sticker':
                item['sticker_id']=str(mat.get('sticker_id',''))
                check(mat.get('type')=='sticker' and str(mat.get('resource_id',''))==item['sticker_id'],'贴纸素材类型或ID不一致')
            elif kind in ('video','audio'):
                item['kind']='image' if mat.get('type')=='photo' else kind
                try:
                    media=local_media(mat['path'],root)
                    sha=sha_of(media);evidence[str(media)]=sha
                    item.update(sha256=sha,path=media)
                except (ValueError,OSError,KeyError,TypeError) as exc:errors.append('素材无法核对：'+str(exc))
            else:errors.append('出现交接未定义的非空轨道：'+str(kind))
            actual.append(item)
    import layout_profile
    check(timeline.get('layout_profile')==layout_profile.binding(),'模板绑定不是当前唯一配置')
    expected=[]
    for frame in timeline['frames']:
        expected.append({**frame,'kind':'image','role':'frame','style':layout_profile.media_transform(frame,assets,canvas)})
    bg=timeline.get('album_background',{})
    if bg.get('asset_id'):expected.append({**bg,'kind':'image','role':'background','style':layout_profile.media_transform(bg,assets,canvas)})
    expected.append({**timeline['audio'],'kind':'audio','role':'audio'})
    for role,kind in (('overlay','image'),('outro','video')):
        if timeline.get(role):expected.append({**timeline[role],'kind':kind,'role':role,'style':layout_profile.media_transform(timeline[role],assets,canvas)})
    for element in timeline.get('branding_elements',[]):
        style=element['style_reference']
        normalized_style={'transform_x':style['x'],'transform_y':style['y'],'scale_x':style['scale_x'],'scale_y':style['scale_y']}
        if element.get('box_px'):normalized_style=layout_profile.media_transform(element,assets,canvas)
        expected.append({**element,'style':normalized_style})
    for request in timeline['caption_api']['requests']:
        for cue in json.loads(request['body']['captions']):
            expected.append({'kind':'text','role':request['role'],'text':cue['text'],
                             'start_us':cue['start'],'end_us':cue['end'],'style':{**request['body'],**request.get('verification',{})}})
    check(len(actual)==len(expected),f'片段数量不符：实际{len(actual)}，应为{len(expected)}')
    unused=set(range(len(actual)));layers={}
    for want in expected:
        def identity(item):
            return (item['kind']==want['kind'] and
                (item.get('content',{}).get('text')==want['text'] if want['kind']=='text'
                 else item.get('sticker_id')==want['sticker_id'] if want['kind']=='sticker'
                 else item.get('sha256')==assets[want['asset_id']]['sha256']))
        candidates=[i for i in sorted(unused) if identity(actual[i])]
        candidates.sort(key=lambda i:abs(actual[i]['segment'].get('target_timerange',{}).get('start',-10**12)-want['start_us']))
        label=want['role']+':'+want.get('text',want.get('asset_id',want.get('sticker_id','')))
        if not candidates:
            errors.append('缺少匹配片段（文字、贴纸ID或素材哈希）：'+label);continue
        index=candidates[0];unused.remove(index);got=actual[index]
        seg,mat=got['segment'],got['material']
        target=seg.get('target_timerange',{})
        duration=want['end_us']-want['start_us']
        check(near(target.get('start'),want['start_us'],1000) and near(target.get('duration'),duration,1000),'时码偏移/时长错误：'+label)
        check(near(seg.get('speed',1),1),'不应再次变速：'+label)
        if want['kind'] in ('audio','video'):
            source=seg.get('source_timerange',{})
            check(near(source.get('start'),0,1000) and near(source.get('duration'),duration,1000),'音视频源区间错误：'+label)
            check(near(seg.get('volume'),want.get('volume',1)),'原声音量错误：'+label)
        if want['kind']!='audio':
            clip=seg.get('clip') or {};transform=clip.get('transform',{});scale=clip.get('scale',{})
            style=want.get('style',{})
            check(near(transform.get('x'),style.get('transform_x',0)/canvas['width'])
                  and near(transform.get('y'),style.get('transform_y',0)/canvas['height']),'位置错误：'+label)
            check(near(scale.get('x'),style.get('scale_x',1)) and near(scale.get('y'),style.get('scale_y',1)),'缩放错误：'+label)
            check(near(clip.get('alpha',1),1) and near(clip.get('rotation',0),0)
                  and not any(clip.get('flip',{}).values()),'透明度/旋转/翻转错误：'+label)
            check(not seg.get('common_keyframes'),'存在交接未定义的运镜/文字关键帧：'+label)
            layers.setdefault(want['role'],[]).append(seg.get('render_index',got['track'].get('render_index',0)))
        if want['kind']=='text':
            style=want['style'];styles=got.get('content',{}).get('styles',[])
            check(bool(styles),'缺少文字样式：'+label)
            check(mat.get('alignment')==style['alignment'],'对齐错误：'+label)
            check(near(mat.get('letter_spacing',0),style.get('letter_spacing',0)*.05),'字间距错误：'+label)
            check(near(mat.get('global_alpha',1),style.get('alpha',1)),'文字不透明度错误：'+label)
            if style.get('pixel_style'):
                check(near(mat.get('font_size'),style['font_size']),'文字素材字号错误：'+label)
                px=style['pixel_style']
                if 'jianying_max_width_px' in px:
                    check(near(mat.get('line_max_width'),px['jianying_max_width_px']/canvas['width']),'剪映文字宽度错误：'+label)
                if px.get('jianying_force_width'):
                    check(mat.get('force_apply_line_max_width') is True,'未固定指定文字框宽度：'+label)
                if px.get('jianying_scale_x') == px.get('jianying_scale_y') == 1.0:
                    check(near(seg.get('uniform_scale',{}).get('value'),1.0),'文字统一缩放不是100%：'+label)
            for part in styles:
                check(near(part.get('size'),style['font_size']),'字号错误：'+label)
                if style.get('single_line'):check(not any(c in got['content'].get('text','') for c in ('\n','\r','\u2028','\u2029')),'标题出现换行：'+label)
                check(part.get('bold',False)==style['bold'],'加粗错误：'+label)
                check(part.get('underline',False)==style.get('underline',False),'下划线错误：'+label)
                if style.get('font_file'):
                    bound=Path(part.get('font',{}).get('path',''))
                    check(mat.get('font_path')==str(bound) and mat.get('font_name')==style.get('font_postscript'),
                          '材料字体与内嵌样式或指定字体不一致：'+label)
                    check(bound.is_file() and sha_of(bound)==style['font_sha256'],'实际字体文件未正确绑定：'+label)
                    if bound.is_file():evidence[str(bound)]=sha_of(bound)
                if style.get('pixel_style'):
                    px=style['pixel_style'];stroke=part.get('strokes',[{}])[0]
                    check(near(stroke.get('width'),px['stroke_px']/px['font_size_px']),'像素描边映射错误：'+label)
                    check(near(stroke.get('content',{}).get('solid',{}).get('alpha'),px['stroke_alpha']),'描边透明度错误：'+label)
                if style.get('font_resource_id'):
                    check(str(part.get('font',{}).get('id'))==style['font_resource_id'],'指定字体错误：'+label)
                check(rgb_equal(part.get('fill',{}).get('content',{}).get('solid',{}).get('color'),color(style['text_color'])),'文字颜色错误：'+label)
                strokes=part.get('strokes',[])
                check(bool(strokes) and rgb_equal(strokes[0].get('content',{}).get('solid',{}).get('color'),color(style['border_color'])),'描边错误：'+label)
                if 'border_width_ui' in style:
                    check(bool(strokes) and near(strokes[0].get('width'),style['border_width_ui']/100*.2),'描边粗细错误：'+label)
                    check(bool(strokes) and near(strokes[0].get('content',{}).get('solid',{}).get('alpha'),style['border_alpha']),'描边不透明度错误：'+label)
                check(not part.get('effectStyle'),'存在覆盖样式的花字：'+label)
        elif want['kind']=='image':
            with Image.open(got['path']) as image:
                if want['role']=='frame':
                    if want.get('shot_id'):check(image.width>image.height and want.get('box_px',[None])[0]==0,'正文必须为原始横图全宽独立图层：'+label)
                    else:check(image.size==(canvas['width'],canvas['height']),'封面不是整幅画布：'+label)
                elif want['role']=='playback':
                    check(image.n_frames==1 and image.mode=='RGBA' and not got['segment'].get('common_keyframes')
                          and not got['segment'].get('keyframe_refs'),'唱片必须是无关键帧的单帧透明图片')
                elif want['role']=='background' and bg.get('mode')=='episode_solid':
                    from PIL import ImageColor
                    check(image.mode=='RGB' and image.getextrema()==tuple((v,v) for v in ImageColor.getrgb(bg['color'])),
                          '包框素材必须为本集选定的单一纯色')
                elif want['role']=='overlay':check(image.size==(canvas['width'],canvas['height']),'品牌贴片尺寸错误：'+label)
                elif want['role'] in ('album_cover','source_label'):check(image.width>0 and image.height>0,'品牌图片无效')
                if timeline.get('safe_area') and want['role'] in ('overlay','album_cover','source_label') and not want.get('box_px'):
                    check(image.size==(canvas['width'],canvas['height']),'安全区派生图片必须为整画布：'+label)
                    check(image.mode=='RGBA','安全区派生图片须保留透明通道：'+label)
                    if image.mode=='RGBA':
                        bounds=image.getchannel('A').getbbox()
                        box=timeline['safe_area']['slots_px'][want['role']]
                        check(bool(bounds) and box[0]<=bounds[0]<bounds[2]<=box[2]
                              and box[1]<=bounds[1]<bounds[3]<=box[3], '品牌可见像素超出安全区域：'+label)
        elif want['kind']=='video':
            result=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(got['path'])],capture_output=True,text=True,timeout=30)
            info=json.loads(result.stdout) if result.returncode==0 else {}
            streams=info.get('streams',[])
            check({'audio','video'}<={s.get('codec_type') for s in streams},'片尾素材缺音轨或视频轨')
            check(float(info.get('format',{}).get('duration',0))*1e6>=duration-1000,'片尾素材长度不足')
            video=next((s for s in streams if s.get('codec_type')=='video'),{})
            check(bool(video.get('height')) and bool(want.get('box_px')),'片尾须保留原素材并按独立图层定位')
    if unused:errors.append(f'有{len(unused)}个未匹配/重复片段')
    if layers.get('background'):
        check(max(layers['background'])<min(layers.get('frame',[0])),'独立底图必须位于正文下方')
    if layers.get('overlay') and layers.get('frame'):
        check(min(layers['overlay'])>max(layers['frame']),'品牌贴片位于画面下方')
    if layers.get('playback') and layers.get('album_cover'):
        check(max(layers['playback'])<min(layers['album_cover']),'唱片图层必须在专辑封面下方')
    if layers.get('source_label'):
        check(min(layers['source_label'])>max(layers.get('playback',[0])+layers.get('album_cover',[0])),
              '来源图片必须在唱片与专辑小封面上方')
    for role in ('playback','album_cover','source_label'):
        if layers.get(role):check(min(layers[role])>max(layers.get('frame',[0])+layers.get('overlay',[0])),'专辑品牌组件位于正文/贴片下方：'+role)
    base=max(layers.get('frame',[0])+layers.get('overlay',[0])+layers.get('playback',[0])+layers.get('album_cover',[0])+layers.get('source_label',[0]))
    for role in ('album','title_line1','title_line2','subtitle','cta','notice'):
        if layers.get(role):check(min(layers[role])>base,'文字位于画面或贴片下方：'+role)
    return {'status':'passed' if not errors else 'failed','errors':errors,
            'draft_json':str(path),'draft_sha256':sha_of(path),'media_sha256':evidence,
            'draft_files_sha256':{str(p):sha_of(p) for p in
                ([path] if Path(draft_path).is_file() else [root/'draft_content.json',root/'draft_info.json']) if p.is_file()},
            'expected_segments':len(expected),'actual_segments':len(actual),
            'verification_scope':'saved_json_and_media; not rendered_visual_review'}


def report_current(job, orientation, payload):
    result=read_json(job/('draft_verification.'+orientation+'.json'),{})
    line=next(t for t in payload['timelines'] if t['orientation']==orientation)
    try:
        return (result.get('status')=='passed' and result.get('source_version')==payload['source_version']
                and result.get('handoff_sha256')==file_hash(job/'capcut_handoff.json')
                and result.get('timeline_hash')==digest(line)
                and result.get('verifier_sha256')==file_hash(Path(__file__))
                and result['draft_sha256']==file_hash(Path(result['draft_json']))
                and bool(result.get('draft_files_sha256'))
                and all(file_hash(Path(p))==sha for p,sha in result['draft_files_sha256'].items())
                and bool(result.get('media_sha256'))
                and all(file_hash(Path(p))==sha for p,sha in result['media_sha256'].items()))
    except (OSError,KeyError,TypeError):return False


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job-dir',required=True,type=Path)
    p.add_argument('--orientation',required=True,choices=['portrait'])
    p.add_argument('--draft',required=True,type=Path)
    a=p.parse_args();job=a.job_dir.resolve()
    try:
        import handoff
        value=handoff.validate(job,read_json(job/'config.json'))
        line=next(t for t in value['timelines'] if t['orientation']==a.orientation)
        result=verify(line,value['assets'],a.draft)
        result.update(source_version=value['source_version'],handoff_sha256=file_hash(job/'capcut_handoff.json'),
                      timeline_hash=digest(line),orientation=a.orientation,verifier_sha256=file_hash(Path(__file__)))
    except (ValueError,OSError,KeyError,TypeError,AttributeError,StopIteration,subprocess.SubprocessError) as exc:
        result={'status':'unverified','errors':[str(exc)],'orientation':a.orientation}
    target=job/('draft_verification.'+a.orientation+'.json');atomic_json(target,result)
    print(json.dumps({'status':result['status'],'errors':result['errors'],'report':str(target)},ensure_ascii=False))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
