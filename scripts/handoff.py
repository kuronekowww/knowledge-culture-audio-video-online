"""Prepare reviewed assets and publish a version-bound, URL-only editing contract.

All editing bodies are compiled here; the downstream installed Skill executes
them in order and downloads the saved draft for structural verification.
"""
import argparse
import copy
import hashlib
import ipaddress
import json
import math
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat
import config
import caption_api
import creative_options
import assembly_api
import album_visuals
import job_versions
import production_checks
import subtitle_review
import safe_layout
from artifacts import atomic_json, digest, file_hash, file_lock, read_json

ADAPTER_VERSION = 16
DIRECTIONS = ('portrait',)


def source_fingerprint(job, cfg):
    files = ('config.json', 'cut_plan.json', 'cut_result.json', 'cut_review_response.json',
             'highlight_identity.json', 'storyboard.json', 'copy.json', 'subtitles.csv',
             'final_transcript.json', 'final_transcript.meta.json', 'subtitle_review_response.json',
             'storyboard_timing_review.json', 'image_manifest.json', 'image_review.json',
             'album_name_review.json', 'visual_bible.json', 'cover_review.json', 'presentation-assets.json')
    audio = subtitle_review.audio_path(job) if (job/'cut_result.json').is_file() else None
    return digest({'adapter': ADAPTER_VERSION, 'files': {n:job_versions.optional_hash(job/n) for n in files},
                   'audio':job_versions.optional_hash(audio) if audio else None,
                   'images':job_versions.image_assets(job), 'spec':config.load_spec(),
                   'album_background':album_visuals.background_state(job,cfg),
                   'presentation_assets':read_json(album_visuals.root(cfg)/'presentation-assets.json',{}),
                   'dependencies':job_versions.build_dependencies(),
                   'terms':job_versions.optional_hash(config.SKILL_DIR/'references/rejected-terms.csv')})


def prepared_fingerprint(job):
    request=read_json(job/'upload_request.json',{})
    return digest({'request':request,'plan':read_json(job/'handoff_plan.json',{}),
                   'bytes':{x['asset_id']:job_versions.optional_hash(Path(x['path'])) for x in request.get('assets',[])}})


def gates(job, cfg):
    import run
    import title_review
    title_review.require(job, cfg)
    identity=production_checks.identity(job)
    job_versions.validate_story_images(job)
    if not production_checks.cut_ready(job,cfg): raise ValueError('切点复核缺失或已过期')
    if not production_checks.storyboard_ready(job): raise ValueError('分镜尚未绑定当前音频与字幕')
    error=run.stage_audit(job,cfg)
    if error: raise ValueError(error)
    if not job_versions.images_ready(job): raise ValueError('图片尚未通过当前版本技术校验，请resume')
    import image_pacing
    image_pacing.validate_job(job)
    creative_options.check_bound(job, cfg)
    album_visuals.validate_inspections(job,cfg)
    return identity


def full_bleed_cover(source, target, size):
    """Cover composition is authored in the image, never shrunk into a safe slot."""
    from artifacts import atomic_copy
    with Image.open(source) as image:
        if image.size != size:
            raise ValueError('封面须先完成1080×1920满幅构图与安全区核对；不能缩进安全框补底')
    atomic_copy(source, target)


def text_layer(text, role, start, end, style, canvas):
    value=copy.deepcopy(style)
    # Diagnostic only. The API mapper uses raw x/y; the service normalizes once.
    value['transform_reference']={'x':value['x']/canvas['width'], 'y':value['y']/canvas['height']}
    return {'text':text,'role':role,'start_us':start,'end_us':end,'style_reference':value}


