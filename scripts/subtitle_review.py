"""Grounded subtitle review exchange: raw ASR -> edits -> timed publication cues.

Semantic review is supplied by the skill's AI executor (or an explicitly configured
local command). This module never fabricates a semantic pass or edits source audio.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import math
import unicodedata
from pathlib import Path

import config
import asr_backend
import shared_album
import subtitle_candidates as candidates
import timing
import subtitle_segmentation as segmentation
from artifacts import atomic_bytes, atomic_json, digest, file_hash, file_lock, read_json, verified_file
from audit_text import load_rejected_terms, scan_text

VERSION = 3
CHECKS = ('proper_names', 'semantics', 'content_risk', 'timing_boundaries', 'simplified_chinese', 'segmentation')


def review_version():
    root = Path(__file__).resolve().parent
    contract = root.parent / 'references/subtitle-review.md'
    return {'schema': VERSION, 'policy': segmentation.POLICY,
            'code': {name: file_hash(root / name) for name in ('subtitle_review.py', 'subtitle_segmentation.py', 'subtitle_candidates.py', 'document_context.py', 'shared_album.py', 'subtitle_fast.py')},
            'contract': file_hash(contract)}


def normalized(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKC', text) if c.isalnum())


def trim_text(text: str) -> str:
    text = text.strip()
    while text and (unicodedata.category(text[-1]).startswith('P') or text[-1].isspace()):
        text = text[:-1]
    return text


def join_text(texts: list[str]) -> str:
    import re
    text = ''.join(texts).strip()
    return re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', text)


def micros(seconds) -> int:
    value = float(seconds)
    if not math.isfinite(value):
        raise ValueError('非有限时间值')
    return round(value * 1_000_000)


def timestamp(value: str) -> int:
    h, m, s = value.replace(',', '.').split(':')
    return micros(int(h) * 3600 + int(m) * 60 + float(s))


def audio_path(job: Path) -> Path:
    result = read_json(job / 'cut_result.json', {})
    raw = result.get('segments', [{}])[0].get('path', 'audio/cut/highlight-01.m4a')
    path = Path(raw)
    return path if path.is_absolute() else job / path


def duration_us(path: Path) -> int:
    from entry_preflight import probe
    return micros(probe(path))


def units_from_asr(data: dict, duration: int) -> list[dict]:
    """Use actual segment/word boundaries; never interpolate by text length."""
    candidates = []
    block_ids_by_index = {}
    if isinstance(data.get('transcription'), list):
        for row in data['transcription']:
            if 'offsets' in row:
                start = micros(float(row['offsets']['from']) / 1000)
                end = micros(float(row['offsets']['to']) / 1000)
            else:
                start = timestamp(row['timestamps']['from'])
                end = timestamp(row['timestamps']['to'])
            candidates.append((row['text'], start, end, 'whisper_segment'))
    elif isinstance(data.get('segments'), list):
        for row in data['segments']:
            begin=len(candidates)
            words = row.get('words', [])
            if words and normalized(''.join(w.get('word', w.get('text', '')) for w in words)) == normalized(row['text']):
                candidates.extend((w.get('word', w.get('text', '')), micros(w['start']), micros(w['end']), 'word') for w in words)
            else:
                origin='document_segment' if (data.get('backend')=='verified_timed_document'
                        and row.get('timing_source')=='document_segment') else 'segment'
                candidates.append((row['text'], micros(row['start']), micros(row['end']), origin))
            if data.get('backend')=='verified_timed_document' and row.get('block_ids'):
                for i in range(begin,len(candidates)):
                    block_ids_by_index[i+1]=row['block_ids']
    elif isinstance(data.get('timestamp'), list):
        texts = data.get('text', '').split()
        if len(texts) != len(data['timestamp']):
            raise ValueError('text与timestamp数量不同，不能按字符估算时间')
        candidates.extend((t, micros(pair[0] / 1000), micros(pair[1] / 1000), 'timestamp')
                          for t, pair in zip(texts, data['timestamp']))
    else:
        raise ValueError('不支持的ASR结构；需要transcription/segments或text+timestamp')
    units = []
    previous = 0
    pending_zero = []
    for index, (text, start, end, source) in enumerate(candidates, 1):
        if not normalized(text) or text.strip().startswith('[_'):
            continue
        if '\ufffd' in text:
            raise ValueError('ASR出现不完整Unicode字符，请重新识别/对齐，不能猜补')
        notes = []
        if end > duration and end - duration <= 100_000 and start < duration:
            # Actual media boundary, not a synthetic per-character estimate.
            end = duration
            notes.append('末尾时码按实际音频边界截定，原始JSON完整保留')
        if start < previous or end < start or start < 0 or end > duration:
            raise ValueError(f'ASR时间无效或重叠: {start}..{end}；请重新识别/对齐')
        if start == end:
            if units and units[-1]['end_us'] == start and not pending_zero:
                units[-1]['text'] += text
                units[-1]['raw_segment_ids'].append(index)
                units[-1]['timing_notes'].append('边界上的零时长字并入前一有效片段')
            else:
                pending_zero.append((text, start, index))
            continue
        raw_ids = [index]
        if pending_zero:
            if any(t != start for _, t, _ in pending_zero):
                raise ValueError('零时长文字缺少相邻有效边界，需重新对齐')
            text = ''.join(t for t, _, _ in pending_zero) + text
            raw_ids = [i for _, _, i in pending_zero] + raw_ids
            notes.append('同起点零时长字并入后一有效片段')
            pending_zero = []
        units.append({'id': f'U{len(units)+1:05d}', 'text': text,
                      'start_us': start, 'end_us': end, 'timing_source': source,
                      'raw_segment_ids': raw_ids, 'timing_notes': notes,
                      **({'document_block_ids':block_ids_by_index[index]} if index in block_ids_by_index else {})})
        previous = end
    if not units or pending_zero:
        raise ValueError('ASR没有有效文字及时间边界')
    return units


def suggested_cues(units: list[dict]) -> list[dict]:
    """Timing-grounded draft only; the reviewer chooses semantic boundaries."""
    groups, current = [], []
    for unit in units:
        if unit.get('timing_source')=='document_segment':
            if current:
                groups.append(current)
                current=[]
            groups.append([unit])
            continue
        if current and unit['start_us'] - current[-1]['end_us'] > segmentation.POLICY['pause_candidate_ms'] * 1000:
            groups.append(current)
            current = []
        current.append(unit)
        if any(c in unit['text'] for c in '。！？!?；;'):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [{'unit_ids': [u['id'] for u in group], 'corrected_text': join_text([u['text'] for u in group]),
             'text': trim_text(join_text([u['text'] for u in group])), 'action': 'keep',
             'reason': '', 'evidence': '', 'rule_ids': [], 'needs_listen': False} for group in groups]


def current_basis(job: Path, cfg: dict) -> dict:
    import content_selection
    content_selection.require_current_speed(job,cfg)
    source = audio_path(job)
    raw = job / 'final_transcript.json'
    provenance = read_json(job / 'final_transcript.meta.json', {})
    audio_sha = file_hash(source)
    raw_sha = file_hash(raw)
    if provenance.get('audio_sha256') != audio_sha or provenance.get('transcript_sha256') != raw_sha:
        raise ValueError('ASR来源未绑定当前音频；先运行transcribe，旧稿不能仅凭存在放行')
    context = shared_album.selected_context(job, cfg)
    settings = cfg.get('asr', {})
    if provenance.get('source_kind')=='verified_timed_document':
        import caption_routing
        report=caption_routing.current_report(job,cfg,provenance)
        identity={'backend':caption_routing.TIMED,'source_recipe':report['recipe'],
                  'report_hash':provenance['route_report_hash']}
    else:
        identity = asr_backend.identity(cfg)
        if provenance.get('backend_identity') != identity:
            raise ValueError('ASR后端、模型或运行版本变化，请先运行transcribe')
        if provenance.get('asr_settings_hash') != digest(settings):
            raise ValueError('ASR参数已变化或缺少版本记录，请先运行transcribe')
        prompt = asr_backend.prompt(cfg, context['dictionary'])
        if provenance.get('prompt_hash') != digest(prompt):
            raise ValueError('专名提示已变化，请先运行transcribe')
    return {'schema': VERSION, 'audio_sha256': audio_sha, 'asr_sha256': raw_sha,
            'asr_settings_hash': digest(settings), 'backend_identity': identity,
            'context_hash': digest(context), 'terms_sha256': file_hash(config.SKILL_DIR / 'references/rejected-terms.csv'),
            'rules_sha256': file_hash(config.SKILL_DIR / 'references/growth-review-rules.md'),
            'review_version': review_version(), 'wordlist_mode':cfg.get('content_wordlist_mode','contextual'), 'mode': 'subtitles_only_audio_unchanged'}


def prepare(job: Path, cfg: dict) -> dict:
    import subtitle_fast
    if subtitle_fast.active(cfg): return subtitle_fast.prepare(job, cfg)
    basis = current_basis(job, cfg)
    units = units_from_asr(read_json(job / 'final_transcript.json'), duration_us(audio_path(job)))
    context = shared_album.selected_context(job, cfg)
    if basis['backend_identity'].get('backend')=='verified_timed_document':
        import caption_routing, document_context
        context=caption_routing.mapped_reference(context,read_json(job/caption_routing.REPORT),
                                               document_context.caption_document(job,cfg,fetch=False))
    terms = load_rejected_terms(config.SKILL_DIR / 'references/rejected-terms.csv')
    request = {'basis': basis, 'audio_path': str(audio_path(job).resolve()), 'units': units,
               'context': context, 'matched_terms': scan_text(join_text([u['text'] for u in units]), terms),
               'rules_path': str(config.SKILL_DIR / 'references/growth-review-rules.md'),
               'review_contract': str(config.SKILL_DIR / 'references/subtitle-review.md'),
               'response_contract': {'preferred': 'accept_patches',
                   'accept': '审核完全部候选后写 remaining，只在 patches 列出修改项；也支持逐项 candidate_id 数组',
                   'patches': '仅问题项输出修正；仍需完整语义、内容与分句检查',
                   'legacy_full_cues_supported': True},
               'segmentation_policy': segmentation.POLICY,
               'suggested_cues': suggested_cues(units),
               'alignment_issues': [u['id'] for u in units if u['end_us']-u['start_us'] > segmentation.POLICY['max_seconds'] * 1_000_000]}
    if basis['backend_identity'].get('backend')=='verified_timed_document':
        report=read_json(job/'caption_source_report.json')
        request['timing_source']={'route':report['route'],'report':str(job/'caption_source_report.json'),
                'wordless_observations':[r for r in report.get('phrase_alignment',[])
                        if r.get('status')=='no_words_detected'
                        and r['end_us']-r['start_us']>=segmentation.POLICY['max_seconds']*1_000_000],
                'gap_review':'对列出的无词区间逐个听实际正文。无漏话时用 gap_checks 逐项记录 start_us/end_us 和 observation；若含漏话在该窗重新识别后再核对。'}
    # Keep a snapshot stable while an external reviewer is preparing a delta response.
    previous = read_json(job / 'subtitle_review_request.json', {})
    if previous.get('basis') == basis and previous.get('units') == units:
        if previous.get('request_hash') == candidates.request_hash(previous):
            return previous
    with timing.span('subtitle_presegment') as measured:
        request['suggested_cues'], request['document_differences'] = candidates.prepare_candidates(
            units, context, request['suggested_cues'], segmentation.POLICY,
            segmentation.protected_phrases(context, {}))
        request['paragraph_cache_hits'] = candidates.reuse(request, shared_album.album_root(cfg))
        measured['cache_hits'] = request['paragraph_cache_hits']
    request['request_hash'] = candidates.request_hash(request)
    atomic_json(job / 'subtitle_review_request.json', request)
    # Immutable evidence of the exact ASR input for this review revision.
    atomic_json(job / 'subtitle_history' / request['request_hash'] / 'asr_raw.json', read_json(job / 'final_transcript.json'))
    return request


def sequence_hits(cues: list[dict], terms: list[str]) -> list[dict]:
    """Scan visible text across adjacent cues, retaining the contributing IDs."""
    groups, current = [], []
    for cue in cues:
        if current and cue['start_us'] - current[-1]['end_us'] > 1_000_000:
            groups.append(current)
            current = []
        current.append(cue)
    if current:
        groups.append(current)
    return [{'cue_ids': [c['id'] for c in g], 'terms': hits} for g in groups
            if (hits := scan_text(join_text([c['text'] for c in g]), terms))]


def csv_bytes(cues: list[dict]) -> bytes:
    def clock(us):
        ms = us // 1000
        return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d}.{ms%1000:03d}'
    out = io.StringIO(newline='')
    w = csv.DictWriter(out, fieldnames=['id', 'start', 'end', 'text'])
    w.writeheader()
    for c in cues:
        if c['text']:
            w.writerow({'id': c['id'], 'start': clock(c['start_us']), 'end': clock(c['end_us']), 'text': c['text']})
    return out.getvalue().encode('utf-8-sig')


def srt_bytes(csv_data):
    rows = csv.DictReader(io.StringIO(csv_data.decode('utf-8-sig')))
    blocks = [f"{i}\n{row['start'].replace('.', ',')} --> {row['end'].replace('.', ',')}\n{row['text']}\n"
              for i, row in enumerate(rows, 1)]
    return ('\n'.join(blocks)+'\n').encode('utf-8-sig')


def coverage_failure(job,request,response,number,expected,actual):
    from collections import Counter
    source=[u['id'] for u in request['units']]
    source_set=set(source)
    submitted=[uid for row in response.get('cues',[]) if isinstance(row,dict) and isinstance(row.get('unit_ids'),list)
               for uid in row['unit_ids'] if isinstance(uid,str)]
    counts=Counter(submitted)
    diagnostic={'request_hash':request['request_hash'],'cue_number':number,'expected_next':expected,'actual':actual,
                'missing_unit_ids':[uid for uid in source if uid not in counts],
                'duplicate_unit_ids':[uid for uid,n in counts.items() if n>1],
                'unknown_unit_ids':[uid for uid in counts if uid not in source_set],'expected_units':len(source),'submitted_units':len(submitted)}
    path=job/'subtitle_coverage_diagnostic.json';atomic_json(path,diagnostic)
    raise ValueError(f'第{number}条unit_ids覆盖/顺序错误，期望{expected[:8]}，实际{actual[:8]}；诊断：{path}')


def apply_review(job: Path, cfg: dict, response_path: Path | None = None) -> dict:
    import subtitle_fast
    if subtitle_fast.active(cfg): return subtitle_fast.apply_review(job, cfg, response_path)
    request = prepare(job, cfg)
    response = read_json(response_path or job / 'subtitle_review_response.json')
    if not isinstance(response, dict) or response.get('request_hash') != request['request_hash']:
        raise ValueError('缺少当前版本语义复核结果，或request_hash已过期')
    if not isinstance(response.get('reviewer'), str) or not response['reviewer'].strip():
        raise ValueError('必须记录实际reviewer')
    if any(response.get('checks', {}).get(name) is not True for name in CHECKS):
        raise ValueError('专名、语义、内容、时码、简体及分句检查均须由审核者完成')
    gaps=(request.get('timing_source') or {}).get('wordless_observations',[])
    if gaps:
        checked=response.get('gap_checks',[])
        if not isinstance(checked,list) or any(not isinstance(row,dict) for row in checked):
            raise ValueError('gap_checks 须对应每个已实听的无词区间')
        expected={(row['start_us'],row['end_us']) for row in gaps}
        actual={(row.get('start_us'),row.get('end_us')) for row in checked
                if row.get('no_missing_speech') is True and str(row.get('observation') or '').strip()}
        if expected!=actual or len(checked)!=len(expected):
            raise ValueError('请实听 timing_source.wordless_observations，逐项记录 gap_checks：start_us/end_us/no_missing_speech/observation；缺字的区间须补对齐')
    submitted_response = response
    if response.get('accept') == 'remaining':
        try:
            patched = {cid for change in response.get('patches', []) for cid in change['candidate_ids']}
        except (KeyError, TypeError) as exc:
            raise ValueError('patches 须为含 candidate_ids 的修改项数组') from exc
        response = {**response, 'accept': [row['candidate_id'] for row in request['suggested_cues']
                                          if row['candidate_id'] not in patched]}
    accepted = response.get('accept', [])
    response = candidates.expand(request, response)
    raw_units = request['units']
    unit_map = {u['id']: u for u in raw_units}
    seen, cues, changes, pending = [], [], [], []
    for number, row in enumerate(response.get('cues', []), 1):
        ids = row.get('unit_ids')
        if not isinstance(ids, list) or not ids or any(x not in unit_map for x in ids):
            coverage_failure(job,request,response,number,[u['id'] for u in raw_units[len(seen):len(seen)+1]],ids if isinstance(ids,list) else [])
        # Strict source-order coverage also forbids duplicates and invented timestamps.
        expected = [u['id'] for u in raw_units[len(seen):len(seen) + len(ids)]]
        if ids != expected:
            coverage_failure(job,request,response,number,expected,ids)
        seen.extend(ids)
        units = [unit_map[x] for x in ids]
        raw = join_text([u['text'] for u in units])
        corrected = row.get('corrected_text')
        text = row.get('text')
        action = row.get('action')
        if not isinstance(corrected, str) or not normalized(corrected) or not isinstance(text, str):
            raise ValueError('corrected_text必须保留忠实校正内容，text必须是字符串')
        if action != 'keep':
            raise ValueError('知识文化字幕仅允许keep与有依据的识别纠错；风险内容请换选段，不改字幕掩盖原声')
        if any(k in row for k in ('start', 'end', 'start_us', 'end_us')):
            raise ValueError('审核结果不得自行提供时间；时码由unit_ids确定')
        reason, evidence = row.get('reason', ''), row.get('evidence', '')
        changed = normalized(raw) != normalized(corrected)
        if changed and (not isinstance(reason, str) or not reason.strip() or not isinstance(evidence, str) or not evidence.strip()):
            raise ValueError(f'第{number}条识别校正必须有reason和evidence；unit_ids={ids[:8]}')
        if action == 'keep' and normalized(corrected) != normalized(text):
            raise ValueError('keep不能改变校正后的语义文字；内容改动用rewrite并记录原因')
        if action == 'omit' and text:
            raise ValueError('omit的text必须为空')
        if action != 'omit' and not normalized(text):
            raise ValueError('非omit字幕不能为空')
        if action != 'keep' and (not isinstance(reason, str) or not reason.strip() or not row.get('rule_ids')):
            raise ValueError('字幕风险处理必须说明reason和rule_ids')
        if not isinstance(row.get('rule_ids', []), list) or any(not isinstance(x, str) for x in row.get('rule_ids', [])):
            raise ValueError('rule_ids必须是字符串数组')
        if '\n' in text or '\r' in text:
            raise ValueError('每条字幕须为单行')
        cue = {'id': f'S{number:03d}', 'unit_ids': ids, 'start_us': units[0]['start_us'],
               'end_us': units[-1]['end_us'], 'raw_text': raw, 'corrected_text': corrected,
               'text': trim_text(text), 'action': action}
        needs_listen = row.get('needs_listen', False)
        if not isinstance(needs_listen, bool):
            raise ValueError('needs_listen必须是布尔值')
        issues = []
        if needs_listen:
            issues.append(reason or '审核者标记需复听')
        if issues:
            pending.append({'cue_id': cue['id'], 'start_us': cue['start_us'],
                            'end_us': cue['end_us'], 'kind': 'listen', 'issues': issues, 'text': raw})
        cues.append(cue)
        changes.append({**cue, 'reason': reason, 'evidence': evidence, 'rule_ids': row.get('rule_ids', []),
                        'recognition_corrected': changed, 'needs_listen': needs_listen,
                        'boundary_reason': row.get('boundary_reason', ''),
                        'short_utterance_reason': row.get('short_utterance_reason', ''),
                        'pause_reason': row.get('pause_reason', ''),
                        'alignment_review': row.get('alignment_review')})
    if seen != [u['id'] for u in raw_units]:
        coverage_failure(job,request,response,len(cues)+1,[u['id'] for u in raw_units[len(seen):]],[])
    if not any(c['text'] for c in cues):
        raise ValueError('不能把全部字幕删除后判定通过')
    terms = load_rejected_terms(config.SKILL_DIR / 'references/rejected-terms.csv')
    hits = sequence_hits(cues, terms)
    for hit in (hits if cfg.get('content_wordlist_mode') == 'strict' else []):
        pending.append({'cue_ids': hit['cue_ids'], 'kind': 'content', 'issues': ['发布字幕仍命中拒审词'], 'terms': hit['terms']})
    phrases = segmentation.protected_phrases(request['context'], response)
    segmentation_issues = segmentation.check(cues, response['cues'], unit_map, phrases)
    pending.extend(segmentation_issues)
    if current_basis(job, cfg) != request['basis']:
        raise ValueError('审核期间输入已变化，请重新准备请求')
    status = 'needs_review' if pending else 'passed'
    reviewed = {'schema': VERSION, 'request_hash': request['request_hash'], 'basis': request['basis'],
                'reviewer': response['reviewer'], 'checks': response['checks'], 'status': status,
                'audio_modified': False, 'review_scope': 'subtitles_only',
                'wordlist_mode':cfg.get('content_wordlist_mode','contextual'), 'wordlist_hits':hits,
                'decision_summary': {'format': 'full_cues' if 'cues' in submitted_response else 'accept_patches',
                    'accepted_candidates': len(accepted),
                    'patched_candidates': sum(len(p.get('candidate_ids', [])) for p in submitted_response.get('patches', [])),
                    'output_cues': len(cues)},
                'cues': cues, 'changes': changes, 'pending': pending,
                'segmentation': {'policy': segmentation.POLICY, 'protected_phrases': phrases,
                                 'issues': segmentation_issues}}
    history = job / 'subtitle_history' / request['request_hash'] / digest(response)
    atomic_json(history / 'submitted_response.json', submitted_response)
    atomic_json(history / 'review_response.json', response)
    atomic_json(history / 'subtitle_review.json', reviewed)
    atomic_json(job / 'asr_corrected.json', {'request_hash': request['request_hash'],
                'cues': [{k: c[k] for k in ('id', 'unit_ids', 'start_us', 'end_us', 'corrected_text')} for c in cues]})
    atomic_json(job / 'subtitle_listen_queue.json', [p for p in pending if p['kind'] == 'listen'])
    atomic_json(job / 'subtitle_repair_queue.json', [p for p in pending if p['kind'] != 'listen'])
    atomic_bytes(job / 'subtitles.preview.csv', csv_bytes(cues))
    if status == 'passed':
        data = csv_bytes(cues)
        atomic_bytes(job / 'subtitles.csv', data)
        atomic_bytes(job / 'subtitles.srt', srt_bytes(data))
        reviewed['srt_sha256'] = file_hash(job / 'subtitles.srt')
        reviewed['subtitles_sha256'] = file_hash(job / 'subtitles.csv')
        candidates.save_verified(request, response, shared_album.album_root(cfg))
        cached = shared_album.album_root(cfg) / 'subtitle-reviews' / request['request_hash']
        with file_lock(cached / 'review.lock'):
            atomic_json(cached / 'response.json', submitted_response)
            atomic_json(cached / 'record.json', {'sha256': file_hash(cached / 'response.json')})
    atomic_json(job / 'subtitle_review.json', reviewed)
    atomic_json(history / 'subtitle_review.json', reviewed)
    return reviewed


def checked_srt(job, reviewed, *, repair=False):
    expected = reviewed.get('srt_sha256')
    path = job / 'subtitles.srt'
    if not expected or verified_file(path, expected):
        return True
    csv_path = job / 'subtitles.csv'
    if not repair or not csv_path.is_file():
        return False
    data = csv_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != reviewed.get('subtitles_sha256'):
        return False
    rendered = srt_bytes(data)
    if hashlib.sha256(rendered).hexdigest() != expected:
        return False
    atomic_bytes(path, rendered)
    return True


def ready(job: Path, cfg: dict, *, repair_srt=False) -> bool:
    import subtitle_fast
    if subtitle_fast.active(cfg): return subtitle_fast.ready(job, cfg)
    try:
        reviewed = read_json(job / 'subtitle_review.json', {})
        return (reviewed.get('status') == 'passed' and reviewed.get('basis') == current_basis(job, cfg)
                and file_hash(job / 'subtitles.csv') == reviewed.get('subtitles_sha256')
                and checked_srt(job, reviewed, repair=repair_srt))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'apply'])
    parser.add_argument('--job-dir', type=Path, required=True)
    parser.add_argument('--response', type=Path)
    args = parser.parse_args()
    job = args.job_dir.expanduser().resolve()
    cfg = read_json(job / 'config.json')
    try:
        with file_lock(job / '.pipeline.lock', timeout=2):
            if args.command == 'prepare':
                import document_context
                document_context.ensure_selected(job, cfg)
            result = prepare(job, cfg) if args.command == 'prepare' else apply_review(job, cfg, args.response)
        print(result.get('status', 'prepared') + ': ' + result['request_hash'])
        return 0 if result.get('status', 'prepared') != 'needs_review' else 3
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'字幕复核失败: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
