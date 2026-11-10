"""线上知识文化长视频前序编排器（止于素材与时间线交接）。

用法：
  python3 run.py run --album-id 126589340 [--csv 表格.csv]
  python3 run.py resume --job-dir jobs/<album_id>
  python3 run.py status --job-dir jobs/<album_id>
  python3 run.py force-stage --job-dir jobs/<album_id> --stage prepare_handoff

10 个阶段：fetch_transcripts → analyze_highlights → download_audio →
cut_speed → subtitles → storyboard → generate_images → review_images →
prepare_handoff → publish_handoff。

确定性阶段自动执行；字幕核对术语、人名、数字、引文和语义，
有疑点才交执行者处理。图片自动技术校验，不等待用户审批。
"""

from __future__ import annotations

import argparse
import csv as _csv
import json
import os
import re
import shutil
import subprocess
import tempfile
import math
import timing
import sys
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import config  # noqa: E402
import state as state_mod  # noqa: E402
import shared_album
import job_versions
import subtitle_review
import production_checks
import storyboard_prepare
import subtitle_fast
from artifacts import atomic_json, atomic_copy, digest, file_hash, file_lock, hash_session, read_json, verified_file


PY = config.PY


def _sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], check=False, capture_output=True, text=True, **kw)


def fail(stage: str, msg: str) -> int:
    print(f"[{stage}] FAILED: {msg}", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- #
# 阶段实现
# --------------------------------------------------------------------------- #

def stage_fetch(job: Path, cfg: dict) -> str:
    # Fetch only the selected track before any creative/paid work; report its real length.
    if cfg.get('master_catalog'):
        import master_catalog, entry_preflight, content_selection
        from download_audio import download_one
        row = master_catalog.input_row(job, cfg)
        plan=read_json(job/'cut_plan.json',{})
        if plan.get('source_audio') and str(plan.get('track_id'))==row['track_id']:
            source=production_checks.path_at(job,plan['source_audio'])
            result={'status':'provided','track_id':row['track_id'],'path':str(source)}
        else:result = download_one(row, job/'audio', False, shared_album.album_root(cfg)/'audio')
        if result['status'] == 'failed': return '入口音频下载失败：'+result['error']
        atomic_json(job/'download_result.json', result)
        length = entry_preflight.probe(Path(result['path']))
        atomic_json(job/'source_preflight.json', {'track_id':row['track_id'], 'source_audio':result['path'],
                    'source_sha256':file_hash(Path(result['path'])), 'actual_seconds':length,
                    **content_selection.speed_options(length, content_selection.speed(plan,cfg)), 'speed_scope':'whole_source',
                    'duration_ok':length>=content_selection.minimum(), 'content_review':'agent_required',
                    'delivery':__import__('delivery').requested(cfg)})
        if length < content_selection.minimum():
            return f'入口预检：声音{row["track_id"]}真实时长{length:.3f}秒，不足300秒；完整短集也不例外。保留本次来源，不自动换声音。'
    return shared_album.fetch(job, cfg)


def _csv_has_transcript(job: Path) -> bool:
    for p in sorted(job.glob("*.csv")):
        try:
            with p.open(encoding="utf-8-sig", newline="") as f:
                fields = _csv.DictReader(f).fieldnames or []
                if "transcript" in fields:
                    return True
        except Exception:
            continue
    return False


def stage_download_audio(job: Path, cfg: dict) -> str:
    from download_audio import download_one
    plan = read_json(job / 'cut_plan.json', {})
    track_id = str(plan.get('track_id', ''))
    if plan.get('source_audio'):
        path = Path(plan['source_audio']).expanduser()
        if not path.is_absolute():
            path = job / path
        if not path.is_file():
            return 'cut_plan.source_audio指定的文件不存在'
        atomic_json(job / 'download_result.json', {'track_id': track_id, 'path': str(path.resolve()), 'sha256': file_hash(path)})
        return ''
    if not cfg.get('csv') and not cfg.get('master_catalog'):
        import xql_catalog
        xql_catalog.ensure_job_catalog(job, cfg)
    rows = shared_album.input_rows(job, cfg)
    selected = next((r for r in rows if r['track_id'].strip() == track_id), None)
    if selected and selected.get('track_play_path'):
        result = download_one(selected, job / 'audio', False, shared_album.album_root(cfg) / 'audio')
        if result['status'] == 'failed':
            return '音频下载失败: ' + result['error']
        atomic_json(job / 'download_result.json', result)
        return ''
    return '当前清单没有对应 track_id 的音频路径；请检查 XQL 快照/兼容 CSV，或在 cut_plan.source_audio 指定确切文件'


def stage_cut_speed(job: Path, cfg: dict) -> str:
    """Preserve the selected source and build the single final narration clock."""
    plan_path = job / "cut_plan.json"
    if not plan_path.exists():
        return "缺少 cut_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    # 优先明确指定的源音频，否则只使用对应track_id的下载结果。
    src_audio = None
    if plan.get("source_audio"):
        src = Path(plan["source_audio"]).expanduser()
        if not src.is_absolute():
            src = job / src
        if src.is_file():
            src_audio = src
    if src_audio is None:
        selected = read_json(job / 'download_result.json', {})
        if str(selected.get('track_id', '')) == str(plan.get('track_id', '')) and selected.get('path'):
            candidate = Path(selected['path'])
            if candidate.is_file():
                src_audio = candidate
    if src_audio is None:
        return "找不到源音频"

    import content_selection
    content_selection.validate(plan,cfg)
    speed = content_selection.speed(plan,cfg)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "缺少 ffmpeg"

    # 用 ffprobe 探测源音频真实时长
    from entry_preflight import probe
    src_duration = probe(src_audio)
    if not math.isfinite(src_duration) or src_duration <= 0:
        return "源音频时长无效"
    content_selection.validate_source(plan, src_duration)
    full = content_selection.selection_mode(plan)=='full_track'
    if full and speed == 1:
        content_selection.duration_ok(src_duration)
        clip={'start':0,'end':src_duration,'speed':1.0,'raw_duration':src_duration,'out_duration':src_duration,
              'expected_out_duration':src_duration,'output_start':0,'output_end':src_duration}
        atomic_json(job/'cut_result.json',{'mode':'full_track','processing':'original_bytes','speed':1,
                    'source_sha256':file_hash(src_audio),'segments':[{'path':str(src_audio.resolve()),'clips':[clip],
                    'total_out_duration':src_duration}]})
        return ''

    raw_clips = []
    clips = [{'start':0,'end':src_duration}] if full else plan['clips']
    for clip in clips:
        start, end = content_selection.seconds(clip['start']), content_selection.seconds(clip['end'])
        if not 0 <= start < end <= src_duration + .001:
            return f'切点超出源音频: {start}..{end} / {src_duration}'
        raw_clips.append({'start':start, 'end':end, 'speed':speed})
    expected_total = sum(c['end']-c['start'] for c in raw_clips) / speed
    content_selection.duration_ok(expected_total)
    mode = 'full_track' if full else 'continuous' if len(raw_clips) == 1 else 'splice'

    out_dir = job / "audio" / "cut"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "highlight-01.m4a"
    if speed != 1:
        from artifacts import digest
        version = digest([file_hash(src_audio), raw_clips, 'atempo-v1'])
        out = out_dir / f'body-{version[:24]}.m4a'
    part_meta = []
    cursor = 0.0
    # Build in isolation so failed conversion leaves the previous final audio intact.
    with tempfile.TemporaryDirectory(prefix='encode-', dir=out_dir) as tmp:
        work = Path(tmp); part_files = []
        for i, c in enumerate(raw_clips, 1):
            dur = c['end'] - c['start']
            single = len(raw_clips) == 1
            part = work / (f'part-{i:02d}.m4a' if single else f'part-{i:02d}.flac')
            filters = [f'atrim=duration={dur:.9f}', 'asetpts=PTS-STARTPTS']
            if speed != 1:
                # Flush atempo's last analysis window, then trim to the source-derived sample budget.
                # This preserves a final consonant and prevents a 420 s / 1.4 job falling below 300 s.
                samples = round(dur / speed * 48_000)
                filters.extend(['aresample=48000','apad=pad_dur=0.15',f'atempo={speed:g}',f'atrim=end_sample={samples}'])
            codec = ['-c:a','aac','-b:a','192k'] if single else ['-c:a','flac']
            command = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
                       '-ss', f"{c['start']:.9f}", '-t', f'{dur:.9f}', '-i', str(src_audio), '-map', '0:a:0', '-vn',
                       '-af', ','.join(filters), *codec, str(part)]
            with timing.span('audio_encode', clip=i, speed=speed) as measured:
                r = _sh(command)
                if r.returncode or not part.is_file() or not part.stat().st_size:
                    measured['status'] = 'error'
                    return f'剪切变速失败 (段{i}): {r.stderr}'
                actual = probe(part)
                if not math.isfinite(actual) or actual <= 0 or abs(actual-dur/speed) > .12:
                    measured['status'] = 'error'
                    return f'剪切变速时长异常 (段{i}): 预期{dur/speed:.3f}s，实际{actual:.3f}s'
            part_files.append(part)
            part_meta.append({'start': c['start'], 'end': c['end'], 'speed': speed,
                              'raw_duration': dur, 'out_duration': round(actual, 6),
                              'expected_out_duration': round(dur/speed, 6),
                              'output_start':round(cursor,6), 'output_end':round(cursor+actual,6)})
            cursor += actual
        if len(part_files) == 1:
            final = part_files[0]
        else:
            concat_list = work / 'concat.txt'
            # Relative names also handle parent folders containing quotes/spaces.
            concat_list.write_text(''.join(f"file '{p.name}'\n" for p in part_files), encoding='utf-8')
            final = work / 'joined.m4a'
            with timing.span('audio_concat') as measured:
                r = _sh([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
                         '-f', 'concat', '-safe', '0', '-i', str(concat_list), '-c:a', 'aac', '-b:a','192k', str(final)])
                if r.returncode or not final.is_file() or not final.stat().st_size:
                    measured['status'] = 'error'
                    return f'拼接失败: {r.stderr}'
        actual_total = probe(final)
        tolerance = .12 * len(part_files)
        if (not math.isfinite(actual_total) or actual_total <= 0
                or abs(actual_total-expected_total) > tolerance):
            return f'最终音频时长异常: 预期{expected_total:.3f}s，实际{actual_total:.3f}s'
        content_selection.duration_ok(actual_total)
        atomic_copy(final, out)
    atomic_json(job / 'cut_result.json', {'mode': mode, 'speed':speed,
                'processing':'pitch_preserving_atempo_v1' if speed != 1 else 'single_pass_v1',
                'source_sha256':file_hash(src_audio),'body_sha256':file_hash(out),
                'segments': [{'path': str(out.resolve()), 'clips': part_meta,
                              'total_out_duration': round(actual_total, 6)}]})
    return ""


