"""Validate source-grounded value units, never infer semantic approval from structure."""
import math
import config
from artifacts import read_json


def seconds(value):
    if isinstance(value, bool): raise ValueError('时间不能是布尔值')
    if isinstance(value, str) and ':' in value:
        total = 0.0
        for part in value.replace(',', '.').split(':'): total = total * 60 + float(part)
    else: total = float(value)
    if not math.isfinite(total): raise ValueError('时间必须有限')
    return total


def minimum():
    return read_json(config.SPEC_PATH)['timing']['body_min_seconds']


def duration_ok(duration):
    if not math.isfinite(duration) or duration < minimum():
        raise ValueError('正文需至少300秒；按完整价值单元重新选段，不得拉慢、循环或无关拼接凑长')


SPEEDS = (1.0, 1.2, 1.4)


def speed(plan, cfg=None):
    value = plan.get('speed', (cfg or {}).get('speed', 1))
    try:
        chosen = float(value)
    except (ValueError, TypeError):
        chosen = None
    if isinstance(value, bool) or chosen not in SPEEDS:
        raise ValueError('正文倍速须为 1、1.2 或 1.4')
    return chosen


def speed_options(source_seconds, chosen):
    limit = minimum()
    return {'speed':chosen, 'selected_source_seconds':source_seconds,
            'expected_body_seconds':round(source_seconds / chosen, 6),
            'available_speeds':[{'speed':r, 'body_seconds':round(source_seconds/r, 6)}
                                for r in SPEEDS if source_seconds >= limit*r]}


def verify_speed_duration(options):
    if options['speed'] not in [row['speed'] for row in options['available_speeds']]:
        choices = '、'.join(f"{row['speed']:g}倍" for row in options['available_speeds']) or '无'
        raise ValueError(f"所选{options['speed']:g}倍预计正文{options['expected_body_seconds']:g}秒，"
                         f"不足{minimum():g}秒；此范围的可选档为{choices}")


def selection_mode(plan):
    policy=read_json(config.SPEC_PATH)['selection_policy']
    mode = plan.get('selection_mode', policy['default'])
    if mode not in policy['modes']:
        raise ValueError('selection_mode须为excerpt或full_track')
    return mode


