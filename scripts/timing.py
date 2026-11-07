"""Append-only timing events. Stage envelopes and overlapping work are not additive."""
import contextlib
import contextvars
import json
from pathlib import Path
import time
import uuid
from artifacts import atomic_json, file_lock, read_json

JOB = contextvars.ContextVar('timing_job', default=None)
CATEGORIES = ('model_load', 'recognition', 'image_request', 'download', 'review', 'confirmation_wait', 'video_export')


@contextlib.contextmanager
def bind(job):
    token = JOB.set(Path(job).resolve())
    try: yield
    finally: JOB.reset(token)


def emit(category, seconds, status='ok', job=None, **meta):
    job = job or JOB.get()
    if job is None: return
    job = Path(job)
    event = dict(id=uuid.uuid4().hex, category=category, seconds=max(0, seconds),
                 status=status, finished_at=time.time(), **meta)
    with file_lock(job / '.timing.lock'):
        with (job / 'timing_events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')


@contextlib.contextmanager
def span(category, job=None, **meta):
    started = time.time(); clock = time.perf_counter(); state = {'status': 'ok'}
    try: yield state
    except BaseException:
        state['status'] = 'error'; raise
    finally:
        emit(category, time.perf_counter() - clock, state['status'], job,
             started_at=started, **meta)


def mark(job, action, category, key, status='ok', output=None):
    """Persistent manual/confirmation intervals. Repeated wait starts are idempotent."""
    job = Path(job); slot = category + ':' + key
    with file_lock(job / '.timing.lock'):
        path = job / 'timing_open.json'; opened = read_json(path, {})
        if action == 'start':
            if slot in opened:
                if category == 'confirmation_wait': return
                raise ValueError('该计时已开始，先结束或取消')
            opened[slot] = {'category': category, 'key': key, 'started_at': time.time()}
        elif action == 'finish':
            if slot not in opened:
                if category == 'confirmation_wait': return
                raise ValueError('没有对应开始记录，不能把未知耗时记为0')
            event = opened.pop(slot); end = time.time()
            event.update(id=uuid.uuid4().hex, finished_at=end,
                         seconds=max(0, end-event['started_at']), status=status, output=output)
            with (job / 'timing_events.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')
        else: raise ValueError('未知计时动作')
        atomic_json(path, opened)


def report(job):
    job = Path(job)
    with file_lock(job / '.timing.lock'):
        path = job / 'timing_events.jsonl'
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        opened = read_json(job / 'timing_open.json', {})
    now = time.time(); categories = {}
    for category in sorted(set(CATEGORIES) | {e['category'] for e in events}):
        rows = [e for e in events if e['category'] == category]
        live = [e for e in opened.values() if e['category'] == category]
        categories[category] = {'completed_seconds': sum(e['seconds'] for e in rows) if rows else None,
                                'samples': len(rows), 'failed_samples': sum(e['status'] == 'error' for e in rows),
                                'open_seconds': sum(max(0, now-e['started_at']) for e in live) if live else None}
    starts = [e.get('started_at', e['finished_at']-e['seconds']) for e in events]
    starts.extend(e['started_at'] for e in opened.values())
    ends = [e['finished_at'] for e in events]
    if opened: ends.append(now)
    observation = {'first_recorded_at': min(starts) if starts else None,
                   'through_at': max(ends) if ends else None,
                   'elapsed_seconds': max(0, max(ends)-min(starts)) if starts and ends else None}
    result = {'schema': 1, 'job': str(job.resolve()), 'observation': observation,
              'categories': categories, 'open_intervals': list(opened.values()),
              'note': 'null=未测量。并发资源耗时及阶段包络不可相加为端到端耗时；等待截至显式确认或再次执行。外部审核/导出需显式start/finish。'}
    atomic_json(job / 'timing_report.json', result)
    return result
