"""Album cover references, episode solid frames, and grounded cover inspection."""
from pathlib import Path
import creative_options
import generate_images
import shared_album
from artifacts import atomic_copy, atomic_json, digest, file_hash, file_lock, read_json, verified_file

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
        needed=('core_subject','visual_tone','composition','title_logic','safe_crop')
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
    state=read_json(Path(job)/'body-frame.json', {})
    settings=cfg.get('album_visuals', {})
    ref=settings.get('reference_image') or state.get('reference_path')
    reference=Path(job)/Path(ref).expanduser() if ref else None
    return {'record':state, 'actual_sha256':file_hash(Path(state['path'])) if state.get('path') and Path(state['path']).is_file() else None,
            'reference_sha256':file_hash(reference) if reference and reference.is_file() else None}


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


def current(job,cfg):
    from solid_frame import materialize
    path=job/'image_manifest.json'
    manifest=generate_images.validate_manifest(read_json(path),path.resolve())
    by_id={r['id']: r for r in manifest['images']}
    shots=read_json(job/'storyboard.json')['shots']
    scenes=[{'id':s['id'],'path':manifest['output_dir']/by_id[s['id']]['filename']} for s in shots]
    frame=materialize(job,scenes)
    value={**validate_setup(job,cfg),**frame}
    if value!=read_json(job/'body-frame.json',{}):
        atomic_json(job/'body-frame.json',value)
    return value


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
    # Cache real album references independently of retired artwork backdrops.
    folder=root(cfg)/'references'
    ref_file=folder/(creative_options.style_hash(cfg)+'.json')
    with file_lock(ref_file.with_suffix('.lock')):
        previous=read_json(ref_file,{})
        if not previous:
            try: previous=find_record(job,cfg,require_file=False)
            except ValueError: pass
        origin=reference_source(job,cfg,previous)
        if not origin.get('reference_path'):
            return origin
        source=Path(origin['reference_path'])
        target=folder/(origin['reference_sha256']+source.suffix.lower())
        if not verified_file(target,origin['reference_sha256']):
            atomic_copy(source,target)
        origin['reference_path']=str(target.resolve())
        if not previous or origin.get('source_reference_path')!=str(target.resolve()):
            if read_json(ref_file,{})!=origin:
                atomic_json(ref_file,origin)
        return origin


def inspection_basis(job,cfg):
    data=generate_images.validate_manifest(read_json(job/'image_manifest.json'),(job/'image_manifest.json').resolve())
    cover=next(x for x in data['images'] if x['id']=='cover_portrait')
    image=data['output_dir']/cover['filename']
    import cover_policy
    raw=read_json(job/'image_manifest.json')
    copy_text=read_json(job/'copy.json')
    historical=cover_policy.legacy(job,cfg)
    previous = cover_policy.submitted_r24_cover(job,cfg,raw) is not None
    basis={'image_sha256':file_hash(image), 'fingerprint':generate_images.image_fingerprint(
           raw.get('api_base',generate_images.DEFAULT_API_BASE),cover,'low'),
           'episode_title':cover_policy.title(copy_text,historical=historical),
           'cover_policy':raw['cover_policy'] if historical or previous else cover_policy.binding()}
    if not historical:
        basis['options']=cover_policy.options(copy_text)
        if cfg.get('cover_policy_version')==2:
            from title_review import approved_version
            basis['title_version']=approved_version(job,cfg)
    return basis


