"""Read selected AI documents and preserve their native segment timing for acoustic verification."""
from __future__ import annotations

import math
import re
import time
from pathlib import Path

from artifacts import atomic_json, digest, file_lock, read_json


NORMALIZER_VERSION = 3


def normalize_document(payload):
    rows = payload.get('body', []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError('AI文稿body必须是段落数组')
    blocks, texts, warnings, invalid = [], [], [], []
    previous_start, furthest_end = -1, -1
    for index, row in enumerate(rows):
        item = {'id': f'D{index + 1:04d}', 'raw_index': index, 'raw': row}
        if not isinstance(row, dict) or not isinstance(row.get('text'), str):
            invalid.append({**item, 'reason': 'missing_text'}); warnings.append(f'段落{index+1}缺少文字')
            continue
        text = row['text'].strip()
        if not text or re.match(r'^文稿版本\s*[:：]', text):
            continue
        texts.append(text)
        try:
            if isinstance(row.get('startTime'), bool) or isinstance(row.get('endTime'), bool):
                raise ValueError('boolean_time')
            start, end = float(row['startTime']), float(row['endTime'])
            if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
                raise ValueError('invalid_range')
        except (ValueError, TypeError, KeyError):
            invalid.append({**item, 'text': text, 'reason': 'invalid_time'})
            warnings.append(f'段落{index+1}时码无效，其他段落仍保留')
            continue
        flags = []
        if start < previous_start: flags.append('out_of_order')
        if start < furthest_end: flags.append('overlap')
        if flags: warnings.append(f'段落{index+1}: ' + ','.join(flags))
        blocks.append({'id': item['id'], 'raw_index': index, 'start_ms': start, 'end_ms': end, 'text': text, 'flags': flags})
        previous_start, furthest_end = start, max(furthest_end, end)
    blocks.sort(key=lambda b: (b['start_ms'], b['raw_index']))
    return {'text': ''.join(texts), 'blocks': blocks, 'warnings': warnings, 'invalid_blocks': invalid,
            'normalizer_version': NORMALIZER_VERSION, 'raw_response': payload,
            'diagnostics': {'spoken_rows': len(texts), 'valid_rows': len(blocks),
                            'invalid_rows': len(invalid), 'coverage': coverage(blocks)}}


def coverage(blocks, start=None, end=None):
    if start is None: start = min((b['start_ms'] for b in blocks), default=0)
    if end is None: end = max((b['end_ms'] for b in blocks), default=start)
    intervals = []
    for block in sorted(blocks, key=lambda b: b['start_ms']):
        left, right = max(start, block['start_ms']), min(end, block['end_ms'])
        if right <= left: continue
        if intervals and left <= intervals[-1][1]: intervals[-1][1] = max(intervals[-1][1], right)
        else: intervals.append([left, right])
    gaps, cursor = [], start
    for left, right in intervals:
        if left > cursor: gaps.append([cursor, left])
        cursor = right
    if cursor < end: gaps.append([cursor, end])
    covered = sum(b-a for a,b in intervals)
    return {'range_ms': [start, end], 'covered_ms': covered, 'ratio': covered/(end-start) if end>start else None,
            'gaps_ms': gaps, 'intervals_ms': intervals,
            'note': '范围仅指文稿时间窗覆盖；重叠计一次，缺口不等于有声漏字，须核对局部音频'}


def archive_response(cache_path, payload):
    archive = cache_path.parent / 'raw' / cache_path.stem / (digest(payload) + '.json')
    if not archive.exists(): atomic_json(archive, payload)
    return str(archive)


def record(track_id, origin, doc):
    return {'schema': 3, 'track_id': str(track_id), 'origin': origin, **doc,
            'content_hash': digest(doc['text']), 'blocks_hash': digest(doc['blocks']), 'fetched_at': time.time()}


def local_document(source, track):
    source = Path(source)
    data = read_json(source)
    if data is None:
        raise ValueError('指定的带时码 AI 文稿不存在：'+str(source))

    def check_track(row):
        if isinstance(row, dict):
            for key in ('track_id','trackId'):
                if key in row and str(row[key]) != str(track):
                    raise ValueError('AI 文稿所标的声音 ID 与本条任务不符：'+str(source))

    check_track(data)
    payload = data.get('raw_response', data) if isinstance(data, dict) else data
    check_track(payload)
    rows = payload.get('body') if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError('ai_doc_path 需要本条声音的原始 body 分段数组；普通文稿请用 user_transcript_path')
    for row in rows:
        check_track(row)
    doc = normalize_document(payload)
    if not doc['text']:
        raise ValueError('本地 AI 文稿未包含可用正文：'+str(source))
    return {**doc, 'track_id':str(track), 'origin':'local_ai_doc',
            'content_hash':digest(doc['text']), 'blocks_hash':digest(doc['blocks'])}


def caption_document(job, cfg, *, fetch=True):
    """Read the full selected-track timeline before correcting an offset or intersecting clips."""
    import shared_album
    track = str(read_json(job/'cut_plan.json', {}).get('track_id',''))
    if not track.isdigit():
        return None
    if cfg.get('ai_doc_path'):
        return local_document(job/Path(cfg['ai_doc_path']).expanduser(), track)
    primary = shared_album.album_root(cfg)/'transcripts'/(track+'.json')
    def cached():
        for path in (primary, primary.parent/'timed'/primary.name):
            saved = read_json(path, {})
            if not saved or saved.get('origin')=='csv':
                continue
            if saved.get('track_id') is not None and str(saved['track_id']) != track:
                raise ValueError('缓存的 AI 文稿与任务声音 ID 不一致：'+track)
            if saved.get('normalizer_version') != NORMALIZER_VERSION and saved.get('raw_response') is not None:
                doc = normalize_document(saved['raw_response'])
                saved = {**saved, **doc, 'blocks_hash':digest(doc['blocks']), 'content_hash':digest(doc['text'])}
            if saved.get('blocks'):
                if digest(saved['blocks'])!=saved.get('blocks_hash') or digest(saved.get('text',''))!=saved.get('content_hash'):
                    raise ValueError('带时码文稿缓存校验失败：'+track)
                return saved
        return None
    saved = cached()
    if saved is None and fetch:
        ensure_selected(job, cfg, for_captions=True)
        saved = cached()
    return saved


def ensure_selected(job, cfg, *, for_captions=False):
    """Upgrade only the selected legacy remote transcript; CSV text stays authoritative."""
    import config
    import mosn_album_track as mosn
    import shared_album
    import timing
    if cfg.get('user_transcript_path'):
        supplied_reference(job, cfg)  # Verify the actual supplied document before any remote fetch.
        if not cfg.get('ai_doc_path') and not for_captions: return
    plan = read_json(job / 'cut_plan.json', {})
    track = str(plan.get('track_id', ''))
    if not track.isdigit():
        return
    path = shared_album.album_root(cfg) / 'transcripts' / f'{track}.json'
    local = cfg.get('ai_doc_path')
    with file_lock(path.with_suffix('.lock')):
        saved = read_json(path, {})
        if local:
            # Per-job overrides must not replace another job's shared album transcript.
            source = Path(local).expanduser()
            if not source.is_absolute():
                source = job / source
            local_document(source, track)
            return
        if saved.get('raw_response') is not None and saved.get('normalizer_version') != NORMALIZER_VERSION:
            raw_path = archive_response(path, saved['raw_response'])
            saved = {**record(track, saved.get('origin'), normalize_document(saved['raw_response'])), 'raw_path': raw_path}
            atomic_json(path, saved)
        if saved.get('origin') == 'csv' or (cfg.get('user_transcript_path') and not saved.get('blocks')):
            # Preserve supplied text; fetch timing reference in a separate cache.
            path = path.parent/'timed'/path.name
            saved = read_json(path,{})
        remote = {'mosn_base_url': cfg.get('mosn_base_url') or config.MOSN_BASE_URL}
        fresh = time.time() - saved.get('fetched_at', 0) < float(cfg.get('transcript_cache_ttl', 86400))
        if saved.get('normalizer_version') == NORMALIZER_VERSION and saved.get('origin') == digest(remote) and fresh:
            return
        attempt_path = job / 'ai_doc_fetch_status.json'
        attempt = read_json(attempt_path, {})
        attempt_key = digest({'track': track, 'remote': remote, 'saved': saved, 'cache_path':str(path)})
        if attempt.get('key') == attempt_key and time.time() - attempt.get('at', 0) < 300:
            return
        with timing.span('document_fetch', track_id=track) as measured:
            fetched = mosn.get_track_document_result(track, remote)
            payload = fetched.get('payload')
            raw_path = archive_response(path, payload) if payload is not None else None
            try:
                doc = normalize_document(payload) if payload else None
            except ValueError:
                doc = None
            if doc and doc['text']:
                atomic_json(path, {**record(track, digest(remote), doc), 'raw_path': raw_path})
                status = 'cached'
            else:
                measured['status'] = 'error'
                status = fetched['status'] if fetched['status'] != 'ok' else 'normalization_failed'
            atomic_json(attempt_path, {'key': attempt_key, 'at': time.time(), 'status': status})


def supplied_reference(job, cfg):
    """A plain user transcript is semantic context, never fabricated word timing."""
    from artifacts import file_hash
    raw = cfg.get('user_transcript_path')
    if not raw: return {}
    path = Path(raw).expanduser()
    if not path.is_absolute(): path = Path(job)/path
    if path.suffix.lower() not in ('.txt', '.md'):
        raise ValueError('user_transcript_path支持UTF-8 TXT/Markdown；带时码JSON用ai_doc_path')
    text = path.read_text(encoding='utf-8-sig').strip()
    if not text: raise ValueError('用户文稿为空，不能静默忽略')
    return {'supplied_transcript':text, 'supplied_document':{'path':str(path.resolve()), 'sha256':file_hash(path),
            'role':'semantic_reference_only', 'not_instructions':True}}


def select(record_value, plan, cut_result):
    """Preserve clip order (including repeated/reordered source ranges)."""
    if record_value.get('raw_response') is not None and record_value.get('normalizer_version') != NORMALIZER_VERSION:
        record_value = record('', '', normalize_document(record_value['raw_response']))
    blocks = record_value.get('blocks', [])
    if blocks and digest(blocks) != record_value.get('blocks_hash'):
        raise ValueError('共享文稿段落校验失败，请重新获取文稿')
    clips = plan.get('clips', []) if plan else cut_result.get('segments', [{}])[0].get('clips', [])
    if not blocks or not clips:
        return {'transcript': record_value.get('text', ''), 'document_mode': 'plain_reference',
                'document_ranges': [], 'document_warnings': record_value.get('warnings', []), 'document_invalid_blocks': record_value.get('invalid_blocks', [])}
    ranges = []
    for number, clip in enumerate(clips, 1):
        def milliseconds(value):
            total=0
            for part in str(value).replace(',', '.').split(':'): total=total*60+float(part)
            return total*1000
        start, end = milliseconds(clip['start']), milliseconds(clip['end'])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError('剪辑范围无效，不能选取局部文稿')
        hits = [i for i, b in enumerate(blocks) if b['start_ms'] < end and b['end_ms'] > start]
        selected = [blocks[i] for i in hits]
        neighbors = [blocks[i] for i in sorted({j for i in hits for j in (i - 1, i + 1)
                                               if 0 <= j < len(blocks) and j not in hits})]
        ranges.append({'clip_index': number, 'source_start_ms': start, 'source_end_ms': end,
                       'blocks': selected, 'neighbors': neighbors, 'coverage': coverage(blocks, start, end)})
    return {'transcript': '\n'.join(b['text'] for r in ranges for b in r['blocks']),
            'document_mode': 'local_timed_reference', 'document_ranges': ranges,
            'document_warnings': record_value.get('warnings', []), 'document_invalid_blocks': record_value.get('invalid_blocks', [])}
