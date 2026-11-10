"""Compile approved visual direction and grounded shots into the production image manifest."""
import argparse
import json
from pathlib import Path
import re

import image_finish

from artifacts import atomic_json, file_hash, read_json
from title_review import PROFILE, require

BODY_RULE = ('单幅横向画面，表现一个清楚的瞬间或关系，主要人物或物件轮廓可辨，层次清楚。'
             '画内不放文字、字幕、数字、水印、标志或界面。示意场景不画成原声未提供的证据。')


def nonempty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('缺少可用的' + field)
    return value.strip()


def shot_rows(shots, units, bible):
    known = {row['id']:row for row in units}
    subjects = bible.get('characters', {})
    if not isinstance(subjects, dict):
        raise ValueError('visual_bible.characters 使用代号到描述的对象，非人物内容可省略')
    setting = bible.get('era_region') or bible.get('era') or bible.get('setting')
    seen_ids, seen_scenes = set(), set()
    for shot in shots:
        sid = shot.get('id')
        if not isinstance(sid, str) or not re.fullmatch('[A-Za-z0-9_-]+', sid) or sid in seen_ids or sid in {'cover_portrait','album_background'}:
            raise ValueError('镜头编号缺失、保留或重复：' + str(sid))
        seen_ids.add(sid)
        ids = shot.get('source_unit_ids')
        if not isinstance(ids, list) or not ids or any(key not in known for key in ids):
            raise ValueError(sid + ' 引用了不存在的原声词或遗漏来源')
        a, b = float(shot['start'])*1e6, float(shot['end'])*1e6
        if not any(known[key]['start_us'] < b and known[key]['end_us'] > a for key in ids):
            raise ValueError(sid + ' 引用的原声时刻与本镜不重合')
        nonempty(shot.get('information_point') or shot.get('beat'), sid+' 信息点')
        scene = nonempty(shot.get('description'), sid+' 可见画面')
        scene_key = ''.join(scene.split())
        if scene_key in seen_scenes:
            raise ValueError(sid+' 与前镜的画面描述完全相同')
        seen_scenes.add(scene_key)
        actors = shot.get('subjects', [])
        if not isinstance(actors, list) or any(key not in subjects for key in actors):
            raise ValueError(sid+' 有未定义的主体代号')
        fragments = [BODY_RULE, '本镜画面：'+scene]
        if actors:
            fragments.append('仅画本镜出场主体：'+'；'.join(nonempty(subjects[k], k) for k in actors))
        if setting:
            fragments.append('场景设定：'+nonempty(setting, '时代或场景设定'))
        if shot.get('composition'):
            fragments.append('构图：'+nonempty(shot['composition'], '构图'))
        yield {'id':sid,'filename':sid+'.png','size':'1536x1024','prompt':'\n'.join(fragments)}


def preserve_overrides(rows, old):
    previous = {row['id']:row for row in old.get('images', [])}
    for row in rows:
        source = previous.get(row['id'], {})
        if source and 'quality' not in source:
            row.pop('quality',None)  # retain historical manifest fallback without rewriting paid recipes
        for key in ('revision', 'backend', 'quality'):
            if key in source:
                row[key] = source[key]


def compile_job(job, cfg):
    require(job, cfg)
    bible = read_json(job/'visual_bible.json', {})
    cover = nonempty(bible.get('cover_scene'), 'visual_bible.cover_scene（本集封面主体、动作与场景）')
    story = read_json(job/'storyboard.json', {})
    shots = story.get('shots')
    if not isinstance(shots, list) or not shots:
        raise ValueError('缺少正文分镜')
    units = read_json(job/'subtitle_review_request.json', {}).get('units', [])
    rows = [{'id':'cover_portrait','filename':'cover_portrait.png','size':'1024x1536',
             'cover_fit':'full_width_9x16','quality':cfg.get('cover_generation_quality',__import__('layout_profile').load()['cover_design']['quality']),'prompt':cover}]
    rows.extend(shot_rows(shots, units, bible))
    # Keep operator-authorized single-shot revisions stable across resumes.
    path = job/'image_manifest.json'
    old = read_json(path, {})
    preserve_overrides(rows, old)
    manifest = {'compiled_by':PROFILE,'approved_title_version':read_json(job/'title_approval.json')['title_version'],
                'output_dir':'images/generated','quality':'low','images':rows}
    # No intermediate unbound file. Compose the exact outgoing prompt once.
    import creative_options
    creative_options.bind_prompts(job, cfg, manifest)


