"""Album-scoped, once-generated background and grounded cover-text inspection."""
import copy
import os
from pathlib import Path
import creative_options
import generate_images
import shared_album
from artifacts import atomic_copy, atomic_json, digest, file_hash, file_lock, read_json, verified_file

BACKGROUND_GUIDE = """生成全专辑共用的无文字竖版氛围底图，采用已确认专辑画风。边角纹理向内自然延伸：左右约四分之一宽度、顶部和底部可见稀疏、低对比的材质与装饰，不能只缩在最外侧窄边。逐渐淡入中央，中央约一半宽度及中段横向视频区域依然安静清爽，标题和专辑信息区域可以清晰排字。不要铺满密集重复花纹。无人物、单集情节、文字、汉字、数字、题字、书名、作者、logo、水印、播放键、边框、UI或占位框。主题装饰应来自本专辑参考，不把某一专辑蓝金红的配色强套到其他专辑。"""


def root(cfg): return shared_album.album_root(cfg)/'visual_assets'


def cover_design_record(cfg):
    """Read the album's reusable cover and headline design before authoring a track."""
    record=read_json(root(cfg)/'cover_design.json',{})
    if record and record.get('album_id')!=str(cfg['album_id']):
        raise ValueError('封面设计档案属于其他专辑')
    return record


def remember_cover_design(job,cfg):
    """Pin the first reviewed design; later tracks reuse it unless user changes it."""
    path=root(cfg)/'cover_design.json'
    with file_lock(path.with_suffix('.lock')):
        existing=cover_design_record(cfg)
        if existing:return existing
        proposal=read_json(job/'cover_design.json',{})
        design=proposal.get('design') if isinstance(proposal,dict) else None
        needed=('core_label','core_subject','visual_tone','composition','title_logic','safe_crop')
        if not isinstance(design,dict) or any(not str(design.get(k,'')).strip() for k in needed):
            raise ValueError('首次封面核对后须写job/cover_design.json的design，记录主体、调性、构图、标题逻辑和安全区')
        basis=inspection_basis(job,cfg)
        cover=job/'images/generated/cover_portrait.png'
        saved_cover=root(cfg)/'cover_design'/('first_cover_'+basis['image_sha256'][:12]+'.png')
        atomic_copy(cover,saved_cover)
        record={'schema':1,'album_id':str(cfg['album_id']),'version':1,'design':design,
                'first_track_id':str(cfg.get('requested_track_id') or ''),
                'first_cover_path':str(saved_cover.resolve()),
                'first_cover_sha256':basis['image_sha256'],
                'background_key':current(job,cfg)['key']}
        atomic_json(path,record)
        return record


def title(copy_text):
    import cover_policy
    return cover_policy.title(copy_text)


def background_state(job,cfg):
    folder=root(cfg)
    try: record=find_record(job,cfg,require_file=False)
    except ValueError: record=read_json(folder/'background.json',{})
    path=Path(record.get('path') or folder/'missing.png')
    settings=cfg.get('album_visuals',{})
    ref=settings.get('reference_image') if isinstance(settings,dict) else None
    if ref:
        reference=Path(ref).expanduser();reference=reference if reference.is_absolute() else job/reference
        reference_hash=file_hash(reference) if reference.is_file() else record.get('reference_sha256')
    else: reference_hash=record.get('reference_sha256')
    return {'settings':settings,'reference_sha256':reference_hash,'style_hash':creative_options.style_hash(cfg),
            'record':record,'actual_sha256':file_hash(path) if path.is_file() else None,
            'review':read_json(folder/'reviews'/(str(record.get('key','missing'))+'.json'),{})}


def find_record(job,cfg,require_file=True):
    settings=cfg.get('album_visuals',{})
    if not isinstance(settings,dict): raise ValueError('album_visuals须为对象')
    desired_ref=None;missing_reference=None
    if settings.get('reference_image'):
        reference=Path(settings['reference_image']).expanduser()
        reference=reference if reference.is_absolute() else job/reference
        if reference.is_file(): desired_ref=file_hash(reference)
        else: missing_reference=str(reference.resolve())
    latest=read_json(root(cfg)/'background.json',{})
    records=[latest]+[read_json(p,{}) for p in (root(cfg)/'history').glob('*.json')]
    for record in records:
        if (record.get('album_id')==str(cfg['album_id']) and record.get('style_hash')==creative_options.style_hash(cfg)
                and (settings.get('reference_mode')!='style_only' or record.get('reference_mode')=='style_only')
                and (desired_ref is None or record.get('reference_sha256')==desired_ref)
                and (missing_reference is None or missing_reference in (record.get('source_reference_path'),record.get('reference_path')))
                and (settings.get('background_revision') is None or settings['background_revision']==record.get('revision'))):
            if not require_file or verified_file(Path(record.get('path','')),record.get('sha256')): return record
    raise ValueError('共用底图尚未就绪或来源/版本已变，请resume恢复同一版本，不盲目重生')