def validate_source(plan, duration):
    """Check measured audio boundaries, never a catalog's estimated duration."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('源音频真实时长无效')
    duration_ok(duration)
    clips = plan['clips']
    mode = selection_mode(plan)
    end_tolerance = .12 if mode == 'full_track' else .001
    for clip in clips:
        if seconds(clip['end']) > duration + end_tolerance:
            raise ValueError('切点超过源音频真实时长')
    if mode == 'full_track':
        if (len(clips) != 1 or seconds(clips[0]['start']) != 0
                or abs(seconds(clips[0]['end']) - duration) > .12):
            raise ValueError('full_track必须完整保留同一声音0至真实结尾；裁剪请用excerpt')


def validate(plan, cfg=None):
    mode = selection_mode(plan)
    clips = plan.get('clips')
    if not isinstance(clips, list) or not clips: raise ValueError('需要clips源音频范围')
    chosen = speed(plan, cfg)
    total = 0; previous = -1
    for clip in clips:
        start, end = seconds(clip['start']), seconds(clip['end'])
        if start < 0 or end <= start or start < previous: raise ValueError('clips必须按源顺序、无重复或重叠')
        if 'speed' in clip and speed(clip) != chosen:
            raise ValueError('本条视频的各选段须沿用同一倍速，使用条目级 speed')
        total += end-start; previous = end
    options = speed_options(total, chosen)
    verify_speed_duration(options)
    body = total / chosen
    content = plan.get('content', {})
    if not isinstance(content, dict): raise ValueError('content需要对象')
    if mode == 'full_track':
        if len(clips)!=1 or seconds(clips[0]['start'])!=0:
            raise ValueError('full_track必须是从0开始的一条完整连续声音')
        if content.get('is_complete') is not True or content.get('is_intro_only') is not False:
            raise ValueError('full_track须实际审阅并记录content.is_complete=true、is_intro_only=false')
    for key in ('problem','demand_reason','delivered_value','conclusion','complete_reason','opening_hook'):
        if not isinstance(content.get(key), str) or not content[key].strip(): raise ValueError('content缺少'+key)
    if body > 600 and not str(content.get('extended_reason','')).strip():
        raise ValueError('超过10分钟允许，但需extended_reason说明保留完整论述的原因')
    if len(clips) > 1 and not str(content.get('splice_reason','')).strip(): raise ValueError('多段拼接需splice_reason说明语义连接与删减')
    evidence = content.get('evidence')
    if not isinstance(evidence, list) or not evidence: raise ValueError('缺少已交付价值的源文稿证据')
    for row in evidence:
        a,b=seconds(row['start']),seconds(row['end'])
        if not isinstance(row.get('text'),str) or not row['text'].strip() or not any(seconds(c['start']) <= a < b <= seconds(c['end']) for c in clips):
            raise ValueError('价值证据必须落在已选择音频范围内')
    hook = content.get('return_hook')
    if mode == 'full_track' and not hook:
        if not isinstance(content.get('return_hook_omission_reason'), str) or not content['return_hook_omission_reason'].strip():
            raise ValueError('完整单集无回站钩子时须写return_hook_omission_reason；不得虚构后续内容')
        return {'duration_seconds':body, **options,'selection_mode':mode,'semantic_review':'agent_required','content':content}
    hook = hook or {}
    if not isinstance(hook, dict): raise ValueError('return_hook需要对象')
    for key in ('question','evidence'):
        if not isinstance(hook.get(key),str) or not hook[key].strip(): raise ValueError('回站钩子缺少'+key)
    if not str(hook.get('track_id','')).isdigit(): raise ValueError('回站钩子需真实track_id')
    a,b=seconds(hook['start']),seconds(hook['end'])
    if a < 0 or b <= a: raise ValueError('回站来源范围无效')
    if str(hook['track_id']) == str(plan.get('track_id')) and any(a < seconds(c['end']) and b > seconds(c['start']) for c in clips):
        raise ValueError('回站钩子应指向本条之外的真实进阶内容')
    return {'duration_seconds':body, **options,'selection_mode':mode,'semantic_review':'agent_required','content':content}


def require_current_speed(job,cfg):
    """Direct subtitle and handoff callers also require the current narration artifact."""
    from artifacts import file_hash
    from subtitle_review import audio_path
    plan = read_json(job/'cut_plan.json', {})
    result = read_json(job/'cut_result.json', {})
    chosen = speed(plan,cfg)
    if result.get('speed', 1) != chosen:
        raise ValueError('倍速已变更，请从 cut_speed 续跑，再使用本次最终音频的真实字幕时码')
    if chosen != 1:
        body = audio_path(job)
        if not body.is_file() or result.get('body_sha256') != file_hash(body):
            raise ValueError('调速后的正文缺失或已改变，请从 cut_speed 恢复')


def validate_job(job):
    plan=read_json(job/'cut_plan.json');result=validate(plan, read_json(job/'config.json', {}))
    import production_checks, subtitle_review
    length=subtitle_review.duration_us(production_checks.source_audio(job))/1e6
    validate_source(plan, length)
    hook=plan['content'].get('return_hook')
    if hook and str(hook['track_id'])==str(plan.get('track_id')):
        if seconds(hook['end'])>length+.12:raise ValueError('回站证据时码超过同声音真实长度；需核对真实后续声音，不能杜撰超出结尾的区间')
    if hook and str(hook['track_id'])!=str(plan.get('track_id')):
        import shared_album
        from artifacts import digest
        cfg=read_json(job/'config.json');tid=str(hook['track_id']);root=shared_album.album_root(cfg)
        cache=read_json(root/'transcripts'/f'{tid}.json',{})
        if cfg.get('master_catalog'):
            import master_catalog
            row=master_catalog.lookup(cfg['master_catalog']['path'],tid)
            if row['album_id']!=str(cfg['album_id']):raise ValueError('回站声音不属于本专辑')
        elif not any(str(r.get('track_id'))==tid for r in shared_album.input_rows(job,cfg)) and str(cache.get('track_id'))!=tid:
            raise ValueError('当前专辑清单/缓存没有该回站声音，不能仅用数字ID放行')
        a=production_checks.path_at(job,hook.get('source_audio',''))
        if not a.is_file() or seconds(hook['end'])>subtitle_review.duration_us(a)/1e6+.12:
            raise ValueError('跨声音回站须用实际已核对的return_hook.source_audio验证时长')
        ref=cache.get('text','') if cache.get('content_hash')==digest(cache.get('text','')) else ''
        if hook.get('source_document'):
            ref=production_checks.path_at(job,hook['source_document']).read_text(encoding='utf-8-sig')
        normalize=lambda text:''.join(ch for ch in text if ch.isalnum())
        quote=normalize(hook['evidence'])
        if not quote or quote not in normalize(ref):raise ValueError('跨声音回站证据不在已核对文稿中；引用实际原句')
    return result