def stage_transcribe(job: Path, cfg: dict) -> str:
    import content_selection
    content_selection.require_current_speed(job,cfg)
    content_selection.validate_job(job)
    content_selection.duration_ok(subtitle_review.duration_us(subtitle_review.audio_path(job))/1e6)
    import caption_routing
    return caption_routing.select_transcript(job, cfg)


def stage_subtitles(job: Path, cfg: dict) -> str:
    if subtitle_review.ready(job, cfg, repair_srt=True):
        return ''
    err = stage_transcribe(job, cfg)
    if err:
        return err
    request = subtitle_review.prepare(job, cfg)
    return storyboard_prepare.alongside_review(job, cfg, request,
        lambda: _finish_subtitle_review(job, cfg, request))


def _finish_subtitle_review(job: Path, cfg: dict, request: dict) -> str:
    response_path = job / 'subtitle_review_response.json'
    response = read_json(response_path, {})
    cached = shared_album.album_root(cfg) / 'subtitle-reviews' / request['request_hash']
    cache_record = read_json(cached / 'record.json', {})
    if response.get('request_hash') != request['request_hash'] and verified_file(cached / 'response.json', cache_record.get('sha256')):
        atomic_copy(cached / 'response.json', response_path)
        response = read_json(response_path)
    if response.get('request_hash') != request['request_hash']:
        command = cfg.get('subtitle_review_command')
        if command:
            if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
                return 'subtitle_review_command必须是命令参数数组，不执行shell字符串'
            with timing.span('review', source='configured_command') as measured:
                result = _sh(command + ['--request', str(job / 'subtitle_review_request.json'), '--output', str(response_path)],
                             timeout=cfg.get('subtitle_review_timeout', 300))
                if result.returncode: measured['status'] = 'error'
            if result.returncode:
                return '语义审核命令失败，未放行字幕: ' + result.stderr[-1000:]
        else:
            return 'AWAITING_REVIEW:请由技能执行者读取subtitle_review_request.json完成语义审核；同时可推进storyboard_prepare_request.json中的分镜预案，再resume'
    with timing.span('review_validation', job=job):
        result = subtitle_review.apply_review(job, cfg)
    if result['status'] != 'passed':
        return 'AWAITING_REVIEW:先处理subtitle_repair_queue.json中的分句/对齐/内容问题；仅subtitle_listen_queue.json中的疑点需要复听；原声未改动'
    return ''