def sample(job, manifest):
    """Three fixed spots for the same cover review. Hash changed sample images only."""
    shots = read_json(job/'storyboard.json')['shots']
    by_id = {row['id']:row for row in manifest['images']}
    result = []
    for idx in sorted({0, len(shots)//2, len(shots)-1}):
        shot = shots[idx]
        path = manifest['output_dir']/by_id[shot['id']]['filename']
        result.append({'id':shot['id'], 'path':str(path), 'sha256':file_hash(path),
                       'point':shot.get('information_point') or shot.get('beat'),
                       'expected_scene':shot.get('description')})
    return result


def sample_basis(rows):
    from artifacts import digest
    return digest([{k:v for k,v in row.items() if k!='path'} for row in rows])


def compile_web(job, cfg, project):
    """Only explicitly requested image inserts use this path; templates need no images."""
    from artifacts import digest
    require(job, cfg)
    context = read_json(project/'web-animation-context.json', {})
    if not context.get('job_dir') or Path(context['job_dir']).resolve() != job.resolve():
        raise ValueError('网页工程须绑定当前声音')
    story = read_json(project/'web-storyboard.json', {})
    bible = read_json(job/'visual_bible.json', {})
    inserts = [shot for shot in story.get('shots', []) if shot.get('type')=='image' and not shot.get('reuse_asset')]
    path = project/'web-image-manifest.json'
    old = read_json(path, {})
    if not inserts:
        if old.get('compiled_by') == PROFILE and old.get('images'):
            atomic_json(path, {**old, 'images':[]})
        return {'count':0}
    style = bible.get('style_prompt') or bible.get('selected_style', {}).get('prompt')
    if not style:
        raise ValueError('网页插图需要 visual_bible.style_prompt，沿用当前专辑画风与网页主色')
    units = read_json(job/'subtitle_review_request.json')['units']
    rows = list(shot_rows([{**shot,'id':shot['asset_id']} for shot in inserts], units, bible))
    preserve_overrides(rows, old)
    for row in rows:
        row['prompt'] = '【统一画风】'+style+'\n'+row['prompt']
    manifest = {'compiled_by':PROFILE,'output_dir':'assets/inserts','quality':'low','images':rows,
                'approved_title_version':read_json(job/'title_approval.json')['title_version']}
    image_finish.finish_manifest(manifest, old)
    if digest(old)!=digest(manifest):
        atomic_json(path, manifest)
    return {'count':len(rows),'manifest':str(path)}


def compile_web_cover(job,cfg):
    import cover_policy
    from title_review import approved_version
    version = approved_version(job,cfg)
    bible=read_json(job/'visual_bible.json',{})
    copy=read_json(job/'copy.json')
    style=nonempty(bible.get('style_prompt') or bible.get('selected_style',{}).get('prompt'),'网页专辑画风')
    scene=nonempty(bible.get('cover_scene'),'本集封面主体与场景')
    row={'id':'cover_portrait','size':'1024x1536','cover_fit':'full_width_9x16','filename':'cover_portrait.png','quality':cfg.get('cover_generation_quality',__import__('layout_profile').load()['cover_design']['quality']),
         'prompt':'【统一画风】'+style+'\n'+scene+cover_policy.prompt(copy)}
    path=job/'web-cover-manifest.json'
    old=read_json(path,{})
    submitted = cover_policy.submitted_r24_cover(job,cfg,old)
    previous = (submitted is not None and old.get('approved_title_version')==version
                and submitted['prompt']=='【统一画风】'+style+'\n'+scene+cover_policy.prompt(copy,previous=True))
    if previous:
        row = dict(submitted)
    preserve_overrides([row],old)
    if row.get('backend'):
        row.update(size='1080x1920')
        row.pop('cover_fit',None)
    manifest={'compiled_by':PROFILE,'approved_title_version':version,
              'cover_policy':cover_policy.R24_BINDING if previous else cover_policy.binding(),
              'output_dir':'images/generated','quality':'low','images':[row]}
    image_finish.finish_manifest(manifest, old)
    if manifest!=old:
        atomic_json(path,manifest)
    return {'manifest':str(path),'cover':str(job/'images/generated/cover_portrait.png')}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job-dir', type=Path, required=True)
    route = p.add_mutually_exclusive_group()
    route.add_argument('--web-project', type=Path)
    route.add_argument('--web-cover', action='store_true')
    route.add_argument('--review-web-cover', action='store_true')
    p.add_argument('--image',type=Path,help='复核已有网页封面的实际图片')
    args = p.parse_args()
    job = args.job_dir.resolve()
    cfg = read_json(job/'config.json', {})
    if cfg.get('production_profile') != PROFILE:
        raise ValueError('历史工程保留原图片清单；此入口用于团队版新任务')
    from presentation import selected
    if args.web_cover and selected(cfg)['mode']=='hyperframes':
        result=compile_web_cover(job,cfg)
    elif args.review_web_cover and selected(cfg)['mode']=='hyperframes':
        import cover_policy
        result=cover_policy.web_review_request(job,cfg,args.image or job/'images/generated/cover_portrait.png')
        result['crop_preview']=cover_policy.crop_preview(job,Path(result['cover_path']),result['basis']['image_sha256'])
        atomic_json(job/'web-cover-review-request.json',result)
    elif args.web_project and selected(cfg)['mode']=='hyperframes':
        result = compile_web(job, cfg, args.web_project.resolve())
    elif not (args.web_project or args.web_cover or args.review_web_cover) and selected(cfg)['mode']=='images':
        compile_job(job, cfg)
        result = {'manifest':str(job/'image_manifest.json')}
    else:
        raise ValueError('网页模式使用 --web-project；纯图片模式使用任务目录')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(str(exc))
