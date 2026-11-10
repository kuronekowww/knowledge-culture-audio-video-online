"""Dependency invalidation and version-bound image technical validation."""
from pathlib import Path

import config
import asr_backend
import generate_images
import shared_album
from artifacts import atomic_json, digest, file_hash, read_json


def optional_hash(path):
    return file_hash(path) if Path(path).is_file() else None


def pin_existing_quality(job: Path, cfg: dict):
    """Preserve paid results of pre-low jobs whose manifests omitted quality."""
    path = job/'image_manifest.json'
    raw = read_json(path, {})
    if not raw or 'quality' in raw:
        return
    matches = set()
    cache = shared_album.album_root(cfg)/'images'
    previous_stage = read_json(job/'state.json', {}).get('stages', {}).get('generate_images')
    has_previous_attempt = previous_stage in {'running', 'failed', 'done', 'awaiting_human'}
    for quality in sorted(generate_images.ALLOWED_QUALITIES):
        data = generate_images.validate_manifest({**raw, 'quality':quality}, path.resolve())
        api = raw.get('api_base', generate_images.DEFAULT_API_BASE)
        for item in data['images']:
            if 'quality' in item:
                continue  # explicit per-image quality does not imply a top-level legacy default
            fp = generate_images.image_fingerprint(api, item, quality)
            local = read_json(data['output_dir']/'.receipts'/f"{item['id']}.json", {})
            if local.get('fingerprint') == fp or (has_previous_attempt and (cache/fp/'request.json').is_file()):
                matches.add(quality)
    if len(matches) > 1:
        raise ValueError('旧图片存在多个quality版本，请在image_manifest明确选择；不自动重发')
    if matches:
        atomic_json(path, {**raw, 'quality':matches.pop()})


def build_dependencies(orientation=None):
    """Track code and media bytes, including replacements at the same path."""
    branding = config.load_spec().get('branding', {})
    media = {}
    if branding.get('enabled'):
        for kind in ('overlay', 'outro'):
            for direction, path in branding.get(kind, {}).items():
                if orientation is None or direction == orientation:
                    media[f'{kind}/{direction}'] = optional_hash(config.SKILL_DIR / path)
        source=branding.get('elements',{}).get('source_label',{})
        for theme in ('dark','light'):
            if source.get(theme+'_asset'):
                media[f'source_label/{theme}']=optional_hash(config.SKILL_DIR/source[theme+'_asset'])
    return {'code': {name: optional_hash(config.SCRIPT_DIR / name)
                     for name in ('run.py', 'handoff.py', 'caption_api.py', 'assembly_api.py', 'creative_options.py', 'job_versions.py', 'content_selection.py', 'request_chunks.py', 'album_visuals.py', 'branding_assets.py', 'master_catalog.py', 'image_pacing.py', 'draft_naming.py', 'safe_layout.py', 'layout_profile.py', 'cover_policy.py', 'layout_html.py', 'jianying_profile.py', 'image_layers.py', 'solid_frame.py')}, 'branding': media, 'layout_profile': __import__('layout_profile').binding(), 'layout_assets': __import__('layout_profile').check_assets()}


def image_assets(job: Path) -> dict:
    path = job / 'image_manifest.json'
    raw = read_json(path)
    if not raw:
        return {}
    data = generate_images.validate_manifest(raw, path.resolve())
    api = raw.get('api_base', generate_images.DEFAULT_API_BASE)
    return {i['id']: {'path': str(data['output_dir'] / i['filename']),
                     'sha256': optional_hash(data['output_dir'] / i['filename']),
                     'fingerprint': generate_images.image_fingerprint(api, i, data['quality'])}
            for i in data['images']}


def approve_images(job: Path, ids: list[str] | None = None, *, automatic=False) -> dict:
    assets = image_assets(job)
    if not assets:
        raise ValueError('没有待审图片')
    selected = ids or list(assets)
    if set(selected) - set(assets):
        raise ValueError('未知镜头ID')
    approvals = read_json(job / 'image_review.json', {}).get('images', {})
    for image_id in selected:
        item = assets[image_id]
        if not item['sha256']:
            raise ValueError(f'缺图: {image_id}')
        generate_images.verify_image(Path(item['path']))
        check_cover_orientation(image_id, Path(item['path']))
        receipt = read_json(Path(item['path']).parent / '.receipts' / f'{image_id}.json', {})
        if receipt.get('fingerprint') != item['fingerprint'] or receipt.get('sha256') != item['sha256']:
            raise ValueError(f'{image_id}缺少当前版本生成凭据；旧图确认后先adopt-existing')
        approvals[image_id] = item
    result = {'schema': 2, 'images': {k: v for k, v in approvals.items() if k in assets},
              'mode': 'automatic_technical' if automatic else 'manual',
              'visual_review_performed': False if automatic else None}
    atomic_json(job / 'image_review.json', result)
    return result