def stage_audit(job: Path, cfg: dict) -> str:
    """审核「用户可见文字」（种草文案 + 字幕）是否命中拒审词。

    注意：只扫这两类可见文本，不扫生图提示词（提示词不可见）。
    生图提示词受 growth-review-rules.md 的画面排除项约束（见 quality-rules.md）。
    """
    if not subtitle_review.ready(job, cfg):
        return '字幕尚未通过当前版本的处理及真实时码检查'
    if not (job / 'copy.json').is_file():
        return '缺少copy.json，不能把空输入判为审核通过'
    audit_script = SCRIPT_DIR / "audit_text.py"
    terms = config.SKILL_DIR / "references" / "rejected-terms.csv"
    if not audit_script.exists() or not terms.exists():
        return "缺少审核脚本或拒审词表"

    results = []
    # 1) 字幕
    subtitles = job / "subtitles.csv"
    if subtitles.exists():
        r = _sh([PY, str(audit_script), "--input-csv", str(subtitles),
                 "--fields", "text", "--terms", str(terms),
                 "--output", str(job / "audit_subtitles.csv"), "--generated", "--force"],
                cwd=str(job))
        results.append(("字幕", r.returncode, r.stdout + r.stderr))

    # 2) Field-scoped album-name review never exempts visible title/subtitles.
    from audit_text import load_rejected_terms, scan_text
    c = read_json(job/'copy.json',{})
    approved_album = production_checks.album_exception(job,cfg)
    records=[]
    for key in ('title_line1','title_line2','album_name'):
        if key=='album_name' and cfg.get('creative_options',{}).get('branding_enabled') is False: continue
        hits=scan_text(c.get(key,''),load_rejected_terms(terms))
        exception=bool(hits and key=='album_name' and approved_album)
        records.append({'field':key,'text':c.get(key,''),'hits':hits,'album_name_exception':exception})
    atomic_json(job/'audit_copy.json',{'fields':records,'album_review':read_json(job/'album_name_review.json',{})})
    if any(r['hits'] and not r['album_name_exception'] for r in records):
        results.append(('文案',1,'字段命中，详见audit_copy.json'))

    # 汇总：有任何命中（returncode != 0）即返回错误
    strict = cfg.get('content_wordlist_mode','contextual') == 'strict'
    hits = [name for name, rc, _ in results if rc != 0 and (strict or rc != 1)]
    if hits:
        return f"审核发现拒审词命中: {', '.join(hits)}。请查看 audit_*.csv 改写后重新审核。"
    return ""


