"""One report for all selected sources: duration, completeness, mode and delivery."""
import argparse
import json
import math
from pathlib import Path
import subprocess
from artifacts import atomic_json, file_hash, read_json


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams','a:0','-show_entries', 'stream=codec_type,duration:format=duration',
                             '-of', 'json', str(path)], capture_output=True, text=True, timeout=30, check=True)
    media=json.loads(result.stdout);streams=media.get('streams',[])
    if not streams or streams[0].get('codec_type')!='audio':raise ValueError('没有可用的原声音轨')
    measured = streams[0].get('duration')
    if measured in (None, 'N/A', ''): measured = media.get('format', {}).get('duration')
    if measured in (None, 'N/A', ''): raise ValueError('媒体未提供可核验的原声音轨时长')
    duration = float(measured)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('真实音频时长无效')
    return duration


def inspect(plan, base, *, candidate=False, cfg=None):
    import content_selection
    errors = []
    row = {'track_id':str(plan.get('track_id', '')), 'selection_mode':plan.get('selection_mode', 'excerpt')}
    try: content_selection.selection_mode(plan)
    except ValueError as exc: errors.append(str(exc))
    if not row['track_id'].isdigit():errors.append('需要真实track_id')
    source = plan.get('source_audio')
    if not source:
        errors.append('入口缺少source_audio：先下载/复用本次选中声音；不以总表估时替代真实时长')
    else:
        path = Path(source).expanduser()
        if not path.is_absolute(): path = Path(base)/path
        try:
            row.update(source_audio=str(path.resolve()), source_sha256=file_hash(path), actual_seconds=probe(path))
            if row['actual_seconds'] < content_selection.minimum():
                errors.append(f'真实源音频{row["actual_seconds"]:.3f}秒，不足300秒；完整短集也不例外')
            full = row['selection_mode']=='full_track'
            if full and 'clips' not in plan:
                plan={**plan,'clips':[{'start':0,'end':row['actual_seconds']}]}
            chosen = content_selection.speed(plan, cfg)
            clips = plan.get('clips')
            source_seconds = row['actual_seconds']
            if clips and not full:
                source_seconds = sum(content_selection.seconds(c['end'])-content_selection.seconds(c['start']) for c in clips)
            row.update(content_selection.speed_options(source_seconds, chosen))
            row['speed_scope'] = 'selected_range' if clips else 'whole_source'
            content_selection.verify_speed_duration(row)
            if full:
                content_selection.validate_source(plan,row['actual_seconds'])
                clips = [{**clips[0], 'start':0, 'end':row['actual_seconds']}]
                plan = {**plan,'clips':clips}
            if not candidate:
                if not full:
                    content_selection.validate_source(plan,row['actual_seconds'])
                content_selection.validate(plan, cfg)
                row['selected_seconds'] = source_seconds  # source-clock field retained for existing callers
                row['clips']=clips
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
    content = plan.get('content', {})
    if not isinstance(content, dict): content = {}
    row['content_review'] = {k:content.get(k) for k in ('is_complete', 'is_intro_only', 'complete_reason')}
    if content.get('is_intro_only') is True:errors.append('只有前言，没有独立正文')
    if content.get('is_complete') is False:errors.append('内容不完整')
    if not all(type(content.get(k)) is bool for k in ('is_complete','is_intro_only')) or not str(content.get('complete_reason','')).strip():
        errors.append('需要执行者通读文稿/复听后记录完整性、是否仅前言和判断依据')
    row.update(ok=not errors, errors=list(dict.fromkeys(errors)))
    return row


def selection(data, base):
    from delivery import requested
    from content_selection import minimum
    candidate='candidates' in data
    items = data['candidates'] if candidate else data.get('segments', data.get('highlights', []))
    if not items: raise ValueError('预检需要非空segments')
    rows = [inspect(item, base, candidate=candidate, cfg=data.get('config', {})) for item in items]
    return {'schema':'knowledge-entry/v1', 'ok':all(row['ok'] for row in rows),
            'minimum_seconds':minimum(), 'short_full_track_exception':False,
            'delivery':requested(data.get('config', {})), 'tracks':rows,
            'note':'结构与实际时长预检；内容完整性由执行者实读/复听，不代表画面或事实审核。'}


def job_report(job, cfg):
    import production_checks
    plan = read_json(Path(job)/'cut_plan.json')
    plan = {**plan, 'source_audio':str(production_checks.source_audio(Path(job)))}
    result = selection({'segments':[plan], 'config':cfg}, job)
    atomic_json(Path(job)/'entry_preflight.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = selection(read_json(args.input), args.input.resolve().parent)
    atomic_json(args.output or args.input.with_suffix('.preflight.json'), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__': raise SystemExit(main())
