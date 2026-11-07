"""Bounded multi-job scheduling; review waits never stop unrelated jobs."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading
import time
import json
import uuid
from artifacts import atomic_json, file_lock, read_json
import config
import generate_images
import resident_asr
import timing
import production_io


def run_batch(manifest, workers=2, image_workers=3, download_workers=3):
    import run as runner
    manifest = Path(manifest).expanduser().resolve()
    raw = read_json(manifest, {})
    if any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 8
           for n in (workers, image_workers, download_workers)): raise ValueError('并发数须为1–8的整数')
    values = raw.get('jobs')
    if not isinstance(values, list) or not values: raise ValueError('批次清单需要非空jobs路径数组')
    jobs = []
    for value in values:
        if not isinstance(value, str) or not value: raise ValueError('job必须为目录路径')
        path = Path(value).expanduser()
        path = (manifest.parent/path).resolve() if not path.is_absolute() else path.resolve()
        if path in jobs or not (path/'config.json').is_file(): raise ValueError(f'任务重复或缺少config.json: {path}')
        jobs.append(path)
    preflight = production_io.preflight(jobs)
    atomic_json(manifest.with_suffix('.preflight.json'),preflight)
    if not preflight['ok']:
        result={'status':'failed','phase':'preflight','preflight':preflight,'jobs':{}}
        atomic_json(manifest.with_suffix('.report.json'),result)
        return result
    report_path = manifest.with_suffix('.report.json')
    with file_lock(manifest.with_suffix('.lock'), timeout=2):
        pool = resident_asr.Pool(); previous = resident_asr.ACTIVE; old_gate = generate_images.BATCH_GATE
        if previous is not None: raise ValueError('不支持在同一进程嵌套批次')
        old_downloads = generate_images.BATCH_DOWNLOADS
        downloads = ThreadPoolExecutor(max_workers=download_workers)
        generate_images.BATCH_DOWNLOADS = downloads
        resident_asr.ACTIVE = pool; generate_images.BATCH_GATE = threading.BoundedSemaphore(image_workers)
        start = time.perf_counter()
        result = {'schema': 1, 'run_id': uuid.uuid4().hex, 'started_at': time.time(), 'status': 'running', 'jobs': {}, 'workers': workers, 'image_workers': image_workers, 'download_workers': download_workers}
        def work(job):
            began = time.perf_counter()
            try:
                code = runner.run_pipeline(job)
                state = read_json(job/'state.json', {}).get('stages', {})
                status = ('failed' if code or 'failed' in state.values() else 'awaiting_review' if 'awaiting_human' in state.values()
                          else 'complete' if all(state.get(s) == 'done' for s in runner.state_mod.STAGES) else 'pending')
                return {'status': status, 'exit_code': code, 'seconds': time.perf_counter()-began,
                        'stages': state, 'timing': timing.report(job)}
            except Exception as exc:
                return {'status': 'failed', 'error_type': type(exc).__name__, 'error': str(exc), 'seconds': time.perf_counter()-began}
        try:
            atomic_json(report_path, result)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(work, job): job for job in jobs}
                for future in as_completed(futures):
                    result['jobs'][str(futures[future])] = future.result()
                    atomic_json(report_path, result)
            states = [r['status'] for r in result['jobs'].values()]
            result['status'] = 'failed' if 'failed' in states else 'complete' if all(s == 'complete' for s in states) else 'awaiting_review'
        finally:
            downloads.shutdown(wait=True)
            generate_images.BATCH_DOWNLOADS = old_downloads
            pool.close(); resident_asr.ACTIVE = previous; generate_images.BATCH_GATE = old_gate
            result['wall_seconds'] = time.perf_counter()-start
            if result['status'] == 'running': result['status'] = 'interrupted'
            atomic_json(report_path, result)
            with manifest.with_suffix('.runs.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(result, ensure_ascii=False)+'\n')
        return result
