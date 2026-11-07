"""Album-wide transcript/audio/dictionary locations shared by highlight jobs."""
from __future__ import annotations

import csv
import io
import time
from pathlib import Path

import config
import document_context
import mosn_album_track as mosn
from artifacts import atomic_bytes, atomic_json, digest, file_lock, read_json


def album_root(cfg: dict) -> Path:
    album = str(cfg['album_id'])
    if not album.isdigit():
        raise ValueError('album_id 必须是数字')
    root = Path(cfg.get('shared_root') or config.SHARED_ROOT).expanduser().resolve()
    return root / 'albums' / album


def input_rows(job: Path, cfg: dict) -> list[dict]:
    if cfg.get('master_catalog'):
        import master_catalog
        return [master_catalog.input_row(job,cfg)]
    raw = cfg.get('csv')
    if not raw:
        import xql_catalog
        snapshot = job / xql_catalog.SNAPSHOT
        return xql_catalog.read_snapshot(snapshot, cfg['album_id']) if snapshot.is_file() else []
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = job / path
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    ids = [r.get('track_id', '').strip() for r in rows]
    if any(not x.isdigit() for x in ids) or len(ids) != len(set(ids)):
        raise ValueError('输入CSV的track_id必须是非空且唯一的数字')
    return rows


def dictionary(cfg: dict) -> dict:
    path = album_root(cfg) / 'dictionary.json'
    value = read_json(path, {'schema': 1, 'entries': []})
    if not isinstance(value, dict) or not isinstance(value.get('entries'), list):
        raise ValueError(f'术语与人名词典格式错误: {path}')
    return value


def import_dictionary(cfg: dict, source: Path):
    value = read_json(source)
    if not isinstance(value, dict) or not isinstance(value.get('entries'), list):
        raise ValueError('词典需要 {"schema":1,"entries":[{"canonical":"姓名","aliases":[],"evidence":"来源"}]}')
    seen = set()
    for entry in value['entries']:
        if not isinstance(entry, dict) or not isinstance(entry.get('canonical'), str) or not entry['canonical'].strip():
            raise ValueError('词典canonical不能为空')
        if entry['canonical'] in seen:
            raise ValueError('词典canonical重复')
        seen.add(entry['canonical'])
        if not isinstance(entry.get('aliases', []), list) or any(not isinstance(x, str) for x in entry.get('aliases', [])):
            raise ValueError('词典aliases必须是字符串数组')
    relations = value.get('relationships', [])
    if not isinstance(relations, list): raise ValueError('relationships必须为数组')
    for relation in relations:
        if not isinstance(relation, dict) or any(not isinstance(relation.get(k), str) or not relation[k].strip()
                for k in ('subject', 'relation', 'object', 'evidence')):
            raise ValueError('人物关系需要subject/relation/object/evidence')
        if relation['subject'] not in seen or relation['object'] not in seen:
            raise ValueError('人物关系两端必须引用词典canonical')
    if 'asr_names' in value:
        import bailian_hotwords
        bailian_hotwords.validate(value)
    path = album_root(cfg) / 'dictionary.json'
    with file_lock(path.with_suffix('.lock')):
        old = read_json(path)
        if old != value:
            if old:
                atomic_json(path.parent / 'dictionary-history' / f'{digest(old)}.json', old)
            atomic_json(path, value)
    return path


