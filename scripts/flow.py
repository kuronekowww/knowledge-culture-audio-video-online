"""Compact online entrypoint: run deterministic stages, return only actionable state."""
from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
import re
import sys
import time

import config
# Execute the validated interpreter even if the shell's default Python lacks requests.
if __name__=='__main__':
    from runtime_doctor import default_record
    import os
    from artifacts import read_json as _read
    saved=_read(default_record(),{}) or {}
    runner=os.environ.get('KNOWLEDGE_PYTHON') or ((saved.get('python') or {}).get('executable') if saved.get('ok') else None)
    if runner and not os.environ.get('KNOWLEDGE_FLOW_REEXEC') and os.path.abspath(runner)!=os.path.abspath(sys.executable):
        if not os.access(runner,os.X_OK):sys.exit('记录的Python不可执行，请重新运行runtime_doctor.py')
        os.execve(runner,[runner,__file__,*sys.argv[1:]],{**os.environ,'KNOWLEDGE_FLOW_REEXEC':'1'})
import creative_options
import production_checks
import production_io
import run
import state
from artifacts import atomic_json, read_json, file_lock, hash_session


TASKS = {
    'analyze_highlights': ('agent', ['source_preflight.json', 'entry_preflight.json', 'cut_plan.json'], '先核对真实时长≥300秒、正文完整且非仅前言；excerpt记录真实回站来源，full_track可记录免钩子原因'),
    'cut_speed': ('agent', ['cut_review_request.json', 'cut_review_response.json'], '处理切点首尾与局部音频证据；仅疑点交用户'),
    'subtitles': ('agent', ['subtitle_review_request.json', 'subtitle_review_response.json'], '依据已校准句段或原声词级时码核对术语、人名、数字、引文和语义；仅疑点复听'),
    'storyboard': ('agent', ['cut_result.json', 'subtitles.csv', 'storyboard.json', 'copy.json', 'image_manifest.json'],
                   '一次写齐分镜、文案、quality=low图片清单；时码由脚本校验'),
    'review_images': ('agent', ['visual_review_request.json','cover_review.json','copy.json','config.json'], '查看本集封面逐字核对标题；上下包框由脚本生成，按真实版本记录，不要求用户逐图确认'),
    'publish_handoff': ('platform', ['upload_request.json', 'uploaded_assets.json'], '上传清单素材并回填URL，无需用户审图'),
}


