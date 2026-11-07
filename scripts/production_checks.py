"""Persistent production identities and evidence-bound review gates."""
from pathlib import Path
import math
import re

import config
import document_context
import shared_album
from artifacts import atomic_json, digest, file_hash, file_lock, read_json


def path_at(job, value):
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (job/path).resolve()


def seconds(value):
    if isinstance(value, str) and ':' in value:
        parts = value.replace(',', '.').split(':'); result = 0
        for p in parts: result = result*60 + float(p)
    else: result = float(value)
    if not math.isfinite(result): raise ValueError('非有限时间值')
    return result


def source_audio(job):
    plan = read_json(job/'cut_plan.json', {})
    value = plan.get('source_audio') or read_json(job/'download_result.json', {}).get('path')
    if not value: raise ValueError('缺少源音频路径')
    path = path_at(job, value)
    if not path.is_file(): raise ValueError('源音频不存在')
    return path


def confirm_highlights(manifest, reviewer, evidence, *, mode='manual'):
    manifest = Path(manifest).resolve()
    if mode not in {'manual', 'agent_selection'}: raise ValueError('未知高光登记方式')
    if not reviewer.strip() or not evidence.strip(): raise ValueError('需要实际选定者和选定依据')
    data = read_json(manifest, {}); items = data.get('highlights')
    if not isinstance(items, list) or not items: raise ValueError('需要highlights数组')
    entries, jobs = {}, set()
    for item in items:
        hid = item.get('highlight_id', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', hid) or hid in entries:
            raise ValueError('高光ID必须合法且唯一')
        job = path_at(manifest.parent, item['job_dir'])
        cfg = read_json(job/'config.json', {}); plan = read_json(job/'cut_plan.json', {})
        if job in jobs or not str(item.get('track_id', '')).isdigit() or not str(item.get('summary', '')).strip():
            raise ValueError('任务重复，或缺少track_id/summary')
        if str(plan.get('track_id')) != str(item['track_id']): raise ValueError('候选声音与cut_plan不一致')
        entries[hid] = {'highlight_id':hid, 'track_id':str(item['track_id']), 'job_dir':str(job),
                        'album_id':str(cfg['album_id']), 'summary':item['summary']}
        jobs.add(job)
    lock_path = manifest.with_suffix('.confirmed.json')
    with file_lock(manifest.with_suffix('.identity.lock')):
        previous = read_json(lock_path, {})
        if previous and previous.get('entries') != entries:
            raise ValueError('已确认高光身份不可重排或替换内容；仅允许修改display_order。新候选使用新批次清单')
        for hid, entry in entries.items():
            identity = Path(entry['job_dir'])/'highlight_identity.json'
            old = read_json(identity, {})
            if old and old.get('entry') != entry: raise ValueError('job已绑定另一高光身份')
        atomic_json(lock_path, {'schema':1, 'entries':entries, 'reviewer':reviewer,
                               'evidence':evidence, 'selection_mode':mode})
        for hid, entry in entries.items():
            atomic_json(Path(entry['job_dir'])/'highlight_identity.json',
                        {'entry':entry,'entry_hash':digest(entry),'manifest':str(manifest),'confirmed':str(lock_path)})
    return {'status':'confirmed','highlights':list(entries)}


def register_selected_highlight(job, cfg):
    """Agent's structured plan is the decision; no fabricated user confirmation."""
    if (job/'highlight_identity.json').is_file():
        return identity(job)
    plan = read_json(job/'cut_plan.json', {})
    hid = plan.get('highlight_id') or cfg.get('highlight_id')
    summary = plan.get('summary')
    if not hid or not isinstance(summary, str) or not summary.strip():
        raise ValueError('cut_plan需要highlight_id和summary，用于自动固定高光身份')
    manifest = job/'selected_highlight.json'
    atomic_json(manifest, {'highlights':[{'highlight_id':hid,'track_id':plan.get('track_id'),
                 'summary':summary,'job_dir':str(job.resolve())}]})
    confirm_highlights(manifest, 'agent', 'Agent提交的cut_plan；默认自主选片，无用户逐条批准',
                       mode='agent_selection')
    return identity(job)


def identity(job):
    saved = read_json(job/'highlight_identity.json', {})
    entry = saved.get('entry', {})
    if not entry or saved.get('entry_hash') != digest(entry): raise ValueError('缺少有效高光身份，先confirm-highlights')
    confirmed = read_json(Path(saved['confirmed']), {}).get('entries', {}).get(entry['highlight_id'])
    live = read_json(Path(saved['manifest']), {}).get('highlights', [])
    matching = [i for i in live if i.get('highlight_id') == entry['highlight_id']]
    if len(matching)!=1: raise ValueError('批次清单中的高光ID丢失或重复')
    item = matching[0]
    if (confirmed != entry or path_at(Path(saved['manifest']).parent,item['job_dir']) != job.resolve()
        or str(item['track_id']) != entry['track_id'] or item['summary'] != entry['summary']
        or str(read_json(job/'cut_plan.json', {}).get('track_id')) != entry['track_id']
        or str(read_json(job/'config.json', {}).get('album_id')) != entry['album_id']):
        raise ValueError('高光身份与批次/声音/job不匹配，禁止按排序重新编号')
    return entry


def document_route(job, cfg):
    document_context.ensure_selected(job, cfg)
    context = shared_album.selected_context(job, cfg)
    plan = read_json(job/'cut_plan.json', {})
    ranges = context.get('document_ranges', [])
    clips = plan.get('clips', [])
    gaps = [g for r in ranges for g in r['coverage']['gaps_ms']]
    if clips:
        route = 'local_window_check' if gaps or not ranges or context.get('document_invalid_blocks') else 'local_document_then_final_asr'
    else:
        # Inspect the raw/cache timeline even before any cut plan exists.
        record = read_json(shared_album.album_root(cfg)/'transcripts'/f"{plan.get('track_id','')}.json", {})
        if record.get('origin')=='csv':
            record=read_json(shared_album.album_root(cfg)/'transcripts/timed'/f"{plan.get('track_id','')}.json",{})
        if cfg.get('ai_doc_path'):
            record = document_context.normalize_document(read_json(path_at(job,cfg['ai_doc_path'])))
        route = 'locate_from_document' if record.get('blocks') else 'localize_before_full_track'
    result = {'schema':1,'route':route,'context':context,'selected_gaps_ms':gaps,
              'full_track_asr_allowed':False,'note':'粗时轴用于定位；缺口/无效段须核对局部窗口；最终字幕始终识别最终剪辑音频'}
    atomic_json(job/'document_route.json',result)
    return result


def cut_request(job, cfg):
    import subtitle_review
    route = document_route(job,cfg)
    audio = source_audio(job); duration = subtitle_review.duration_us(audio)/1e6
    plan = read_json(job/'cut_plan.json', {})
    clips = plan.get('clips') or [{'start':0,'end':duration}]
    ranges=[]
    for i,c in enumerate(clips,1):
        start,end=seconds(c['start']),seconds(c['end'])
        if start<0 or end<=start or end>duration+.001: raise ValueError('切点超出源音频')
        ranges.append({'clip_index':i,'start':start,'end':end,'context_start':max(0,start-3),
                       'context_end':min(duration,end+3)})
    basis={'audio_sha256':file_hash(audio),'plan_hash':digest(plan),'context_hash':digest(route['context']),
           'timing':config.load_spec()['timing'],'identity':identity(job)}
    result={'basis':basis,'source_audio':str(audio),'ranges':ranges,'route':route['route'],
            'selected_gaps_ms':route['selected_gaps_ms'],'request_hash':digest(basis)}
    atomic_json(job/'cut_review_request.json',result)
    return result


def cut_ready(job,cfg):
    try:
        request=cut_request(job,cfg); response=read_json(job/'cut_review_response.json',{})
        if response.get('request_hash')!=request['request_hash'] or not response.get('reviewer'): return False
        rows=response.get('clips',[])
        if [r.get('clip_index') for r in rows]!=[r['clip_index'] for r in request['ranges']]: return False
        for row in rows:
            if any(row.get(k) is not True for k in ('start_complete','end_complete','audio_context_checked')): return False
            if not isinstance(row.get('evidence'),str) or not row['evidence'].strip(): return False
        if (request['selected_gaps_ms'] or request['route']=='local_window_check') and response.get('gaps_checked') is not True: return False
        return True
    except (ValueError,OSError,KeyError,TypeError): return False


def storyboard_basis(job):
    parallel = (job/'storyboard_prepare_request.json').is_file()
    if parallel:
        import subtitle_review
        cfg = read_json(job/'config.json', {})
        if not subtitle_review.ready(job, cfg):
            raise ValueError('并行分镜必须在字幕通过后核对定稿')
    return {'audio_sha256':file_hash(__import__('subtitle_review').audio_path(job)),
            'cut_result_hash':file_hash(job/'cut_result.json'),'storyboard_hash':file_hash(job/'storyboard.json'),
            **({'corrected_hash':file_hash(job/'asr_corrected.json'),
                'subtitles_hash':file_hash(job/'subtitles.csv'),
                'copy_hash':file_hash(job/'copy.json'),
                'manifest_hash':file_hash(job/'image_manifest.json'),
                'visual_bible_hash':digest(read_json(job/'visual_bible.json', {}))} if parallel else {}),
            'identity':identity(job)}


def bind_storyboard(job,reviewer,evidence):
    if not reviewer.strip() or not evidence.strip(): raise ValueError('需要实际分镜时码核对者及依据')
    import subtitle_review
    units=read_json(job/'storyboard.json',{}).get('shots',[])
    duration=subtitle_review.duration_us(subtitle_review.audio_path(job))/1e6
    previous=0
    for shot in units:
        a,b=seconds(shot['start']),seconds(shot['end'])
        if a<previous-.001 or abs(a-previous)>.1 or b<=a or b>duration+.1: raise ValueError('分镜时码缺口/重叠/越界')
        previous=b
    if not units or abs(previous-duration)>.1: raise ValueError('分镜未覆盖最终音频')
    import image_pacing
    pacing=image_pacing.validate(units,duration)
    result={'basis':storyboard_basis(job),'reviewer':reviewer,'evidence':evidence,'image_pacing':pacing}
    atomic_json(job/'storyboard_timing_review.json',result);return result


def storyboard_ready(job):
    try: return read_json(job/'storyboard_timing_review.json',{}).get('basis')==storyboard_basis(job)
    except (OSError,ValueError,KeyError):return False


def album_basis(job,cfg):
    text=read_json(job/'copy.json',{}).get('album_name','')
    return {'field':'album_name','album_id':str(cfg['album_id']),'text':text,'text_hash':digest(text),
            'terms':file_hash(config.SKILL_DIR/'references/rejected-terms.csv'),
            'rules':file_hash(config.SKILL_DIR/'references/growth-review-rules.md')}


def review_album_name(job,cfg,reviewer,user_evidence,reason):
    if not all(isinstance(v,str) and v.strip() for v in (reviewer,user_evidence,reason)):
        raise ValueError('需要实际审核者、用户指定作品原名的依据和语义理由')
    result={'basis':album_basis(job,cfg),'reviewer':reviewer,'user_evidence':user_evidence,
            'reason':reason,'decision':'original_work_title','scope':'album_name_only'}
    if not result['basis']['text']: raise ValueError('作品名为空')
    atomic_json(job/'album_name_review.json',result);return result


def album_exception(job,cfg):
    saved=read_json(job/'album_name_review.json',{})
    return (saved.get('basis')==album_basis(job,cfg) and saved.get('decision')=='original_work_title'
            and saved.get('scope')=='album_name_only' and all(saved.get(k) for k in ('reviewer','user_evidence','reason')))