def fetch(job: Path, cfg: dict) -> str:
    """Prefer supplied text; cache each remote track with a TTL and content hash."""
    if cfg.get('master_catalog'):
        import master_catalog
        cfg.update(master_catalog.prepare_cover(job,cfg))
    if not cfg.get('csv') and not cfg.get('master_catalog'):
        import xql_catalog
        xql_catalog.ensure_job_catalog(job, cfg)
    rows = input_rows(job, cfg)
    if not cfg.get('csv') and not cfg.get('master_catalog'):
        rows = rows[:max(1, int(cfg.get('n_transcripts', 100)))]
    remote_config = {'mosn_base_url': cfg.get('mosn_base_url') or config.MOSN_BASE_URL}
    if not rows:
        return '无可用 track_id；请检查输入清单或 XQL 查询结果'
    output = []
    failures = 0
    ttl = float(cfg.get('transcript_cache_ttl', 86400))
    for row in rows:
        track_id = row['track_id'].strip()
        supplied = (row.get('transcript') or '').strip()
        origin = 'csv' if supplied else digest(remote_config)
        cache = album_root(cfg) / 'transcripts' / f'{track_id}.json'
        with file_lock(cache.with_suffix('.lock')):
            saved = read_json(cache, {})
            text = supplied
            doc = None
            if not text and saved.get('origin') == origin and digest(saved.get('text', '')) == saved.get('content_hash'):
                if time.time() - saved.get('fetched_at', 0) < ttl:
                    text = saved.get('text', '')
            error = ''
            if not text:
                # Consecutive service failures stop repeated requests for the rest of this batch.
                if failures >= 3:
                    error = '连续3条文稿失败，本批停止请求；可补充CSV transcript后resume'
                else:
                    fetched = mosn.get_track_document_result(track_id, remote_config)
                    payload = fetched.get('payload')
                    if payload is not None: document_context.archive_response(cache, payload)
                    atomic_json(cache.parent / 'diagnostics' / f'{track_id}.json', {k:v for k,v in fetched.items() if k!='payload'})
                    try:
                        doc = document_context.normalize_document(payload) if payload else None
                    except ValueError:
                        doc = None
                    text = doc['text'] if doc else ''
                    if not text.strip():
                        error = fetched['status'] if fetched['status'] != 'ok' else 'normalization_failed'
                        failures = failures+1 if fetched['status']=='access_failed' else 0
                    else:
                        failures = 0
            if text:
                if doc:
                    atomic_json(cache, document_context.record(track_id, origin, doc))
                elif saved.get('text') != text or saved.get('origin') != origin:
                    atomic_json(cache, {'schema': 1, 'track_id': track_id, 'origin': origin,
                        'text': text, 'content_hash': digest(text), 'fetched_at': time.time()})
                elif not supplied and time.time() - saved.get('fetched_at', 0) >= ttl:
                    saved['fetched_at'] = time.time()
                    atomic_json(cache, saved)
            output.append({'album_id': str(cfg['album_id']), 'track_id': track_id,
                           'sound_id': track_id, 'transcript': text, 'error': error})
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=['album_id', 'track_id', 'sound_id', 'transcript', 'error'])
    writer.writeheader()
    writer.writerows(output)
    atomic_bytes(job / f"album_{cfg['album_id']}_transcripts.csv", stream.getvalue().encode('utf-8-sig'))
    return '' if any(r['transcript'] for r in output) else '没有有效文稿，详见文稿CSV的error列'


def selected_context(job: Path, cfg: dict) -> dict:
    plan = read_json(job / 'cut_plan.json', {})
    track_id = str(plan.get('track_id', ''))
    record = read_json(album_root(cfg) / 'transcripts' / f'{track_id}.json', {}) if track_id else {}
    supplied_transcript = record.get('text','') if record.get('origin')=='csv' else ''
    if supplied_transcript:
        timed = read_json(album_root(cfg)/'transcripts/timed'/f'{track_id}.json',{})
        if timed: record=timed
    if cfg.get('ai_doc_path'):
        source = Path(cfg['ai_doc_path']).expanduser()
        if not source.is_absolute():
            source = job / source
        record = document_context.record(track_id, 'local_ai_doc', document_context.normalize_document(read_json(source)))
    if record and digest(record.get('text', '')) != record.get('content_hash'):
        raise ValueError('共享文稿内容校验失败，请重新fetch_transcripts')
    return {'track_id': track_id, **document_context.select(record, plan, read_json(job / 'cut_result.json', {})),
            'dictionary': dictionary(cfg), 'supplied_transcript': supplied_transcript}