def summary(job, failure=None):
    saved = state.load_state(job)
    stage = state.first_pending(saved)
    if failure:
        return {'job':str(job), 'status':'failed', 'stage':stage, 'message':str(failure)[:500]}
    import presentation, delivery
    cfg = read_json(job/'config.json', {})
    import title_review
    title_task = title_review.pending(job, cfg)
    if title_task:
        return title_task
    target = delivery.requested(cfg)
    if presentation.selected(cfg)['mode'] == 'hyperframes' and stage in ('storyboard', 'generate_images', 'prepare_handoff', 'publish_handoff'):
        import web_delivery
        done=web_delivery.current(job,cfg)
        if done:return {'job':str(job),**done}
        return {'job':str(job), 'status':'awaiting_agent_web_animation', 'delivery':target,
                'files':[str(job/'web_animation_request.json')],
                'action':'按网页生产模板继续；默认交可编辑工程；仅在用户明确要求MP4时完成渲染与成片核验'}
    if stage is None:
        import assemble_video
        if assemble_video.selected(read_json(job/'config.json',{})) == 'openfilm':
            import openfilm_backend
            finished = openfilm_backend.current(job)
            if finished:
                return {'job':str(job),'status':'render_verified','output':finished['output'],
                        'project_dir':finished['project_dir'],'backend':'openfilm'}
            if target['target'] in ('project','preview'):
                project=openfilm_backend.inspected_project(job)
                if project:
                    return {'job':str(job),'status':'project_verified' if target['target']=='project' else 'awaiting_preview_playback',
                            'project_dir':str(project),'backend':'openfilm','action':'在OpenFilm Studio打开本工程；若交预览，须实际点击播放、复核时长和推进，再提供打开方式。'}
            return {'job':str(job),'status':'ready_for_openfilm','backend':'openfilm',
                    'action':'运行python scripts/assemble_video.py --backend openfilm --job-dir '+str(job)
                             +' --project-dir /独立工程目录 --options /openfilm-options.json'}
        import assemble_draft, assembly_runtime
        finished = assemble_draft.current(job)
        if finished:
            if target['target']=='mp4':
                import openfilm_backend, verify_native_export
                native=verify_native_export.current(job,finished)
                if native:return {'job':str(job),**native}
                companion=None if target.get('native_capcut_only') else openfilm_backend.current(job)
                if companion:return {'job':str(job),'status':'render_verified','output':companion['output'],'draft_dir':finished['draft_dir'],'renderer':'openfilm'}
                return {'job':str(job),'status':'awaiting_mp4_export','draft_dir':finished['draft_dir'],'delivery':target,
                        'action':'从当前草稿导出MP4，再运行verify_native_export.py --job-dir 任务目录 --mp4 成片；需真实抽帧复核'}
            return {'job':str(job),'status':'awaiting_preview_playback' if target['target']=='preview' else 'draft_verified',
                    'draft_dir':finished['draft_dir'],'delivery':target,
                    'action':'在剪映中打开草稿，核对真实时长、实际点击播放并观察进度，交付打开方式' if target['target']=='preview' else '工程已核验'}
        if assembly_runtime.profile()['mode']=='local':
            if not (job/'upload_request.json').is_file():
                return {'job':str(job),'status':'failed','message':'本地素材交接缺失，请resume'}
            return {'job':str(job),'status':'ready_for_local_assembly',
                    'action':'运行python scripts/assemble_video.py --backend capcut --job-dir '+str(job)}
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
    if stage=='subtitles':
        source_report=read_json(job/'caption_source_report.json',{})
        if source_report:
            labels={'document':'使用已校准的文稿短句','hybrid':'使用文稿短句和原声词级时码',
                    'asr_cache':'复用已有整轨词稿','asr':'使用整轨识别'}
            source=labels.get(source_report.get('route'),'已选择字幕时码')
            if source_report.get('route') in ('document','hybrid'):
                source+=f"（直用 {len(source_report['direct'])} 句，补对齐 {len(source_report['align'])} 段）"
            action=source+'。核对姓名、数值、语义与断句，按审核请求指出的疑窗复听。'
    if cfg.get('production_profile') == title_review.PROFILE:
        if stage == 'storyboard':
            files = ['subtitles.csv','subtitle_review_request.json','storyboard.json','visual_bible.json','copy.json']
            action = '在分镜中引用真实句段/词单元ID、信息点和可见画面，在视觉设定中写封面场景及固定主体；脚本自动编译整套生图清单。'
        elif stage == 'review_images':
            action = '打开 visual_review_request 的封面及首、中、末三张正文图，一次记录封面文字、主体及正文场景/风格；上下包框由脚本生成。'
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


def select_jobs(jobs, names):
    """Accept exact directory names or stable highlight IDs. Preserve manifest order."""
    if not names:
        return jobs
    chosen = set()
    for name in names:
        matches = [j for j in jobs if j.name == name or read_json(j/'config.json', {}).get('highlight_id') == name]
        if len(matches) != 1:
            raise ValueError(f'任务名或 highlight_id 未唯一匹配批次任务: {name}')
        chosen.add(matches[0])
    return [j for j in jobs if j in chosen]


def last_status(jobs, log):
    """Read prior results without re-running preflight, verification, or a pipeline."""
    summary_path = log.with_suffix('.summary.json')
    batch_rows = {r['job']:r for r in read_json(summary_path, {}).get('jobs', [])}
    batch_time = summary_path.stat().st_mtime if summary_path.exists() else 0
    rows = []
    for job in jobs:
        saved = read_json(job/'flow.last.json', {})
        if str(job) in batch_rows and batch_time > saved.get('observed_at', 0):
            row = {**batch_rows[str(job)], 'observed_at':batch_time}
        elif saved:
            row = saved
        else:
            saved_state = state.load_state(job)
            stage = state.first_pending(saved_state)
            row = {'job':str(job), 'status':'no_flow_summary', 'stage':stage,
                   'stage_state':saved_state['stages'].get(stage),
                   'message':saved_state.get('messages', {}).get(stage)}
        rows.append(row)
    return {'status':'last_observed', 'total':len(rows), 'jobs':rows,
            'action':'以上是最近一次运行记录。处理其中待办后只续跑对应任务。'}