def validate_images(job: Path) -> dict:
    """Decode files, check orientation and provenance; never claim human/visual review."""
    record = read_json(job / 'image_review.json', {})
    if record.get('mode') == 'automatic_technical' and images_ready(job):
        return record
    return approve_images(job, automatic=True)


def cover_id(assets: dict, orientation: str) -> str:
    if orientation != 'portrait': raise ValueError('本技能只输出portrait')
    if 'cover_portrait' not in assets: raise ValueError('需要独立带标题竖封面cover_portrait')
    return 'cover_portrait'


def check_cover_orientation(image_id: str, path: Path):
    from PIL import Image
    with Image.open(path) as image:
        portrait=image_id in {'cover_portrait','album_background'}
        if (portrait and image.width>=image.height) or (not portrait and image.width<=image.height):
            raise ValueError(f'{image_id}实际方向与封面/底图竖向、正文横向规则不符')


def draft_image_assets(job: Path, orientation: str) -> dict:
    assets = image_assets(job)
    used = {s['id'] for s in read_json(job / 'storyboard.json', {}).get('shots', [])}
    used.add(cover_id(assets, orientation))
    return {key: assets[key] for key in sorted(used)}


def images_ready(job: Path) -> bool:
    assets = image_assets(job)
    approved = read_json(job / 'image_review.json', {}).get('images', {})
    return bool(assets) and all(x['sha256'] and approved.get(i) == x for i, x in assets.items())


def signature(stage: str, job: Path, cfg: dict):
    h = lambda name: optional_hash(job / name)
    if stage == 'fetch_transcripts':
        csv_path = Path(cfg.get('csv') or 'xql_tracks.csv')
        if not csv_path.is_absolute():
            csv_path = job / csv_path
        payload = [cfg.get('album_id'), cfg.get('n_transcripts'), optional_hash(csv_path),
                   cfg.get('mosn_base_url'), cfg.get('transcript_cache_ttl', 86400)]
    elif stage in {'analyze_highlights', 'download_audio'}:
        payload = [h('cut_plan.json')]
        if stage == 'download_audio':
            payload.append(h('download_result.json'))
            plan = read_json(job / 'cut_plan.json', {})
            if not plan.get('source_audio'):
                from download_audio import build_url
                row = next((r for r in shared_album.input_rows(job, cfg)
                            if r['track_id'].strip() == str(plan.get('track_id', ''))), {})
                payload.append([build_url(row.get('track_play_path') or ''), row.get('source_version', '')])
    elif stage == 'cut_speed':
        plan = read_json(job / 'cut_plan.json', {})
        source = plan.get('source_audio')
        if source:
            path = Path(source)
            if not path.is_absolute():
                path = job / path
            source_hash = optional_hash(path)
        else:
            selected = read_json(job / 'download_result.json', {})
            source_hash = optional_hash(Path(selected['path'])) if selected.get('path') else None
        payload = [h('cut_plan.json'), source_hash, config.load_spec()['timing']]
        import content_selection
        chosen = content_selection.speed(plan,cfg)
        if chosen != 1:
            from subtitle_review import audio_path
            payload.extend([chosen, h('cut_result.json'), optional_hash(audio_path(job))])
    elif stage == 'subtitles':
        import subtitle_review
        asr_settings = cfg.get('asr', {})
        import caption_routing
        route_scope=[caption_routing.mode(cfg),cfg.get('subtitle_word_ranges'),cfg.get('caption_probe_asr')]
        if read_json(job/'final_transcript.meta.json',{}).get('source_kind')==caption_routing.TIMED:
            # A changed document must reach the planner even if the old selected clip missed it.
            import document_context
            doc=document_context.caption_document(job,cfg,fetch=False)
            route_scope.extend([caption_routing.recipe(job,cfg,doc) if doc else None, h(caption_routing.REPORT),h('final_transcript.meta.json')])
        payload = [optional_hash(subtitle_review.audio_path(job)), h('final_transcript.json'),
                   h('subtitles.csv'), shared_album.selected_context(job, cfg), route_scope,
                   asr_settings, cfg.get('subtitle_review_mode'), cfg.get('content_wordlist_mode','contextual'), asr_backend.identity(cfg), subtitle_review.review_version(),
                   optional_hash(config.SKILL_DIR / 'references/rejected-terms.csv'),
                   optional_hash(config.SKILL_DIR / 'references/growth-review-rules.md')]
        review=read_json(job/'subtitle_review.json',{})
        if review.get('status')=='passed' and review.get('srt_sha256'):
            payload.extend([review['srt_sha256'],h('subtitles.srt')])
    elif stage == 'storyboard':
        import creative_options
        payload = [h('storyboard.json'), h('copy.json'), h('image_manifest.json'), h('cut_result.json'),
                   h('storyboard_timing_review.json'), h('highlight_identity.json'), creative_options.style_hash(cfg), cfg.get('album_visuals')]
        if cfg.get('production_profile') == 'team-v1':
            payload.extend([h('visual_bible.json'), h('subtitle_review_request.json'),
                            optional_hash(config.SCRIPT_DIR/'team_visuals.py')])
        if (job / 'storyboard_prepare_request.json').is_file():
            payload.extend([h('asr_corrected.json'), h('subtitles.csv'), h('visual_bible.json')])
    elif stage in {'generate_images', 'review_images'}:
        import album_visuals
        payload = [image_assets(job)]
        if stage == 'review_images':
            payload.append(album_visuals.background_state(job,cfg))
        if stage == 'review_images':
            payload = [payload, h('image_review.json'), h('cover_review.json'), 'technical_and_cover_text_v2']
    elif stage in {'prepare_handoff', 'publish_handoff'}:
        import handoff
        payload = [handoff.source_fingerprint(job, cfg)]
        if stage == 'prepare_handoff':
            payload.append(handoff.prepared_fingerprint(job))
        else:
            payload.extend([h('uploaded_assets.json'), h('capcut_handoff.json'),
                            h('capcut_handoff.receipt.json'), handoff.publication_fresh(job)])
    else:
        raise ValueError(stage)
    return digest(payload)