def current(job,cfg): return find_record(job,cfg)


def reference_source(job,cfg,previous=None):
    """A real provided/cached image, or a clearly identified confirmed-text source."""
    settings=cfg.get('album_visuals',{});previous=previous or {}
    mode=settings.get('reference_mode')
    if mode not in (None,'user_image','style_only'):raise ValueError('reference_mode仅支持user_image或style_only')
    raw=settings.get('reference_image')
    if mode=='style_only' and raw:raise ValueError('style_only不应填写或伪造reference_image')
    if raw:
        path=Path(raw).expanduser();path=path if path.is_absolute() else job/path
        if not path.is_file() and str(path.resolve()) in (previous.get('source_reference_path'),previous.get('reference_path')):
            path=Path(previous.get('reference_path') or '')
        if not path.is_file():raise ValueError('指定专辑封面缺失，请恢复原图；确实无图时移除路径并使用style_only')
    elif previous.get('reference_path') and mode!='style_only':
        path=Path(previous['reference_path'])
        if not path.is_file():raise ValueError('已缓存专辑封面缺失，请恢复；不静默改成文字画风重新收费')
    else:
        if mode=='user_image':raise ValueError('user_image需要真实reference_image')
        confirmation=cfg.get('creative_options',{}).get('style_confirmation',{})
        if confirmation.get('confirmed_by')!='user' or not str(confirmation.get('evidence','')).strip():
            raise ValueError('没有专辑封面时需要用户已确认的文字画风；不伪造图片或确认')
        creative_options.selected(cfg)
        return {'reference_mode':'style_only','reference_path':None,'source_reference_path':None,'reference_sha256':None}
    generate_images.verify_image(path)
    return {'reference_mode':'user_image','reference_path':str(path),'source_reference_path':str(path.resolve()),'reference_sha256':file_hash(path)}


def album_cover_path(record):
    if record.get('reference_mode')=='style_only':return None
    path=Path(record.get('reference_path') or '')
    if not path.is_file() or file_hash(path)!=record.get('reference_sha256'):
        raise ValueError('真实专辑封面缺失或字节已变化，不能用底图/假封面顶替')
    return path


def validate_setup(job,cfg):
    try: return current(job,cfg)
    except ValueError: pass
    options=cfg.get('album_visuals',{})
    if not isinstance(options,dict): raise ValueError('album_visuals须为对象')
    try: return find_record(job,cfg,require_file=False)
    except ValueError: pass
    source=reference_source(job,cfg)
    if not isinstance(options.get('background_prompt'),str) or not options['background_prompt'].strip():
        raise ValueError('首次底图需填写已确认画风下的background_prompt')
    if source['reference_mode']=='user_image' and not str(options.get('reference_summary','')).strip():
        raise ValueError('真实封面需填写reference_summary视觉分析')
    return source