def assemble_ready(jobs, manifest):
    """Build ready, local CapCut projects serially. Never opt into a new draft attempt."""
    import assemble_draft, assemble_video, assembly_runtime, delivery, presentation
    if assembly_runtime.profile()['mode'] != 'local':
        raise ValueError('整批本地组装需要 local 运行配置')
    rows = []
    log = manifest.with_suffix('.assemble.log')
    with file_lock(manifest.with_suffix('.lock'), timeout=2), log.open('a', encoding='utf-8') as output:
        for job in jobs:
            try:
                with file_lock(job/'.pipeline.lock', timeout=2), file_lock(job/'.assembly.lock', timeout=2), hash_session():
                    cfg = read_json(job/'config.json')
                    if (presentation.selected(cfg)['mode'] != 'images' or assemble_video.selected(cfg) != 'capcut'
                            or delivery.requested(cfg)['target'] != 'project'):
                        rows.append({'job':str(job),'status':'different_delivery','action':'按本条原先选定的制作方式和交付目标继续。'})
                        continue
                    row = summary(job)
                    if row['status'] == 'ready_for_local_assembly':
                        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                            draft = assemble_draft.assemble(job, cfg)
                        if draft.get('status') != 'draft_verified':
                            raise ValueError('未获得草稿保存核验回执')
                        row = {'job':str(job), 'status':'draft_verified','draft_dir':draft['draft_dir'], 'assembled_now':True}
            except Exception as exc:
                row = {'job':str(job), 'status':'failed', 'stage':'assembly', 'message':str(exc)[:500]}
            rows.append(row)
            atomic_json(job/'flow.last.json', {**row,'observed_at':time.time()})
    result = {'status': 'failed' if any(r['status']=='failed' for r in rows) else
                        'draft_verified' if all(r['status']=='draft_verified' for r in rows) else 'awaiting_input',
              'total':len(jobs),'drafts_verified':sum(r['status']=='draft_verified' for r in rows),
              'assembled_now':sum(bool(r.get('assembled_now')) for r in rows),'jobs':rows,'log':str(log)}
    atomic_json(manifest.with_suffix('.assemble.summary.json'), result)
    return result


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
    import entry_preflight, delivery
    delivery.requested(settings)
    # Review all selected sources together before creating or updating any jobs.
    preflight = entry_preflight.selection(data, Path(selection).resolve().parent)
    atomic_json(Path(selection).with_suffix('.preflight.json'), preflight)
    if not preflight['ok']:
        reasons = ['声音'+r['track_id']+'：'+'；'.join(r['errors']) for r in preflight['tracks'] if not r['ok']]
        raise ValueError('入口预检未通过。'+' / '.join(reasons)+'。详见 '+str(Path(selection).with_suffix('.preflight.json')))
    destination = Path(destination).resolve()
    root = destination.parent
    records = []
    ids = set()
    catalog = data.get('catalog')
    if catalog:
        if settings.get('master_catalog'):
            raise ValueError('整批使用顶层catalog或单声音配置master_catalog，二者选一')
        catalog = (Path(selection).resolve().parent/Path(catalog).expanduser()).resolve()
    multi_track = len({str(item.get('track_id')) for item in items}) > 1
    if multi_track and settings.get('ai_doc_path'):
        raise ValueError('多声音的带时码文稿按 segments[].ai_doc_path 分别绑定')
    doc_owners = {}
    if multi_track and settings.get('user_transcript_path'):
        raise ValueError('多声音的文稿按每条 segments[].user_transcript_path 绑定')
    # Validate the complete selection before mutating existing jobs.
    for item,checked in zip(items,preflight['tracks']):
        item={**item,'clips':checked['clips']}
        if 'speed' in settings or 'speed' in item:
            item['speed'] = checked['speed']
        hid = item.get('highlight_id', '')
        track = str(item.get('track_id', ''))
        individual = {}
        if catalog:
            import master_catalog
            from artifacts import digest
            row = master_catalog.lookup(catalog, track)
            if row['album_id'] != album:
                raise ValueError('选中声音所属专辑与本批不符：'+track)
            individual.update(master_catalog={'path':str(catalog), 'track_id':track,
                'row_sha256':digest(row),'selected_row':row}, requested_track_id=track, album_name=row['album_name'])
        if item.get('user_transcript_path'):
            doc=(Path(selection).resolve().parent/Path(item['user_transcript_path']).expanduser()).resolve()
            if not doc.is_file():
                raise ValueError('本条文稿不存在：'+str(doc))
            individual['user_transcript_path']=str(doc)
        timed_file = item.get('ai_doc_path') or settings.get('ai_doc_path')
        if timed_file:
            import document_context
            source = Path(timed_file).expanduser()
            # Legacy config paths are job-relative. New per-item inputs are selection-relative.
            if not source.is_absolute():
                base = Path(selection).resolve().parent if item.get('ai_doc_path') else root/'jobs'/f'{album}-{hid}'
                source = base/source
            source = source.resolve()
            if doc_owners.setdefault(source,track) != track:
                raise ValueError('不同声音不能使用同一份带时码文稿：'+str(source))
            document_context.local_document(source,track)
            individual['ai_doc_path'] = str(source)
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
        plan = {k:item[k] for k in ('highlight_id','summary','clips','source_audio','speed','content','selection_mode') if k in item}
        if plan.get('source_audio'):
            source = Path(plan['source_audio']).expanduser()
            plan['source_audio'] = str(source.resolve() if source.is_absolute() else (Path(selection).resolve().parent/source).resolve())
        plan['track_id'] = track
        import title_review
        titles = title_review.selection_fields(item['titles']) if 'titles' in item else None
        if not oldcfg or titles or oldcfg.get('production_profile') == title_review.PROFILE:
            plan['title_source_sha256'] = checked['source_sha256']
            individual['production_profile'] = title_review.PROFILE
        if not oldcfg:
            individual['cover_policy_version'] = 2
        records.append((job, oldcfg, plan, titles, individual))
    import presentation
    resolved = creative_options.resolve_album({**settings, 'album_id':album})
    if presentation.selected(resolved)['mode']=='images': creative_options.selected(resolved)
    settings = {k:v for k,v in resolved.items() if k != 'album_id'}
    for job, oldcfg, plan, titles, individual in records:
        job.mkdir(parents=True, exist_ok=True)
        with file_lock(job/'.pipeline.lock', timeout=2):
            atomic_json(job/'config.json', {**oldcfg, **settings, 'album_id':album,
                        'highlight_id':plan['highlight_id'],
                        **individual,
                        'n_transcripts':settings.get('n_transcripts', oldcfg.get('n_transcripts', 100))})
            if titles:
                atomic_json(job/'copy.json', {**read_json(job/'copy.json', {}), **titles})
            import album_visuals
            saved_design=album_visuals.cover_design_record(run.load_job_cfg(job))
            if saved_design:atomic_json(job/'cover_design_reference.json',saved_design)
            atomic_json(job/'cut_plan.json', plan)
            production_checks.register_selected_highlight(job, run.load_job_cfg(job))
    atomic_json(destination, {'schema':1, 'album_id':album, 'selection_mode':'agent_selection',
                'jobs':[str(job.relative_to(root)) for job, _, _, _, _ in records]})
    return destination


