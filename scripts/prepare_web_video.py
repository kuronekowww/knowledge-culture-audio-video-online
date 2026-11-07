"""Prepare an isolated web-animation authoring folder; never generate or render media."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from artifacts import atomic_json, file_hash, read_json
import layout_profile
import presentation

ROOT = Path(__file__).resolve().parent.parent

def prepare(job, destination):
    job=Path(job).resolve();destination=Path(destination).resolve()
    cfg=read_json(job/'config.json',{})
    choice=presentation.selected(cfg)
    if choice['mode']!='hyperframes':
        raise ValueError('纯图片任务继续使用原流程；不能自动切换为网页动画')
    if destination.exists():
        raise ValueError('目标目录已存在，保留已有编辑；续作请读取现有上下文，或选择新的版本目录')
    if destination==job or destination in job.parents:
        raise ValueError('工程目录不能覆盖任务目录')
    layout_profile.check_assets()
    source=ROOT/'assets/knowledge-templates'
    if read_json(source/'config/layout-binding.json')['sha256'] != layout_profile.binding()['sha256']:
        raise ValueError('知识模板参考的布局绑定已过期；先迁移并核对模板，不能仅改哈希冒充已更新')
    destination.mkdir(parents=True)
    try:
        reference=destination/'template-reference'
        shutil.copytree(source,reference)
        binding=read_json(reference/'config/layout-binding.json')
        binding.update(source_path=str(ROOT/'layout-profile.json'),**layout_profile.binding())
        atomic_json(reference/'config/layout-binding.json',binding)
        # Gallery is a reference, deliberately not the root production composition.
        (reference/'REFERENCE_ONLY.txt').write_text('这里是已核验模板参考，不是当前声音的成片；正式内容和原声时码需在工程根目录另行编排。\n')
        context={
            'status':'authoring_required', 'job_dir':str(job),
            'presentation':choice, 'layout_profile':layout_profile.binding(),
            'layout_source':str(ROOT/'layout-profile.json'),
            'reference_gallery':'template-reference/index.html',
            'opening_styles':{s:str(ROOT/'vendor/motion-showreel/references/styles'/f'{s}.md') for s in dict.fromkeys((*presentation.OPENING_STYLES,*([choice['opening_style']] if choice.get('opening_style','auto')!='auto' else [])))},
            'instructions':str(ROOT/'references/web-animation.md'),
            'existing_inputs':{},
            'remaining':['按真实字幕选开场与分镜','制作图像动画开场','填入正文模板','按需生成并插入图片',
                         '创建并验证真实Hyperframes composition','预览与最终输出'],
            'rendered':False
        }
        for name in ('cut_plan.json','cut_result.json','copy.json','subtitle_review_request.json','subtitle_review_result.json','transcript.json'):
            path=job/name
            if path.is_file():context['existing_inputs'][name]={'path':str(path),'sha256':file_hash(path)}
        atomic_json(destination/'web-animation-context.json',context)
        presentation.web_request(job,cfg)
        return {'status':context['status'],'project_dir':str(destination),'context':str(destination/'web-animation-context.json'),'rendered':False}
    except Exception:
        # This directory was newly created by this attempt, never an existing project.
        shutil.rmtree(destination)
        raise

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job-dir',type=Path,required=True)
    p.add_argument('--project-dir',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(prepare(a.job_dir,a.project_dir),ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except (ValueError,OSError) as e:print(str(e),file=sys.stderr);sys.exit(1)
