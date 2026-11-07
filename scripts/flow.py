"""Compact online entrypoint: run deterministic stages, return only actionable state."""
from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
import re
import sys

import config
import creative_options
import production_checks
import production_io
import run
import state
from artifacts import atomic_json, read_json, file_lock


TASKS = {
    'analyze_highlights': ('agent', ['cut_plan.json'], '按完整价值选5–10分钟及以上内容，记录content与真实回站来源；画风按专辑复用'),
    'cut_speed': ('agent', ['cut_review_request.json', 'cut_review_response.json'], '处理切点首尾与局部音频证据；仅疑点交用户'),
    'subtitles': ('agent', ['subtitle_review_request.json', 'subtitle_review_response.json'], '按真实词级时码核对术语、人名、数字、引文和语义；仅疑点复听'),
    'storyboard': ('agent', ['cut_result.json', 'subtitles.csv', 'storyboard.json', 'copy.json', 'image_manifest.json'],
                   '一次写齐分镜、文案、quality=low图片清单；时码由脚本校验'),
    'review_images': ('agent', ['visual_review_request.json','cover_review.json','copy.json','config.json'], '查看本集封面逐字核对标题；首次共用底图检查无字/留白，按真实版本记录，不要求用户逐图确认'),
    'publish_handoff': ('platform', ['upload_request.json', 'uploaded_assets.json'], '上传清单素材并回填URL，无需用户审图'),
}


def summary(job, failure=None):
    saved = state.load_state(job)
    stage = state.first_pending(saved)
    if failure:
        return {'job':str(job), 'status':'failed', 'stage':stage, 'message':str(failure)[:500]}
    if stage is None:
        import assemble_video
        if assemble_video.selected(read_json(job/'config.json',{})) == 'openfilm':
            import openfilm_backend
            finished = openfilm_backend.current(job)
            if finished:
                return {'job':str(job),'status':'render_verified','output':finished['output'],
                        'project_dir':finished['project_dir'],'backend':'openfilm'}
            return {'job':str(job),'status':'ready_for_openfilm','backend':'openfilm',
                    'action':'运行python scripts/assemble_video.py --backend openfilm --job-dir '+str(job)
                             +' --project-dir /独立工程目录 --options /openfilm-options.json'}
        import assemble_draft, assembly_runtime
        finished = assemble_draft.current(job)
        if finished:
            return {'job':str(job),'status':'draft_verified','draft_dir':finished['draft_dir']}
        if assembly_runtime.profile()['mode']=='local':
            if not (job/'upload_request.json').is_file():
                return {'job':str(job),'status':'failed','message':'本地素材交接缺失，请resume'}
            return {'job':str(job),'status':'ready_for_local_assembly',
                    'action':'运行python scripts/assemble_draft.py --job-dir '+str(job)}
        output = job/'capcut_handoff.json'
        if not output.is_file():
            return {'job':str(job), 'status':'failed', 'message':'交接文件缺失，请resume恢复'}
        return {'job':str(job), 'status':'ready_for_capcut', 'handoff':str(output)}
    failed = saved['stages'].get(stage) == 'failed'
    if stage in ('storyboard', 'generate_images', 'prepare_handoff', 'publish_handoff'):
        try: creative_options.selected(read_json(job/'config.json'))
        except ValueError as exc:
            return {'job': str(job), 'status': 'awaiting_user', 'stage': stage,
                    'action': str(exc), 'files': [str(job/'config.json')]}
    actor, files, action = TASKS.get(stage, ('operator', [], '查看日志中的具体失败项后恢复；不要重跑全批'))
    return {'job':str(job), 'status':'failed' if failed else 'awaiting_'+actor,
            'stage':stage, 'action':action,
            'files':[str(job/name) for name in files],
            **({'message':saved['messages'][stage][:500]} if saved.get('messages',{}).get(stage) else {})}


def jobs_in(manifest):
    manifest = Path(manifest).resolve()
    values = read_json(manifest, {}).get('jobs')
    if not isinstance(values, list) or not values:
        raise ValueError('批次需要非空jobs数组')
    jobs = [(Path(x).expanduser() if Path(x).is_absolute() else manifest.parent/x).resolve() for x in values]
    if len(set(jobs)) != len(jobs):
        raise ValueError('重复job路径')
    identities = set()
    for job in jobs:
        cfg = run.load_job_cfg(job)
        plan = read_json(job/'cut_plan.json', {})
        saved = read_json(job/'highlight_identity.json', {}).get('entry', {})
        hid = saved.get('highlight_id') or plan.get('highlight_id') or cfg.get('highlight_id')
        if hid:
            key = (str(cfg['album_id']), hid)
            if key in identities: raise ValueError('批次有重复高光ID')
            identities.add(key)
    return jobs