@hash_session()
def execute(jobs, log, manifest=None, *, workers=2, image_workers=3, download_workers=3):
    """Redirect the complete command (including workers), never per-thread stdout."""
    log.parent.mkdir(parents=True, exist_ok=True)
    failures = {}
    import title_review
    active = []
    waiting = {}
    for job in jobs:
        try:
            task = title_review.pending(job, read_json(job/'config.json', {}))
            if task:
                waiting[job] = task
            else:
                active.append(job)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            failures[job] = str(exc)
    try:
        with log.open('a', encoding='utf-8') as output, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            if active and manifest:
                from batch_runner import run_batch
                result = run_batch(manifest, workers, image_workers, download_workers, selected_jobs=active)
                if result.get('phase') == 'preflight':
                    problems = [c['name']+': '+c.get('error','') for c in result['preflight']['checks'] if not c['ok']]
                    failures.update({j:'; '.join(problems) for j in active})
                for j in active:
                    row = result.get('jobs',{}).get(str(j),{})
                    if row.get('error'): failures[j] = row['error']
            elif active:
                job = active[0]
                check = production_io.preflight(active)
                atomic_json(job/'preflight.json', check)
                if not check['ok']:
                    failures[job] = '; '.join(c['name']+': '+c.get('error','') for c in check['checks'] if not c['ok'])
                else:
                    run.run_pipeline(job)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        failures.update({j:str(exc) for j in active})
    rows = [waiting.get(j) or summary(j, failures.get(j)) for j in jobs]
    observed_at = time.time()
    for job, row in zip(jobs, rows):
        atomic_json(job/'flow.last.json', {**row, 'observed_at':observed_at})
    status = ('failed' if any(r['status']=='failed' for r in rows) else
              'preview_ready' if all(r['status']=='preview_ready' for r in rows) else
              'project_verified' if all(r['status']=='project_verified' for r in rows) else
              'awaiting_mp4_export' if all(r['status']=='awaiting_mp4_export' for r in rows) else
              'awaiting_preview_playback' if all(r['status']=='awaiting_preview_playback' for r in rows) else
              'render_verified' if all(r['status']=='render_verified' for r in rows) else
              'ready_for_openfilm' if all(r['status'] in ('ready_for_openfilm','render_verified') for r in rows) else
              'draft_verified' if all(r['status']=='draft_verified' for r in rows) else
              'ready_for_local_assembly' if all(r['status'] in ('ready_for_local_assembly','draft_verified') for r in rows) else
              'ready_for_capcut' if all(r['status'] in ('ready_for_capcut','draft_verified') for r in rows) else 'awaiting_input')
    result = {'status':status, 'ready':sum(r['status'] in ('ready_for_capcut','ready_for_local_assembly','draft_verified','ready_for_openfilm','render_verified','preview_ready','project_verified') for r in rows),
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
    pre=sub.add_parser('preflight',help='先检查已提供的候选，不要求ASR、生图凭据或创作方式')
    pre.add_argument('--input',type=Path,required=True);pre.add_argument('--output',type=Path)
    select = sub.add_parser('select')
    select.add_argument('--input', required=True, type=Path)
    select.add_argument('--manifest', required=True, type=Path)
    batch = sub.add_parser('batch')
    batch.add_argument('--manifest', required=True, type=Path)
    batch.add_argument('--only', nargs='+', metavar='ID', help='仅推进这些任务目录名或 highlight_id')
    assemble = sub.add_parser('assemble-ready', help='依次保存当前就绪的纯图片本地剪映草稿')
    assemble.add_argument('--manifest', type=Path, required=True)
    assemble.add_argument('--only', nargs='+', metavar='ID', help='仅组装指定的任务目录名或 highlight_id')
    for parser in (select, batch):
        for option, default in (('workers', 2), ('image-workers', 3), ('download-workers', 3)):
            parser.add_argument('--'+option, type=int, choices=range(1, 9), default=default)
    resume = sub.add_parser('resume')
    resume.add_argument('--job-dir', required=True, type=Path)
    status = sub.add_parser('status', help='只读最近一次待办或结果，不重新核验素材')
    target = status.add_mutually_exclusive_group(required=True)
    target.add_argument('--manifest', type=Path)
    target.add_argument('--job-dir', type=Path)
    a = p.parse_args()
    try:
        manifest = None
        if a.cmd == 'assemble-ready':
            manifest = a.manifest.resolve()
            result = assemble_ready(select_jobs(jobs_in(manifest), a.only), manifest)
            print(json.dumps(result, ensure_ascii=False, separators=(',',':')))
            return 1 if result['status']=='failed' else 0
        if a.cmd == 'status':
            if a.manifest:
                jobs = jobs_in(a.manifest)
                log = a.manifest.resolve().with_suffix('.flow.log')
            else:
                job = a.job_dir.expanduser().resolve()
                if not (job/'config.json').is_file():
                    raise ValueError(f'缺少任务配置: {job}')
                jobs, log = [job], job/'flow.log'
            print(json.dumps(last_status(jobs, log), ensure_ascii=False, separators=(',',':')))
            return 0
        if a.cmd=='preflight':
            import entry_preflight
            result=entry_preflight.selection(read_json(a.input),a.input.resolve().parent)
            atomic_json(a.output or a.input.with_suffix('.preflight.json'),result)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['ok'] else 1
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
            jobs = select_jobs(jobs_in(manifest), getattr(a, 'only', None))
            log = manifest.with_suffix('.flow.log')
        else:
            jobs = [a.job_dir.expanduser().resolve()]
            run.load_job_cfg(jobs[0])
            log = jobs[0]/'flow.log'
        limits = {key:getattr(a, key) for key in ('workers','image_workers','download_workers') if hasattr(a, key)}
        result = execute(jobs, log, manifest, **limits)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result = {'status':'failed', 'message':str(exc)[:500]}
    print(json.dumps(result, ensure_ascii=False, separators=(',',':')))
    return 1 if result['status']=='failed' else 0


if __name__ == '__main__':
    sys.exit(main())