def stage_generate_images(job: Path, cfg: dict) -> str:
    import title_review
    title_review.require(job, cfg)
    import presentation
    route = presentation.image_pipeline_guard(job, cfg)
    if route: return route
    import creative_options
    try: creative_options.check_bound(job, cfg)
    except ValueError as exc: return 'AWAITING_REVIEW:' + str(exc)
    if (job / 'storyboard_prepare_request.json').exists() and not production_checks.storyboard_ready(job):
        return '并行预案尚未对照已通过字幕核对定稿；先bind-storyboard再生图'
    import generate_images
    manifest = job / 'image_manifest.json'
    audit_err = stage_audit(job, cfg)
    if audit_err:
        return audit_err
    raw = generate_images.read_manifest(manifest)
    data = generate_images.validate_manifest(raw, manifest.resolve())
    job_versions.validate_story_images(job)
    import image_pacing
    image_pacing.validate_job(job)
    declared = {image['id']: image for image in data['images']}
    for orientation in config.load_spec()['orientations']:
        job_versions.cover_id(declared, orientation)
    report = generate_images.run_batch(data, os.environ.get(config.GPT_IMAGE_TOKEN_ENV, ''),
        api_base=raw.get('api_base', generate_images.DEFAULT_API_BASE),
        cache_dir=shared_album.album_root(cfg) / 'images', only=cfg.get('images_only'),
        retry_generation=bool(cfg.get('retry_generation')), adopt_existing=bool(cfg.get('adopt_existing')))
    failed = [r for r in report['results'] if r['status'] == 'failed']
    return '生图/下载待处理: ' + str(failed) if failed else ''


def stage_prepare_handoff(job: Path, cfg: dict) -> str:
    import presentation
    route = presentation.image_pipeline_guard(job, cfg)
    if route: return route
    import handoff
    handoff.prepare(job, cfg)
    return ''


def stage_analyze_highlights(job: Path, cfg: dict) -> str:
    if not (job/'cut_plan.json').is_file():
        return 'AWAITING_REVIEW:Agent按完整价值选段并写cut_plan.json（highlight_id、summary、track_id、clips、content）；无需逐条确认'
    import content_selection
    err = stage_download_audio(job, cfg)
    if err: return err
    import entry_preflight
    report = entry_preflight.job_report(job, cfg)
    if not report['ok']:
        return 'AWAITING_REVIEW:入口预检未通过，查看entry_preflight.json；先修完整性/前言判断和真实范围，不自动换声音'
    content_selection.validate_job(job)
    production_checks.register_selected_highlight(job, cfg)
    return ''


def stage_storyboard(job: Path, cfg: dict) -> str:
    import presentation
    route = presentation.image_pipeline_guard(job, cfg)
    if route: return route
    import creative_options
    try: creative_options.selected(cfg)
    except ValueError as exc: return 'AWAITING_REVIEW:' + str(exc)
    from title_review import PROFILE
    team = cfg.get('production_profile') == PROFILE
    required = ('storyboard.json', 'copy.json', 'visual_bible.json')
    if not team:
        required += ('image_manifest.json',)
    missing = [name for name in required if not (job/name).is_file()]
    if missing:
        return 'AWAITING_REVIEW:Agent一次写齐分镜产物：' + ', '.join(missing)
    import album_visuals
    from content_selection import validate_job
    validate_job(job)
    text = read_json(job/'copy.json')
    import cover_policy
    cover_policy.title(text, historical=cover_policy.legacy(job,cfg))
    try: album_visuals.validate_setup(job,cfg)
    except ValueError as exc: return 'AWAITING_REVIEW:'+str(exc)
    if team:
        import team_visuals
        try:
            team_visuals.compile_job(job, cfg)
        except ValueError as exc:
            return 'AWAITING_REVIEW:'+str(exc)
    else:
        creative_options.bind_prompts(job, cfg)
    job_versions.validate_story_images(job)
    if not subtitle_review.ready(job, cfg):
        return 'AWAITING_REVIEW:先处理必要字幕项'
    text = read_json(job/'copy.json')
    import layout_profile
    for key in ('title_line1','title_line2'):
        try:layout_profile.fit(text.get(key),key)
        except ValueError as exc:return 'AWAITING_REVIEW:'+str(exc)
    old_basis = read_json(job/'storyboard_timing_review.json', {}).get('basis', {})
    current_audio = file_hash(subtitle_review.audio_path(job))
    if (old_basis.get('audio_sha256') and old_basis['audio_sha256'] != current_audio
            and old_basis.get('storyboard_hash') == file_hash(job/'storyboard.json')):
        return 'AWAITING_REVIEW:最终音频改变，Agent需核对分镜；更新分镜或写入source_audio_sha256标明已对照的新音频哈希'
    if not production_checks.storyboard_ready(job):
        production_checks.bind_storyboard(job, 'script',
            '自动检查分镜连续覆盖最终音频并绑定当前产物；不代表语义或视觉审核')
    return ''