def prepare(job, cfg):
    import run
    job=Path(job).resolve()
    identity=gates(job,cfg)
    import draft_naming
    draft_name=draft_naming.requested(job,cfg)
    background=album_visuals.current(job,cfg)
    layered = True
    version=source_fingerprint(job,cfg)
    previous=read_json(job/'upload_request.json',{})
    if (previous.get('source_version')==version and (job/'handoff_plan.json').is_file()
            and previous.get('plan_sha256')==file_hash(job/'handoff_plan.json')
            and all(Path(x['path']).is_file() and file_hash(Path(x['path']))==x['sha256'] for x in previous.get('assets',[]))
            and previous.get('assets')):
        return previous
    # Remove only the active publication pointer; old receipts remain in history.
    if (job/'capcut_handoff.json').is_file():
        old=read_json(job/'capcut_handoff.json')
        atomic_json(job/'handoff_history'/f"{digest(old)}.json",old)
        (job/'capcut_handoff.json').unlink()
    choice=creative_options.check_bound(job, cfg)
    spec=config.effective_spec(cfg);audio=subtitle_review.audio_path(job)
    body_us=subtitle_review.duration_us(audio)
    import content_selection
    content_selection.validate_job(job)
    content_selection.duration_ok(body_us/1e6)
    outro_us=round(spec['timing']['outro_seconds']*1e6)
    cover_us=min(round(spec['timing']['cover_seconds']*1e6),body_us)
    images=job_versions.image_assets(job);story=read_json(job/'storyboard.json');copy_text=read_json(job/'copy.json')
    raw_subs=run._read_subtitles(job/'subtitles.csv')
    subtitles=[];last=0
    for cue in raw_subs:
        start,end=cue['start_us'],cue['end_us']
        if not cue['text'].strip() or start<last or end<=start or end>body_us:
            raise ValueError('字幕重叠/越界/为空；必须基于最终音频真实时码')
        subtitles.append(cue);last=end
    if not subtitles: raise ValueError('缺少发布字幕')
    subtitles=caption_api.display_cues(subtitles,cover_us)
    shots=[];cursor=cover_us;seen=set()
    for shot in story.get('shots',[]):
        sid=shot['id']
        if sid in seen or sid not in images: raise ValueError('镜头ID重复或缺素材')
        seen.add(sid)
        start,end=run._to_us(shot['start']),run._to_us(shot['end'])
        if start<0 or end<=start or end>body_us+120000: raise ValueError('镜头时码越界')
        start=max(start,cover_us);end=min(end,body_us)
        if end<=start: continue
        if abs(start-cursor)>1000: raise ValueError('封面后镜头存在空档、重叠或乱序')
        shots.append({'shot_id':sid,'start_us':cursor,'end_us':end});cursor=end
    if abs(cursor-body_us)>1000: raise ValueError('镜头未覆盖最终音频尾部')
    if shots: shots[-1]['end_us']=body_us
    assets={}
    def add(asset_id,path,kind,**meta):
        path=Path(path).resolve()
        if not path.is_file() or path.stat().st_size<=0: raise ValueError('缺少素材 '+asset_id)
        assets[asset_id]={'asset_id':asset_id,'path':str(path),'kind':kind,
                          'sha256':file_hash(path),'bytes':path.stat().st_size,**meta}
        return asset_id
    add('audio',audio,'audio',duration_us=body_us)
    timelines=[]
    for direction in DIRECTIONS:
        canvas=spec[direction]['canvas'];size=(canvas['width'],canvas['height'])
        cover_id=job_versions.cover_id(images,direction)
        job_versions.check_cover_orientation(cover_id,Path(images[cover_id]['path']))
        frame_ids={}
        for sid in [cover_id]+[s['shot_id'] for s in shots]:
            asset_id=direction+'__'+sid
            target=job/'handoff_media'/version/f'{asset_id}.png'
            if sid==cover_id:
                full_bleed_cover(images[sid]['path'],target,size)
            else:
                target=Path(images[sid]['path'])
            with Image.open(target) as actual:
                actual_size=ImageOps.exif_transpose(actual).size
            add(asset_id,target,'image',width=actual_size[0],height=actual_size[1],source_sha256=images[sid]['sha256'],album_background_sha256=background['sha256'])
            frame_ids[sid]=asset_id
        frames=[{'asset_id':frame_ids[cover_id],'role':'cover','start_us':0,'end_us':cover_us}]
        frames += [{**s,'asset_id':frame_ids[s['shot_id']],'role':'story'} for s in shots]
        texts=[]
        for role in ('album','title_line1','title_line2'):
            if role=='album' and not spec['branding']['enabled']: continue
            value=copy_text['album_name' if role=='album' else role]
            style=copy.deepcopy(spec[direction]['texts'][role])
            if role=='album' and len(value)>=style.get('small_threshold',10**9):
                style['font_size']=style.get('font_size_small',style['font_size'])
            start=cover_us
            if start<body_us: texts.append(text_layer(value,role,start,body_us,style,canvas))
        timeline={'orientation':direction,'draft_name':draft_name,'canvas':canvas,'body_duration_us':body_us,
                  'total_duration_us':body_us,'audio':{'asset_id':'audio','start_us':0,'end_us':body_us,'volume':1.0},
                  'frames':frames,'cover_title_embedded':True,'episode_title':copy_text['episode_title'],
                  'album_background':{'sha256':background['sha256'],'revision':background['revision'],'reuse_scope':'episode','mode':background['mode'],'color':background['color']},'frame_fit':'already_canvas_sized; fill_canvas_without_additional_crop',
                  'subtitles':subtitles,'subtitle_style_reference':spec[direction]['texts']['subtitle'],
                  'texts':texts,'presentation_policy':spec['presentation_policy'],'subtitle_display_policy':'hide_first_second_without_shifting_source_timing','transform_convention':spec['transform_convention'],
                  'text_defaults':spec['text_defaults'], 'branding_enabled':spec['branding']['enabled'],
                  'safe_area':copy.deepcopy(spec[direction]['safe_area'])}
        if layered:
            from image_layers import MODE, centered_box
            bg_id=add(direction+'__album_background',background['path'],'image',role='album_background')
            timeline['album_background'].update(asset_id=bg_id,start_us=cover_us,end_us=body_us,
                                                box_px=[0,0,*size],fit='cover')
            timeline['frame_fit']=MODE
            for frame in frames:
                if frame.get('role')=='story':
                    frame['box_px'],frame['original_size_px']=centered_box(assets[frame['asset_id']]['path'],canvas)
        if spec['branding']['enabled']:
            overlay_source=config.SKILL_DIR/spec['branding']['overlay'][direction]
            overlay_path=job/'handoff_media'/version/(direction+'__overlay.png')
            from artifacts import atomic_copy
            safe_layout.transparent_canvas(overlay_source,overlay_path,size,safe_layout.slot(spec,'overlay'),crop_alpha=True)
            overlay=add(direction+'__overlay',overlay_path,'image',source_sha256=file_hash(overlay_source))
            import branding_assets
            timeline['branding_elements']=[branding_assets.playback_image(spec['branding']['elements']['playback'],body_us,start_us=cover_us)]
            thumb_path=album_visuals.album_cover_path(background)
            timeline['album_cover_available']=thumb_path is not None
            if thumb_path is not None:
                thumb=add(direction+'__album_cover',thumb_path,'image',source_sha256=file_hash(thumb_path))
                thumb_style={k:spec['branding']['elements']['album_cover'][k] for k in ('x','y','scale_x','scale_y','alpha')}
                timeline['branding_elements'].append({'role':'album_cover','kind':'image','asset_id':thumb,'start_us':cover_us,'end_us':body_us,'style_reference':thumb_style})
            else:
                timeline['album_cover_omitted_reason']='未提供真实专辑封面，按用户确认文字画风制作；仅略过小封面，保留其他品牌组件'
            source_style=spec['branding']['elements']['source_label']
            source_theme=safe_layout.label_theme(background['path'],source_style,size,safe_layout.slot(spec,'source_label'))
            source_path=config.SKILL_DIR/source_style[source_theme+'_asset']
            with Image.open(source_path) as image:
                if image.mode!='RGBA' or image.getextrema()[3][0]!=0 or image.getextrema()[3][1]==0:
                    raise ValueError('来源图片必须是具有实际透明区域的RGBA PNG')
                source_size=image.size
            label=add(direction+'__source_label',source_path,'image',width=source_size[0],height=source_size[1],
                      source_sha256=file_hash(source_path),source_label_theme=source_theme)
            label_style={k:source_style[k] for k in ('x','y','scale_x','scale_y','alpha')}
            timeline['source_label_theme']=source_theme
            timeline['branding_elements'].append({'role':'source_label','kind':'image','asset_id':label,
                                                  'start_us':cover_us,'end_us':body_us,'style_reference':label_style})
            outro_source=config.SKILL_DIR/spec['branding']['outro'][direction]
            outro_path=outro_source
            outro=add(direction+'__outro',outro_path,'video',source_sha256=file_hash(outro_source))
            info=run._sh(['ffprobe','-v','error','-show_streams','-show_format','-of','json',assets[outro]['path']])
            details=json.loads(info.stdout)
            types={s.get('codec_type') for s in details.get('streams',[])}
            if info.returncode or not {'audio','video'}<=types or float(details['format']['duration'])*1e6<outro_us-1000:
                raise ValueError('品牌片尾必须包含有效音画轨且长度足够')
            timeline.update(overlay={'asset_id':overlay,'start_us':0,'end_us':body_us},
                            outro={'asset_id':outro,'start_us':body_us,'end_us':body_us+outro_us,
                                   'source_start_us':0,'volume':1.0,'preserve_original_audio':True},
                            total_duration_us=body_us+outro_us)
        import layout_profile
        timeline, assets = layout_profile.apply(job,timeline,assets,copy_text)
        timeline['caption_api']=caption_api.compile_timeline(timeline)
        timelines.append(timeline)
    plan={'schema':'knowledge-culture-handoff/v1','status':'awaiting_upload','source_version':version,
          'consumer_skill':'jianchuang-jianying-assistant','time_unit':'microseconds',
          'identity':{k:identity[k] for k in ('highlight_id','album_id','track_id','summary')},'draft_name':draft_name,
          'creative_selection':choice,'value_unit':read_json(job/'cut_plan.json')['content'],
          'timelines':timelines,'review':{'cut':'current','subtitles':'source_grounded_full_review',
          'images': ('automatic_technical_checks_only' if read_json(job/'image_review.json',{}).get('mode')=='automatic_technical'
                     else 'legacy_review_record'),
          'image_human_review_required':False,'cover_title_visual_inspection':'passed','frame_check':'algorithmic_solid'},'render_submitted':False,'draft_created':False}
    if source_fingerprint(job,cfg)!=version: raise ValueError('准备期间输入改变，请重新准备')
    atomic_json(job/'handoff_plan.json',plan)
    result={'schema':1,'source_version':version,'plan_sha256':file_hash(job/'handoff_plan.json'),
            'assets':list(assets.values()),'required_output':'uploaded_assets.json'}
    atomic_json(job/'upload_request.json',result)
    return result