def create_selection(selection, destination):
    """Initialize a whole batch from one Agent JSON, with stable identities."""
    data = read_json(selection)
    album = str(data['album_id'])
    items = data.get('segments', data.get('highlights'))
    if not album.isdigit() or not isinstance(items, list) or not items:
        raise ValueError('selection需要album_id和非空highlights')
    settings = data.get('config', {})
    if not isinstance(settings, dict) or 'album_id' in settings:
        raise ValueError('config必须为对象，album_id放在顶层')
    settings = {**settings, 'subtitle_review_mode':'full'}
    destination = Path(destination).resolve()
    root = destination.parent
    records = []
    ids = set()
    # Validate the complete selection before mutating existing jobs.
    for item in items:
        hid = item.get('highlight_id', '')
        track = str(item.get('track_id', ''))
        binding=settings.get('master_catalog')
        if binding and track!=str(binding['track_id']):raise ValueError('选段声音ID与用户点选的总表声音不匹配')
        if not isinstance(hid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', hid) or hid in ids:
            raise ValueError('高光ID必须合法且唯一')
        if not track.isdigit() or not isinstance(item.get('summary'), str) or not item['summary'].strip():
            raise ValueError('每条高光需要track_id及summary')
        clips = item.get('clips')
        if not isinstance(clips, list) or not clips:
            raise ValueError('每条高光需要clips源音频范围')
        for clip in clips:
            if production_checks.seconds(clip['start']) < 0 or production_checks.seconds(clip['end']) <= production_checks.seconds(clip['start']):
                raise ValueError('高光时间范围无效')
        import content_selection
        content_selection.validate(item)
        job = root/'jobs'/f'{album}-{hid}'
        oldcfg = read_json(job/'config.json', {})
        if oldcfg and str(oldcfg.get('album_id')) != album:
            raise ValueError('job已属于其他专辑')
        old = read_json(job/'highlight_identity.json', {}).get('entry', {})
        if old and (old.get('highlight_id') != hid or old.get('track_id') != track or old.get('summary') != item['summary']):
            raise ValueError('不可替换已绑定的高光身份；新候选使用新ID')
        ids.add(hid)
        plan = {k:item[k] for k in ('highlight_id','summary','clips','source_audio','speed','content') if k in item}
        plan['track_id'] = track
        records.append((job, oldcfg, plan))
    resolved = creative_options.resolve_album({**settings, 'album_id':album})
    creative_options.selected(resolved)
    settings = {k:v for k,v in resolved.items() if k != 'album_id'}
    for job, oldcfg, plan in records:
        job.mkdir(parents=True, exist_ok=True)
        atomic_json(job/'config.json', {**oldcfg, **settings, 'album_id':album,
                    'highlight_id':plan['highlight_id'],
                    'n_transcripts':settings.get('n_transcripts', oldcfg.get('n_transcripts', 100))})
        import album_visuals
        saved_design=album_visuals.cover_design_record(run.load_job_cfg(job))
        if saved_design:atomic_json(job/'cover_design_reference.json',saved_design)
        atomic_json(job/'cut_plan.json', plan)
        production_checks.register_selected_highlight(job, run.load_job_cfg(job))
    atomic_json(destination, {'schema':1, 'album_id':album, 'selection_mode':'agent_selection',
                'jobs':[str(job.relative_to(root)) for job, _, _ in records]})
    return destination


def execute(jobs, log, manifest=None):
    """Redirect the complete command (including workers), never per-thread stdout."""
    log.parent.mkdir(parents=True, exist_ok=True)
    failures = {}
    try:
        with log.open('a', encoding='utf-8') as output, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            if manifest:
                from batch_runner import run_batch
                result = run_batch(manifest)
                if result.get('phase') == 'preflight':
                    problems = [c['name']+': '+c.get('error','') for c in result['preflight']['checks'] if not c['ok']]
                    failures = {j:'; '.join(problems) for j in jobs}
                for j in jobs:
                    row = result.get('jobs',{}).get(str(j),{})
                    if row.get('error'): failures[j] = row['error']
            else:
                check = production_io.preflight(jobs)
                atomic_json(jobs[0]/'preflight.json', check)
                if not check['ok']:
                    failures[jobs[0]] = '; '.join(c['name']+': '+c.get('error','') for c in check['checks'] if not c['ok'])
                else:
                    run.run_pipeline(jobs[0])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        failures = {j:str(exc) for j in jobs}
    rows = [summary(j, failures.get(j)) for j in jobs]
    status = ('failed' if any(r['status']=='failed' for r in rows) else
              'render_verified' if all(r['status']=='render_verified' for r in rows) else
              'ready_for_openfilm' if all(r['status'] in ('ready_for_openfilm','render_verified') for r in rows) else
              'draft_verified' if all(r['status']=='draft_verified' for r in rows) else
              'ready_for_local_assembly' if all(r['status'] in ('ready_for_local_assembly','draft_verified') for r in rows) else
              'ready_for_capcut' if all(r['status'] in ('ready_for_capcut','draft_verified') for r in rows) else 'awaiting_input')
    result = {'status':status, 'ready':sum(r['status'] in ('ready_for_capcut','ready_for_local_assembly','draft_verified','ready_for_openfilm','render_verified') for r in rows),
              'total':len(rows), 'jobs':rows, 'log':str(log)}
    atomic_json(log.with_suffix('.summary.json'), result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    start = sub.add_parser('start')
    start.add_argument('--album-id')
    start.add_argument('--catalog',type=Path,help='已导入的标准总表JSON')
    start.add_argument('--track-id',help='从总表精确选中这一条声音')
    start.add_argument('--config', type=Path, help='可选平台设置JSON；仅使用已配置的凭据环境变量')
    select = sub.add_parser('select')
    select.add_argument('--input', required=True, type=Path)
    select.add_argument('--manifest', required=True, type=Path)
    batch = sub.add_parser('batch')
    batch.add_argument('--manifest', required=True, type=Path)
    resume = sub.add_parser('resume')
    resume.add_argument('--job-dir', required=True, type=Path)
    a = p.parse_args()
    try:
        manifest = None
        if a.cmd == 'start':
            if a.track_id and not a.catalog:
                candidate=config.SHARED_ROOT/'master-catalog.json'
                if not candidate.is_file():raise ValueError('尚无持久总表索引，请先导入总表，或显式指定--catalog')
                a.catalog=candidate
            if a.catalog:
                if not a.track_id:raise ValueError('总表入口需要--track-id')
                import master_catalog
                row=master_catalog.lookup(a.catalog,a.track_id)
                if a.album_id and a.album_id!=row['album_id']:raise ValueError('专辑ID与总表选中行不一致')
                a.album_id=row['album_id'];a.track_id=row['track_id']
            elif not a.album_id:raise ValueError('需要--album-id，或--catalog与--track-id')
            job_id=a.album_id+'-'+a.track_id+'-analysis' if a.catalog else a.album_id+'-analysis'
            job = run.create_job(a.album_id, None, job_id=job_id)
            if a.config:
                settings = read_json(a.config)
                if not isinstance(settings, dict) or 'album_id' in settings:
                    raise ValueError('平台config须为对象，不可覆盖album_id')
                atomic_json(job/'config.json', {**run.load_job_cfg(job), **settings})
            if a.catalog:master_catalog.configure(job,read_json(job/'config.json'),a.catalog,a.track_id)
            jobs = [job]
            log = job/'flow.log'
        elif a.cmd in {'select','batch'}:
            manifest = a.manifest.resolve()
            if a.cmd == 'select':
                with file_lock(manifest.with_suffix('.selection.lock'), timeout=2):
                    create_selection(a.input.resolve(), manifest)
            jobs = jobs_in(manifest)
            log = manifest.with_suffix('.flow.log')
        else:
            jobs = [a.job_dir.expanduser().resolve()]
            run.load_job_cfg(jobs[0])
            log = jobs[0]/'flow.log'
        result = execute(jobs, log, manifest)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result = {'status':'failed', 'message':str(exc)[:500]}
    print(json.dumps(result, ensure_ascii=False, separators=(',',':')))
    return 1 if result['status']=='failed' else 0


if __name__ == '__main__':
    sys.exit(main())