def stage_review_images(job: Path, cfg: dict) -> str:
    job_versions.validate_images(job)
    import album_visuals
    try:
        album_visuals.review_request(job,cfg)
        album_visuals.validate_inspections(job,cfg)
        album_visuals.remember_cover_design(job,cfg)
    except ValueError as exc: return 'AWAITING_REVIEW:'+str(exc)
    return ''


def stage_publish_handoff(job: Path, cfg: dict, mode=None) -> str:
    import assembly_runtime
    if assembly_runtime.profile(mode)["mode"] == "local":
        # Prepared local files go directly to loopback assembly; never invoke an upload adapter.
        return ""
    import handoff
    mapping_valid = False
    if (job/'uploaded_assets.json').is_file():
        try:
            request = read_json(job/'upload_request.json')
            handoff.mapping(job, cfg, request)
            mapping_valid = True
        except (ValueError, KeyError, TypeError):
            pass
    command = cfg.get('upload_command')
    if not mapping_valid and command:
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            return 'upload_command必须是非空命令参数数组'
        with timing.span('upload', source='configured_command'):
            result = _sh(command + ['--request', str(job/'upload_request.json'),
                                   '--output', str(job/'uploaded_assets.json')],
                         timeout=cfg.get('upload_timeout', 600))
        if result.returncode:
            return '上传适配器失败，保留素材，查看执行日志：' + result.stderr[-500:]
        if not (job/'uploaded_assets.json').is_file():
            return '上传适配器未生成uploaded_assets.json'
    elif not mapping_valid:
        return 'AWAITING_REVIEW:等待平台按 upload_request.json 上传文件并回填 uploaded_assets.json；然后 resume'
    handoff.publish(job, cfg)
    return ''


# --------------------------------------------------------------------------- #
# 阶段注册
# --------------------------------------------------------------------------- #

RUNNERS: dict[str, Callable[[Path, dict], str]] = {
    "fetch_transcripts": stage_fetch,
    "analyze_highlights": stage_analyze_highlights,
    "download_audio": stage_download_audio,
    "cut_speed": stage_cut_speed,
    "subtitles": stage_subtitles,  # 最终音频云ASR＋知识文化full核对
    "storyboard": stage_storyboard,
    "generate_images": stage_generate_images,
    "review_images": stage_review_images,
    "prepare_handoff": stage_prepare_handoff,
    "publish_handoff": stage_publish_handoff,
}

HUMAN_HINT = {
    "analyze_highlights": "Agent选定剧情后写cut_plan，脚本自动登记固定高光ID，无需用户逐条确认。",
    "download_audio": "按专辑 XQL 清单下载选中 track_id；本地音频通过 cut_plan.source_audio 指定。",
    "subtitles": "核对subtitle_review_request.json中的术语、数字、引文和语义，写response后续跑，脚本会自动套用。",
    "storyboard": "Agent写分镜/文案/图片清单；脚本自动校验时码并绑定。",
    "review_images": "脚本自动校验图片文件、方向和来源版本，无人工审核。",
}


def human_ready(stage: str, job: Path) -> bool:
    if stage == 'subtitles':
        return subtitle_review.ready(job, load_job_cfg(job))
    if stage == 'storyboard' and not production_checks.storyboard_ready(job):
        return False
    if stage == 'analyze_highlights':
        try: production_checks.identity(job)
        except (ValueError,OSError,KeyError): return False
    if stage == 'review_images':
        return job_versions.images_ready(job)
    checks = {
        "analyze_highlights": ["cut_plan.json"],
        "download_audio": ["audio"],
        "storyboard": ["storyboard.json", "copy.json", "image_manifest.json"],
    }
    for marker in checks.get(stage, []):
        path = job / marker
        if marker == "audio":
            if not path.is_dir() or not any(path.iterdir()):
                return False
        elif not path.exists():
            return False
    return True


# --------------------------------------------------------------------------- #
# 辅助函数
# --------------------------------------------------------------------------- #