def check_url(url, allowed_hosts):
    if not isinstance(url,str): raise ValueError('素材URL必须为字符串')
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None,443)):
        raise ValueError('素材需要不带账号密码的标准HTTPS URL')
    if parsed.hostname.lower() not in allowed_hosts: raise ValueError('素材域名未在asset_allowed_hosts明确配置')
    if parsed.hostname.lower() in {'localhost'} or parsed.hostname.lower().endswith('.localhost'):
        raise ValueError('不能使用本机URL')
    try:
        address=ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global: raise ValueError('素材不能使用内网或保留IP')


def publication_fresh(job):
    receipt=read_json(job/'capcut_handoff.receipt.json',{})
    return bool(receipt and receipt.get('valid_until',0)>time.time())


def mapping(job,cfg,request):
    uploaded=read_json(job/'uploaded_assets.json',{})
    if uploaded.get('source_version')!=request['source_version']: raise ValueError('上传回填属于旧版本')
    rows=uploaded.get('assets')
    if not isinstance(rows,list): raise ValueError('上传回填需要assets数组')
    result={}
    allowed=cfg.get('asset_allowed_hosts',[])
    if not isinstance(allowed,list) or not allowed or any(not isinstance(x,str) or not x for x in allowed):
        raise ValueError('请配置asset_allowed_hosts为实际存储域名列表')
    allowed={x.lower() for x in allowed};now=time.time()
    expected={x['asset_id']:x for x in request['assets']}
    for row in rows:
        aid=row.get('asset_id')
        if aid in result or aid not in expected: raise ValueError('上传素材ID重复或未知')
        if row.get('sha256')!=expected[aid]['sha256']: raise ValueError('上传回填哈希与当前素材不符')
        check_url(row.get('url'),allowed)
        expiry=row.get('expires_at')
        if expiry is not None and (isinstance(expiry,bool) or not isinstance(expiry,(int,float)) or not math.isfinite(expiry) or expiry<now+3600):
            raise ValueError('素材URL有效期不足一小时；expires_at使用Unix秒，长期URL显式填null')
        if 'expires_at' not in row: raise ValueError('需明确expires_at或null')
        result[aid]=row
    if set(result)!=set(expected): raise ValueError('上传回填缺少素材')
    return result


