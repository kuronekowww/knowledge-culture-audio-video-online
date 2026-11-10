"""Show the actual cover and on-screen titles, then record a specific user decision."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path

from artifacts import atomic_json, digest, file_lock, read_json

PROFILE = 'team-v1'
FIELDS = ('episode_title', 'cover_title_lines', 'title_line1', 'title_line2')


def title_fields(value, historical=False):
    if not isinstance(value, dict):
        raise ValueError('需要提供封面和片内两行标题')
    result = {key: value.get(key) for key in FIELDS}
    for key in ('episode_title', 'title_line1', 'title_line2'):
        line = result[key]
        if not isinstance(line, str) or not line.strip() or len(line.splitlines()) != 1 or line != line.strip():
            raise ValueError(f'{key} 需要一行完整文字')
    import cover_policy
    cover_policy.title(result, historical=historical)
    return result


def proposal(job, cfg):
    plan = read_json(job/'cut_plan.json', {})
    import cover_policy
    fields = title_fields(read_json(job/'copy.json', {}), historical=cover_policy.legacy(job,cfg))
    content = plan.get('content', {})
    source = {k: plan.get(k) for k in ('track_id','highlight_id','clips','summary','title_source_sha256')}
    source.update(album_id=cfg['album_id'], evidence=content.get('evidence', []))
    return {'track_id': source['track_id'], 'highlight_id': source['highlight_id'],
            **fields, 'evidence': source['evidence'][:1],
            'title_version': digest([source, fields])}


def pending(job, cfg):
    if cfg.get('production_profile') != PROFILE or not (job/'cut_plan.json').exists():
        return None
    row = {'job': str(job), 'stage': 'title'}
    try:
        title = proposal(job, cfg)
    except ValueError as exc:
        return {**row, 'status': 'awaiting_agent_title', 'action': str(exc), 'files': [str(job/'copy.json')]}
    receipt = read_json(job/'title_approval.json', {})
    if (receipt.get('title_version') == title['title_version'] and receipt.get('confirmed_by') == 'user'
            and str(receipt.get('evidence', '')).strip()):
        return None
    try:
        import layout_profile
        for role in ('title_line1','title_line2'):
            layout_profile.fit(title[role], role)
    except ValueError as exc:
        return {**row, 'status':'awaiting_agent_title', 'action':str(exc), 'files':[str(job/'copy.json')]}
    import content_selection
    plan = read_json(job/'cut_plan.json')
    clips = plan.get('clips', [])
    speed_info = {}
    if clips:
        source_seconds = sum(content_selection.seconds(c['end'])-content_selection.seconds(c['start']) for c in clips)
        speed_info = content_selection.speed_options(source_seconds, content_selection.speed(plan,cfg))
    return {**row, **title, **speed_info, 'status':'awaiting_user_title',
            'action':'合并展示本批封面与片内两行标题；收到用户明确答复后，仅登记获准版本。'}


def require(job, cfg):
    wait = pending(job, cfg)
    if wait:
        raise ValueError(wait['action'])


def approved_version(job,cfg):
    """Demand this exact human decision even when called outside flow."""
    require(job,cfg)
    title = proposal(job,cfg)
    receipt = read_json(job/'title_approval.json',{})
    if (receipt.get('confirmed_by')!='user' or not str(receipt.get('evidence','')).strip()
            or receipt.get('title_version')!=title['title_version']):
        raise ValueError('生成或复用封面前请先登记用户对当前标题的确认')
    return title['title_version']


def selection_fields(value):
    fields=title_fields(value)
    for key in ('album_core_label','cover_label_placement','cover_accent_line'):
        if key in value:
            fields[key]=value[key]
    import cover_policy
    cover_policy.options(fields)
    return fields


def decide(jobs, versions, evidence):
    if not evidence or not evidence.strip() or len(set(versions)) != len(versions):
        raise ValueError('须记录用户实际答复并指定唯一标题版本')
    # Running jobs outside the user's choice can continue while these titles are approved.
    selected = {}
    for job in jobs:
        cfg = read_json(job/'config.json', {})
        if cfg.get('production_profile') != PROFILE or not (job/'cut_plan.json').is_file():
            continue
        try:
            candidate = proposal(job, cfg)
        except ValueError:
            continue
        if candidate['title_version'] in versions:
            selected[candidate['title_version']] = job
    if set(versions) != selected.keys():
        raise ValueError('所选标题或选段已改变，请展示当前版本')
    with ExitStack() as locks:
        for job in sorted(selected.values()):
            locks.enter_context(file_lock(job/'.pipeline.lock', timeout=2))
        import layout_profile
        approvals = []
        for version, job in selected.items():
            title = proposal(job, read_json(job/'config.json', {}))
            if title['title_version'] != version:
                raise ValueError('所选标题或选段已改变，请展示当前版本')
            for role in ('title_line1','title_line2'):
                layout_profile.fit(title[role], role)
            approvals.append((job, title))
        for job, title in approvals:
            atomic_json(job/'title_approval.json', {**title, 'confirmed_by':'user', 'evidence':evidence})
    return {'confirmed':len(versions)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--manifest', type=Path)
    source.add_argument('--job-dir', type=Path)
    p.add_argument('--accept', nargs='+', metavar='TITLE_VERSION')
    p.add_argument('--evidence', help='本次对这些标题的真实用户答复')
    args = p.parse_args()
    if args.manifest:
        manifest = args.manifest.resolve()
        jobs = [(manifest.parent/Path(value)).resolve() for value in read_json(manifest)['jobs']]
    else:
        jobs = [args.job_dir.resolve()]
    if not jobs or len(set(jobs)) != len(jobs) or any(not (j/'config.json').is_file() for j in jobs):
        p.error('批次需要有效且不重复的任务目录')
    if args.accept:
        result = decide(jobs, args.accept, args.evidence)
    else:
        result = [pending(j, read_json(j/'config.json')) or {'job':str(j), 'status':'no_title_action'} for j in jobs]
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(str(exc))