def validate_inspections(job,cfg):
    """Agent performs these inspections; this function never invents a visual pass."""
    background=current(job,cfg)
    cover_review=read_json(job/'cover_review.json',{})
    if (cover_review.get('basis')!=inspection_basis(job,cfg) or cover_review.get('title_exact') is not True
            or not str(cover_review.get('reviewer','')).strip() or not str(cover_review.get('evidence','')).strip()):
        raise ValueError('请执行者查看本集封面并核对标题，写cover_review.json（basis/title_exact/reviewer/evidence），不伪造OCR或用户批准')
    import cover_policy
    copy_text=read_json(job/'copy.json')
    layout=cover_review.get('layout',{})
    historical=cover_policy.legacy(job,cfg)
    if layout.get('canvas')!=[1080,1920] or layout.get('full_bleed') is not True:
        raise ValueError('核对实际最终封面的1080×1920尺寸和满幅背景')
    if historical:
        # Do not relabel a previously reviewed cover as conforming to the new design.
        if (layout.get('cover_policy')!=cover_review['basis']['cover_policy']
                or layout.get('core_label_exact') is not True or layout.get('face_or_core_detail_clear') is not True):
            raise ValueError('历史封面缺少原图对应的真实检查记录')
    else:
        previous = cover_review['basis']['cover_policy'] == cover_policy.R24_BINDING
        expected_profile = cover_policy.R24_PROFILE if previous else cover_policy.layout_profile.load()['id']
        if layout.get('profile')!=expected_profile:
            raise ValueError('请用当前模板的封面检查请求记录目测结果')
        cover_policy.validate_review(layout,copy_text['cover_title_lines'],copy_text,previous=previous)
    from title_review import PROFILE
    if cfg.get('production_profile') == PROFILE:
        import team_visuals
        manifest = job/'image_manifest.json'
        data = generate_images.validate_manifest(read_json(manifest), manifest.resolve())
        live = team_visuals.sample_basis(team_visuals.sample(job, data))
        review = cover_review.get('body_sample', {})
        if review.get('basis') != live or review.get('passed') is not True or not str(review.get('observation','')).strip():
            raise ValueError('结合封面检查开头、中段、末尾三张正文图，核对原声、画风、主体与无字；在同一回执记录 body_sample。')
    return background


def review_request(job,cfg):
    from image_finish import REVIEW
    import config
    record=current(job,cfg)
    basis=inspection_basis(job,cfg)
    raw=read_json(job/'image_manifest.json')
    data=generate_images.validate_manifest(raw,(job/'image_manifest.json').resolve())
    cover=next(x for x in data['images'] if x['id']=='cover_portrait')
    import cover_policy
    profile,policy=cover_policy.settings()
    previous = basis['cover_policy']==cover_policy.R24_BINDING
    safe_area = dict(config.load_spec()['portrait']['safe_area'])
    if previous:
        safe_area['profile'] = cover_policy.R24_PROFILE
    result={'schema':2,'cover_policy':basis['cover_policy'],
            'proportion_reference_path':str((cover_policy.layout_profile.ROOT/policy['reference_asset']).resolve()),
            'expected_title_lines':read_json(job/'copy.json')['cover_title_lines'],
            'cover_options':cover_policy.options(read_json(job/'copy.json')),'title_box_px':cover_policy.R24_TITLE_BOX if previous else cover_policy.title_box(),
            'cover_path':str(data['output_dir']/cover['filename']),'expected_title':basis['episode_title'],
            'safe_area':safe_area,
            'cover_design_reference':cover_design_record(cfg),
            'expected_core_label':cover_policy.options(read_json(job/'copy.json'))['label'],
            'cover_basis':basis,'cover_review_output':str(job/'cover_review.json'),
            'background_path':record['path'],'background_sha256':record['sha256'],'background_key':record['key'],
            'body_frame_color':record['color'],
            'instruction':'查看最终9:16及中央3:4裁图后，记录 basis/title_exact/reviewer/evidence，layout 填 profile、canvas、full_bleed、cover_policy、subject_bounds_px、focus_point_px、title_bounds_px、dominant_core_subject、face_or_core_detail_clear；有简称时补 core_label、core_label_exact、core_label_placement、core_label_bounds_px、core_label_max_glyph_height_px。有指定重点色时记录 title_line_colors、accent_line。包框纯色由脚本计算。'+REVIEW}
    from title_review import PROFILE
    if cfg.get('production_profile') == PROFILE:
        import team_visuals
        sample = team_visuals.sample(job, data)
        result['body_sample'] = {'shots':sample,'basis':team_visuals.sample_basis(sample),
            'instruction':'执行者在同一次封面检查中打开这三张图，比较预期场景、画风和人物连续性，核对无文字/水印、无明显畸形。在 cover_review.body_sample 记录 basis、passed 与具体 observation。有缺陷只修对应镜头。'+REVIEW}
    result['crop_preview']=cover_policy.crop_preview(job,Path(result['cover_path']),basis['image_sha256'])
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