_ORIENTATION_CN = {"landscape": "横屏", "portrait": "竖屏"}


def _highlight_index(job: Path) -> str:
    """从 job 目录名解析高光序号（如 126589340-02 → 02），无后缀默认 01。"""
    saved = read_json(job/'highlight_identity.json',{})
    if saved:
        return production_checks.identity(job)['highlight_id']
    m = re.search(r"-(\d+)$", job.name)
    if m:
        return f"{int(m.group(1)):02d}"
    return "01"


def _probe_audio_duration_us(audio: str) -> int:
    return round(_probe_audio_duration_seconds(audio) * 1_000_000)


def _probe_audio_duration_seconds(audio: str) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("缺少 ffprobe")
    r = _sh([ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio])
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise RuntimeError(f"无法探测音频时长: {audio}")


def _to_us(value) -> int:
    if isinstance(value, (int, float)):
        return round(float(value) * 1_000_000)
    parts = str(value).strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        s = s.replace(",", ".")
        return round((int(h) * 3600 + int(m) * 60 + float(s)) * 1_000_000)
    return round(float(str(value).replace(",", ".")) * 1_000_000)


def _to_seconds(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) == 3:
        h, m, s = parts
        s = s.replace(",", ".")
        return int(h) * 3600 + int(m) * 60 + float(s)
    return float(text.replace(",", "."))