def current_prepared(job,cfg):
    gates(job,cfg)
    request=read_json(job/'upload_request.json',{})
    if request.get('source_version')!=source_fingerprint(job,cfg): raise ValueError('前序内容改变，请先prepare')
    if request.get('plan_sha256')!=job_versions.optional_hash(job/'handoff_plan.json'): raise ValueError('交接时间线被改动，请先prepare')
    if not request.get('assets'): raise ValueError('交接素材为空')
    for row in request['assets']:
        if row['sha256']!=job_versions.optional_hash(Path(row['path'])): raise ValueError('交接素材缺失或被改动，请先prepare')
    return request


def publish(job,cfg):
    request=current_prepared(job,cfg);rows=mapping(job,cfg,request)
    try:
        return validate(job,cfg)
    except (ValueError,OSError,KeyError,TypeError):
        pass
    # Remove the active pointer before attempting a new remote verification.
    (job/'capcut_handoff.json').unlink(missing_ok=True)
    expected={x['asset_id']:x for x in request['assets']}
    remote={}
    for aid,row in rows.items():
        try:
            with requests.get(row['url'],stream=True,allow_redirects=False,timeout=(10,60)) as response:
                if response.status_code!=200: raise ValueError('HTTP '+str(response.status_code))
                sha=hashlib.sha256();size=0
                for chunk in response.iter_content(262144):
                    size+=len(chunk)
                    if size>expected[aid]['bytes']: raise ValueError('返回大小不符')
                    sha.update(chunk)
                if size!=expected[aid]['bytes'] or sha.hexdigest()!=expected[aid]['sha256']:
                    raise ValueError('远程文件内容与已审核素材不一致')
        except (requests.RequestException,ValueError) as exc:
            # Never echo a signed URL or a response body into logs.
            raise ValueError(f'素材 {aid} 的远程内容校验失败（{type(exc).__name__}），请检查上传或有效期') from None
        remote[aid]={'url':row['url'],'sha256':row['sha256'],'bytes':size,'kind':expected[aid]['kind'],
                     'expires_at':row['expires_at']}
    current_prepared(job,cfg)
    if mapping(job,cfg,request)!=rows: raise ValueError('检查期间上传回填发生变化')
    plan=read_json(job/'handoff_plan.json')
    payload={**plan,'status':'ready_for_capcut','assets':remote}
    for timeline in payload['timelines']:
        timeline['api_sequence']=assembly_api.compile_timeline(timeline,remote)
    atomic_json(job/'capcut_handoff.json',payload)
    expiry=min([time.time()+3600]+[r['expires_at']-3600 for r in rows.values() if r['expires_at'] is not None])
    receipt={'source_version':request['source_version'],'handoff_sha256':file_hash(job/'capcut_handoff.json'),
             'uploads_sha256':file_hash(job/'uploaded_assets.json'),'checked_at':time.time(),
             'valid_until':expiry,'remote_content_verified':True}
    atomic_json(job/'capcut_handoff.receipt.json',receipt)
    return payload


def validate(job,cfg):
    request=current_prepared(job,cfg);mapping(job,cfg,request)
    receipt=read_json(job/'capcut_handoff.receipt.json',{})
    if (not publication_fresh(job) or not receipt.get('remote_content_verified')
            or receipt.get('source_version')!=request['source_version']
            or receipt.get('handoff_sha256')!=job_versions.optional_hash(job/'capcut_handoff.json')
            or receipt.get('uploads_sha256')!=job_versions.optional_hash(job/'uploaded_assets.json')):
        raise ValueError('交接校验缺失、版本变化或过期，请publish重新检查')
    return read_json(job/'capcut_handoff.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','publish','validate'])
    parser.add_argument('--job-dir',required=True,type=Path)
    args=parser.parse_args();job=args.job_dir.resolve()
    try:
        with file_lock(job/'.pipeline.lock',timeout=2):
            result=globals()[args.command](job,read_json(job/'config.json'))
        print(json.dumps({'status':result.get('status','awaiting_upload'),'job':str(job)},ensure_ascii=False))
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('交接未完成：'+str(exc));return 1


if __name__=='__main__': raise SystemExit(main())