def ensure_background(job,cfg):
    """No episode title/track enters its key; a single album lock spans the paid call."""
    folder=root(cfg);settings=copy.deepcopy(cfg.get('album_visuals',{}))
    if not isinstance(settings,dict):raise ValueError('album_visuals须为对象')
    with file_lock(folder/'.background.lock',timeout=900):
        latest=read_json(folder/'background.json',{})
        try: previous=find_record(job,cfg,require_file=False)
        except ValueError: previous=latest
        try:
            return current(job,cfg)
        except ValueError:
            pass
        origin=reference_source(job,cfg,previous)
        reference=Path(origin['reference_path']) if origin['reference_path'] else None
        ref_hash=origin['reference_sha256']
        revision=settings.get('background_revision',previous.get('revision',1))
        if type(revision) is not int or revision<1:raise ValueError('background_revision须为正整数')
        style=creative_options.style_hash(cfg)
        changed=bool(previous and (ref_hash!=previous['reference_sha256'] or style!=previous['style_hash'] or revision!=previous['revision']))
        if changed:
            confirmation=settings.get('regeneration_confirmation',{})
            if revision<=previous['revision'] or confirmation.get('confirmed_by')!='user' or not str(confirmation.get('evidence','')).strip():
                raise ValueError('更换底图需递增background_revision并记录实际用户修改要求；不随声音重复生成')
        size=settings.get('background_size',previous.get('size','1024x1536'))
        key=digest({'album_id':str(cfg['album_id']),'reference_sha256':ref_hash,'style_hash':style,'revision':revision})
        home=folder/'versions'/key
        manifest=home/'image_manifest.json'
        # Keep the first prompt snapshot stable across jobs and retries of the same album version.
        if not manifest.is_file():
            summary=settings.get('reference_summary') or ('未提供专辑封面；使用用户已确认的文字画风，不声称分析过原图' if origin['reference_mode']=='style_only' else '')
            direction=settings.get('background_prompt')
            if not isinstance(summary,str) or not summary.strip() or not isinstance(direction,str) or not direction.strip():
                raise ValueError('首次底图需填写实际来源说明与background_prompt')
            reference_copy=None
            if reference is not None:
                reference_copy=folder/'references'/(ref_hash+reference.suffix.lower())
                atomic_copy(reference,reference_copy)
            prompt='【统一画风（用户确认）】'+creative_options.selected(cfg)['style']['prompt']+('\n【封面视觉分析】' if reference is not None else '\n【文字画风来源】')+summary+'\n'+direction+'\n'+BACKGROUND_GUIDE
            raw={'output_dir':'generated','quality':'low','api_base':settings.get('api_base',cfg.get('image_api_base',generate_images.DEFAULT_API_BASE)),'images':[{'id':'album_background','filename':'album-background.png','size':size,'prompt':prompt,'revision':revision}]}
            generate_images.validate_manifest(raw,manifest.resolve())
            atomic_json(manifest,raw)
            atomic_json(home/'reference.json',{**origin,'reference_path':str(reference_copy) if reference_copy else None,'reference_summary':summary,'revision':revision})
        raw=read_json(manifest);data=generate_images.validate_manifest(raw,manifest.resolve())
        report=generate_images.run_batch(data,os.environ.get('GPT_IMAGE_2_TOKEN',''),api_base=raw.get('api_base',generate_images.DEFAULT_API_BASE),
            cache_dir=shared_album.album_root(cfg)/'images')
        if any(row['status']=='failed' for row in report['results']):raise ValueError('共用底图生成/下载待恢复，查看共享目录回执，不重新付费提交')
        image=data['output_dir']/'album-background.png'
        receipt=read_json(image.parent/'.receipts/album_background.json',{})
        if not verified_file(image,receipt.get('sha256')):raise ValueError('共用底图缺少真实生成凭据')
        with generate_images.Image.open(image) as im:
            if im.width>=im.height:raise ValueError('共用底图实际必须竖向')
        basis=read_json(home/'reference.json')
        record={'schema':1,'album_id':str(cfg['album_id']),'revision':revision,'key':key,'style_hash':style,
                'reference_mode':basis.get('reference_mode','user_image'),'reference_sha256':ref_hash,'reference_path':basis['reference_path'],'source_reference_path':basis.get('source_reference_path',basis['reference_path']),'size':raw['images'][0]['size'],
                'path':str(image),'sha256':file_hash(image),'fingerprint':receipt['fingerprint'],'provenance':receipt.get('provenance')}
        if previous and changed:atomic_json(folder/'history'/(previous['key']+'.json'),previous)
        if not latest or revision>=latest.get('revision',0): atomic_json(folder/'background.json',record)
        else: atomic_json(folder/'history'/(record['key']+'.json'),record)
        return record


def inspection_basis(job,cfg):
    data=generate_images.validate_manifest(read_json(job/'image_manifest.json'),(job/'image_manifest.json').resolve())
    cover=next(x for x in data['images'] if x['id']=='cover_portrait')
    image=data['output_dir']/cover['filename']
    return {'image_sha256':file_hash(image),'fingerprint':generate_images.image_fingerprint(read_json(job/'image_manifest.json').get('api_base',generate_images.DEFAULT_API_BASE),cover,'low'),
            'episode_title':title(read_json(job/'copy.json')),
            'cover_policy':__import__('cover_policy').binding()}