def _read_subtitles(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in _csv.DictReader(handle):
            rows.append({"text": row["text"], "start_us": _to_us(row["start"]), "end_us": _to_us(row["end"])})
    return rows


def load_job_cfg(job: Path) -> dict:
    cfg_path = job / "config.json"
    if not cfg_path.exists():
        raise SystemExit(f"缺少 job 配置: {cfg_path}")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def create_job(album_id: str, n_transcripts: int | None, csv_path: str | None = None,
               job_id: str | None = None, shared_root: str | None = None) -> Path:
    name = job_id or album_id
    if not album_id.isdigit() or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('album_id必须是数字；job-id只允许字母、数字、下划线和连字符')
    job = (config.JOBS_DIR / name).expanduser().resolve()
    job.mkdir(parents=True, exist_ok=True)
    cfg = read_json(job / 'config.json', {})
    if cfg.get('album_id') and str(cfg['album_id']) != album_id:
        raise ValueError('该job-id已属于其他专辑')
    if not cfg:
        from title_review import PROFILE
        cfg['production_profile'] = PROFILE
        cfg['cover_policy_version'] = 2
    cfg['album_id'] = album_id
    cfg['n_transcripts'] = n_transcripts if n_transcripts is not None else cfg.get('n_transcripts', 100)
    if csv_path:
        source = Path(csv_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError('CSV文件不存在')
        cfg['csv'] = str(source)
        atomic_copy(source, job / 'input_snapshot.csv')
    if shared_root:
        cfg['shared_root'] = str(Path(shared_root).expanduser().resolve())
    atomic_json(job / 'config.json', cfg)
    return job


def _run_pipeline(job: Path, *, force_stage: str | None = None) -> int:
    job = job.expanduser().resolve()
    state = state_mod.load_state(job)
    cfg = load_job_cfg(job)
    import title_review
    wait = title_review.pending(job, cfg)
    if wait:
        print(wait['action'])
        return 0
    import creative_options
    cfg = creative_options.resolve_album(cfg)
    atomic_json(job/'config.json', cfg)
    if cfg.get('subtitle_review_mode', 'full') != 'full':
        raise ValueError('知识文化内容使用full字幕核对，不支持仅拒审词模式')
    job_versions.pin_existing_quality(job, cfg)
    if (job/'highlight_identity.json').exists(): production_checks.identity(job)
    changes = job_versions.invalidate_changed(state, job, cfg)
    if changes:
        print('输入版本变化，仅重跑相关阶段: ' + ', '.join(changes))
    state_mod.save_state(job, state)
    if force_stage:
        for s in state_mod.stages_downstream(force_stage):
            state["stages"][s] = "pending"
        state_mod.save_state(job, state)
        print(f"已重置 {force_stage} 及其下游阶段")

    start = state_mod.first_pending(state)
    if start is None:
        print("全部阶段已完成。")
        return 0

    for stage in state_mod.STAGES:
        if state_mod.STAGES.index(stage) < state_mod.STAGES.index(start):
            continue
        status = state["stages"].get(stage, "pending")
        if status == "done":
            continue
        runner = RUNNERS[stage]
        if runner is None:
            if human_ready(stage, job):
                timing.mark(job, 'finish', 'confirmation_wait', stage)
                print(f"[{stage}] 产物已就绪，自动通过")
                state_mod.set_stage(state, stage, "done")
                job_versions.stamp(state, stage, job, cfg)
                state_mod.save_state(job, state)
                continue
            state_mod.set_stage(state, stage, "awaiting_human")
            state_mod.save_state(job, state)
            timing.mark(job, 'start', 'confirmation_wait', stage)
            print(f"[{stage}] 等待执行者产物：{HUMAN_HINT[stage]}")
            return 0
        timing.mark(job, 'finish', 'confirmation_wait', stage)
        print(f"[{stage}] 执行中…")
        state_mod.set_stage(state, stage, "running")
        state_mod.save_state(job, state)
        try:
            with timing.span('stage', stage=stage) as measured:
                if stage == 'cut_speed' and not production_checks.cut_ready(job,cfg):
                    err = 'AWAITING_REVIEW:已生成cut_review_request.json；Agent核对句首尾与局部音频后写response，无需额外prepare-cut/check-cut命令'
                else:
                    err = runner(job, cfg)
                if err: measured['status'] = 'awaiting_review' if err.startswith('AWAITING_REVIEW:') else 'error'
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
            err = str(exc)
        if err and err.startswith('AWAITING_REVIEW:'):
            state.setdefault('messages', {})[stage] = err.removeprefix('AWAITING_REVIEW:')
            state_mod.set_stage(state, stage, 'awaiting_human')
            state_mod.save_state(job, state)
            timing.mark(job, 'start', 'confirmation_wait', stage)
            print(err)
            return 0
        if err:
            state.setdefault('messages', {})[stage] = err
            state_mod.set_stage(state, stage, "failed")
            state_mod.save_state(job, state)
            return fail(stage, err)
        state_mod.set_stage(state, stage, "done")
        state.setdefault('messages', {}).pop(stage, None)
        job_versions.stamp(state, stage, job, cfg)
        job_versions.invalidate_changed(state, job, cfg)
        state_mod.save_state(job, state)
        print(f"[{stage}] 完成")

    print("前序交接已完成；草稿和 MP4 由线上剪映 Skill 另行处理。")
    return 0


def run_pipeline(job: Path, *, force_stage: str | None = None) -> int:
    job = job.expanduser().resolve()
    with file_lock(job / '.pipeline.lock', timeout=2), timing.bind(job), hash_session():
        try:
            with timing.span('pipeline_attempt') as measured:
                result = _run_pipeline(job, force_stage=force_stage)
                if result: measured['status'] = 'error'
                return result
        finally:
            timing.report(job)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--album-id", required=True)
    p_run.add_argument("--n", type=int)
    p_run.add_argument("--csv", help="可选旧 CSV 兼容入口；省略时按 album-id 自动查询 XQL 音频清单")

    p_run.add_argument('--job-id', help='高光独立任务ID，同专辑可创建多个job')
    p_run.add_argument('--shared-dir', help='跨job共享缓存根目录')

    p_run.add_argument("--init-only", action="store_true", help="仅创建任务；预检后 resume")

    p_resume = sub.add_parser("resume")
    p_resume.add_argument("--job-dir", required=True, type=Path)

    p_status = sub.add_parser("status")
    p_status.add_argument("--job-dir", required=True, type=Path)

    p_force = sub.add_parser("force-stage")
    p_force.add_argument("--job-dir", required=True, type=Path)
    p_force.add_argument("--stage", required=True, choices=state_mod.STAGES)

    p_transcribe = sub.add_parser("transcribe")
    p_transcribe.add_argument("--job-dir", required=True, type=Path)

    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--job-dir", required=True, type=Path)

    p_subtitles = sub.add_parser('subtitles')
    p_subtitles.add_argument('--job-dir', required=True, type=Path)
    p_subtitles.add_argument('--apply', action='store_true', help='应用必要拒审词替换和短句边界')
    p_images = sub.add_parser('images')
    p_images.add_argument('--job-dir', required=True, type=Path)
    p_images.add_argument('--only', nargs='+')
    p_images.add_argument('--adopt-existing', action='store_true')
    p_images.add_argument('--retry-generation', action='store_true')
    p_approve = sub.add_parser('approve-images')
    p_approve.add_argument('--job-dir', required=True, type=Path)
    p_approve.add_argument('--ids', nargs='+')
    p_dict = sub.add_parser('dictionary')
    p_dict.add_argument('--job-dir', required=True, type=Path)
    p_dict.add_argument('--input', required=True, type=Path)

    p_prepare = sub.add_parser('storyboard-prepare', help='独立准备/恢复分镜预案，不覆盖定稿或放行生图')
    p_prepare.add_argument('--job-dir', type=Path, required=True)
    p_prepare.add_argument('--retry', action='store_true', help='检查失败/中断后显式重试预案命令')

    p_batch = sub.add_parser('batch', help='一轮前序批次调度；等待审核或上传不阻塞其他job')
    p_batch.add_argument('--manifest', type=Path, required=True)
    p_batch.add_argument('--workers', type=int, default=2)
    p_batch.add_argument('--image-workers', type=int, default=3)
    p_batch.add_argument('--download-workers', type=int, default=3, help='本批共享图片下载并发，1–8')
    p_timing = sub.add_parser('timing', help='耗时报告及外部审核计时')
    p_timing.add_argument('--job-dir', type=Path, required=True)
    p_timing.add_argument('--action', choices=['report', 'start', 'finish'], default='report')
    p_timing.add_argument('--kind', choices=['review'])
    p_timing.add_argument('--key', help='如subtitles/review_images/landscape/portrait')
    p_timing.add_argument('--status', choices=['ok', 'error', 'cancelled'], default='ok')

    args = parser.parse_args()

    if args.cmd == 'batch':
        from batch_runner import run_batch
        result = run_batch(args.manifest, args.workers, args.image_workers, args.download_workers)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result['status'] == 'failed' else 0
    if args.cmd == 'timing':
        job = args.job_dir.expanduser().resolve()
        if not (job / 'config.json').is_file(): raise ValueError('不存在有效job')
        if args.action != 'report':
            if not args.kind or not args.key: raise ValueError('start/finish需要--kind和--key')
            output = None
            if args.action == 'start' and args.kind == 'review':
                timing.mark(job, 'finish', 'confirmation_wait', args.key)
            timing.mark(job, args.action, args.kind, args.key, args.status, output)
        print(json.dumps(timing.report(job), ensure_ascii=False))
        return 0


    if args.cmd == "run":
        job = create_job(args.album_id, args.n, args.csv, args.job_id, args.shared_dir)
        if args.init_only:
            print(job); return 0
        return run_pipeline(job)
    if args.cmd == "resume":
        return run_pipeline(args.job_dir)
    if args.cmd == "status":
        state = state_mod.load_state(args.job_dir)
        for s in state_mod.STAGES:
            print(f"{s:20} {state['stages'].get(s, 'pending')}")
        prepared = read_json(args.job_dir / 'storyboard_prepare_status.json', {})
        if prepared:
            print('storyboard_prepare   ' + prepared.get('status', 'unknown'))
        return 0
    if args.cmd == "force-stage":
        return run_pipeline(args.job_dir, force_stage=args.stage)
    if args.cmd in {'subtitles', 'images', 'approve-images', 'dictionary', 'storyboard-prepare'}:
        job = args.job_dir.expanduser().resolve()
        cfg = load_job_cfg(job)
        with file_lock(job / '.pipeline.lock', timeout=2), timing.bind(job):
            if args.cmd == 'storyboard-prepare':
                request = subtitle_review.prepare(job, cfg)
                prepared = storyboard_prepare.prepare(job, cfg, request)
                result = storyboard_prepare.run(job, cfg, prepared, retry=args.retry)
                print(json.dumps(result, ensure_ascii=False))
                return 1 if result['status'] == 'failed' else 0
            if args.cmd == 'dictionary':
                print(shared_album.import_dictionary(cfg, args.input.expanduser().resolve()))
                return 0
            if args.cmd == 'approve-images':
                with timing.span('review_validation', source='image_approval'):
                    job_versions.approve_images(job, args.ids)
                timing.mark(job, 'finish' if job_versions.images_ready(job) else 'start',
                            'confirmation_wait', 'review_images')
                print('所选图片审核记录已绑定当前版本')
                return 0
            if args.cmd == 'images':
                cfg.update(images_only=args.only, adopt_existing=args.adopt_existing, retry_generation=args.retry_generation)
                err = stage_generate_images(job, cfg)
            elif args.apply:
                with timing.span('review_validation'):
                    result = subtitle_review.apply_review(job, cfg)
                timing.mark(job, 'finish' if result['status'] == 'passed' else 'start',
                            'confirmation_wait', 'subtitles')
                print(result['status'])
                return 0 if result['status'] == 'passed' else 3
            else:
                err = stage_transcribe(job, cfg)
                if not err:
                    request = subtitle_review.prepare(job, cfg)
                    err = storyboard_prepare.alongside_review(job, cfg, request,
                        (lambda: _finish_subtitle_review(job,cfg,request)) if subtitle_fast.active(cfg) else (lambda: ''))
                    if err.startswith('AWAITING_REVIEW:'):
                        print(err); err = ''
                    timing.mark(job, 'finish' if subtitle_review.ready(job,cfg) else 'start', 'confirmation_wait', 'subtitles')
                    print('已准备字幕与分镜预案；百炼默认仅处理拒审词及必要分段，字幕通过后再定稿分镜')
            return fail(args.cmd, err) if err else 0
    if args.cmd == "transcribe":
        job = args.job_dir.expanduser().resolve()
        cfg = load_job_cfg(job)
        with file_lock(job / '.pipeline.lock', timeout=2), timing.bind(job):
            err = stage_transcribe(job, cfg)
        if err:
            return fail("transcribe", err)
        print("转写完成 → final_transcript.json")
        return 0
    if args.cmd == "audit":
        job = args.job_dir.expanduser().resolve()
        cfg = load_job_cfg(job)
        err = stage_audit(job, cfg)
        if err:
            return fail("audit", err)
        print("当前字幕模式及可见文字检查通过（关键词模式不代表语义复核，原声未处理）")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