INVALIDATES = {
    'fetch_transcripts': ['fetch_transcripts'],
    'analyze_highlights': ['analyze_highlights', 'download_audio', 'cut_speed', 'subtitles', 'storyboard', 'prepare_handoff', 'publish_handoff'],
    'download_audio': ['download_audio', 'cut_speed', 'subtitles', 'storyboard', 'prepare_handoff', 'publish_handoff'],
    'cut_speed': ['cut_speed', 'subtitles', 'storyboard', 'prepare_handoff', 'publish_handoff'],
    'subtitles': ['subtitles', 'prepare_handoff', 'publish_handoff'],
    'storyboard': ['storyboard', 'prepare_handoff', 'publish_handoff'],
    'generate_images': ['generate_images', 'review_images', 'prepare_handoff', 'publish_handoff'],
    'review_images': ['review_images', 'prepare_handoff', 'publish_handoff'],
    'prepare_handoff': ['prepare_handoff', 'publish_handoff'],
    'publish_handoff': ['publish_handoff'],
}


def invalidate_changed(state: dict, job: Path, cfg: dict):
    stamps = state.setdefault('fingerprints', {})
    changes = []
    for stage in state['stages']:
        if state['stages'][stage] != 'done':
            continue
        if stage == 'subtitles':
            # Recover a missing derivative before invalidating already finished video stages.
            import subtitle_review
            review = read_json(job/'subtitle_review.json',{})
            if review.get('status')=='passed':
                subtitle_review.checked_srt(job,review,repair=True)
        value = signature(stage, job, cfg)
        if stage not in stamps:
            # Legacy semantic/image approvals have no provenance and must be renewed.
            if stage not in {'subtitles', 'generate_images', 'review_images', 'prepare_handoff', 'publish_handoff'}:
                stamps[stage] = value
                continue
        if stamps.get(stage) != value:
            changes.append(stage)
            for dependent in INVALIDATES[stage]:
                state['stages'][dependent] = 'pending'
    return changes


def stamp(state: dict, stage: str, job: Path, cfg: dict):
    state.setdefault('fingerprints', {})[stage] = signature(stage, job, cfg)


def validate_story_images(job):
    """Catch missing/extra images before any paid generation request."""
    path=job/'image_manifest.json'
    images=generate_images.validate_manifest(read_json(path),path.resolve())['images']
    ids=[x['id'] for x in read_json(job/'storyboard.json',{}).get('shots',[])]
    if not ids or len(ids)!=len(set(ids)) or 'cover_portrait' in ids:
        raise ValueError('分镜需要非空、唯一镜头ID，独立封面不作为正文镜头')
    if {x['id'] for x in images} != set(ids)|{'cover_portrait'}:
        raise ValueError('图片清单须恰好包含独立竖封面及每个正文镜头；共享底图不能放入逐集清单；不多生图、不漏镜头')