def validate_inspections(job,cfg):
    """Agent performs these inspections; this function never invents a visual pass."""
    background=current(job,cfg)
    bg_review=read_json(root(cfg)/'reviews'/(background['key']+'.json'),{})
    if (bg_review.get('sha256')!=background['sha256'] or bg_review.get('no_text') is not True or bg_review.get('center_clear') is not True
            or not str(bg_review.get('reviewer','')).strip() or not str(bg_review.get('evidence','')).strip()):
        raise ValueError('请执行者查看一次专辑底图，写共享reviews/<background.key>.json（sha256/no_text/center_clear/reviewer/evidence）；不是再次向用户提问')
    cover_review=read_json(job/'cover_review.json',{})
    if (cover_review.get('basis')!=inspection_basis(job,cfg) or cover_review.get('title_exact') is not True
            or not str(cover_review.get('reviewer','')).strip() or not str(cover_review.get('evidence','')).strip()):
        raise ValueError('请执行者查看本集封面并核对标题，写cover_review.json（basis/title_exact/reviewer/evidence），不伪造OCR或用户批准')
    import config
    safe=config.load_spec()['portrait']['safe_area']
    layout=cover_review.get('layout',{})
    if (layout.get('profile')!=safe['profile'] or layout.get('canvas')!=[1080,1920]
            or layout.get('full_bleed') is not True):
        raise ValueError('封面需1080×1920满幅构图复核：在cover_review.layout记录当前profile/canvas/full_bleed及主体和标题边界')
    rect=safe['rect_px']
    core=read_json(job/'copy.json').get('album_core_label') or cover_design_record(cfg).get('design',{}).get('core_label')
    if (not core or layout.get('core_label')!=core or layout.get('core_label_exact') is not True
            or layout.get('core_subject_present') is not True):
        raise ValueError('封面须实际核对专辑核心主体与顶部/右侧的次级专辑简称；不能用本集配角代替专辑主角')
    for key in ('subject_bounds_px','title_bounds_px','core_label_bounds_px'):
        boxes=layout.get(key)
        if not isinstance(boxes,list) or not boxes:raise ValueError('封面缺少目视边界记录：'+key)
        for box in boxes:
            if (not isinstance(box,list) or len(box)!=4 or not all(isinstance(v,(int,float)) for v in box)
                    or not (rect[0]<=box[0]<box[2]<=rect[2] and rect[1]<=box[1]<box[3]<=rect[3])):
                raise ValueError('封面主体或完整标题超出顶部13%/底部25%/左右5%安全区：'+key)
    __import__('cover_policy').validate_review(layout,read_json(job/'copy.json')['cover_title_lines'])
    return background


def review_request(job,cfg):
    import config
    record=current(job,cfg)
    basis=inspection_basis(job,cfg)
    raw=read_json(job/'image_manifest.json')
    data=generate_images.validate_manifest(raw,(job/'image_manifest.json').resolve())
    cover=next(x for x in data['images'] if x['id']=='cover_portrait')
    (root(cfg)/'reviews').mkdir(parents=True,exist_ok=True)
    import cover_policy
    profile,policy=cover_policy.settings()
    result={'schema':2,'cover_policy':cover_policy.binding(),
            'proportion_reference_path':str((cover_policy.layout_profile.ROOT/policy['reference_asset']).resolve()),
            'expected_title_lines':read_json(job/'copy.json')['cover_title_lines'],
            'expected_line_colors':policy['episode_title']['line_colors'][:len(read_json(job/'copy.json')['cover_title_lines'])],'title_box_px':cover_policy.title_box(),
            'cover_path':str(data['output_dir']/cover['filename']),'expected_title':basis['episode_title'],
            'safe_area':config.load_spec()['portrait']['safe_area'],
            'cover_design_reference':cover_design_record(cfg),
            'expected_core_label':read_json(job/'copy.json').get('album_core_label') or cover_design_record(cfg).get('design',{}).get('core_label'),
            'cover_basis':basis,'cover_review_output':str(job/'cover_review.json'),
            'background_path':record['path'],'background_sha256':record['sha256'],'background_key':record['key'],
            'background_review_output':str(root(cfg)/'reviews'/(record['key']+'.json')),
            'instruction':'实际查看后记录cover_review的basis/title_exact/reviewer/evidence，以及layout.profile/canvas/full_bleed/core_label/core_label_exact/core_subject_present/subject_bounds_px/title_bounds_px/core_label_bounds_px；另记录layout.cover_policy、每行title_bounds_px/title_line_colors、core_label_placement/core_label_max_glyph_height_px、dominant_core_subject/face_or_core_detail_clear；标题按文案为两行或三行。边界与颜色为目视标注，不冒充OCR。background: sha256/no_text/center_clear/reviewer/evidence。'}
    atomic_json(job/'visual_review_request.json',result)
    return result


def main():
    import argparse,json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['status','review-request'])
    parser.add_argument('--job-dir',type=Path,required=True)
    args=parser.parse_args();job=args.job_dir.expanduser().resolve()
    try:
        cfg=read_json(job/'config.json')
        result=review_request(job,cfg) if args.command=='review-request' else background_state(job,cfg)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,OSError,KeyError,TypeError,StopIteration) as exc:
        print('专辑视觉待处理：'+str(exc));return 1


if __name__=='__main__':raise SystemExit(main())
