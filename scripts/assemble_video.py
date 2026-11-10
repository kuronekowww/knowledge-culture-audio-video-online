"""Choose the requested output backend; the existing CapCut default stays unchanged."""
import argparse
from pathlib import Path
import subprocess
import sys
from artifacts import read_json


def selected(cfg, explicit=None):
    value=explicit or cfg.get('output_backend','capcut')
    if value not in ('capcut','openfilm'):
        raise ValueError('output_backend只支持capcut或openfilm')
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__,add_help=False)
    parser.add_argument('--backend',choices=['capcut','openfilm'])
    parser.add_argument('--job-dir',type=Path,required=True)
    args,remaining=parser.parse_known_args()
    cfg=read_json(args.job_dir/'config.json',{})
    import title_review
    title_review.require(args.job_dir, cfg)
    import presentation
    if presentation.selected(cfg)['mode']=='hyperframes':
        parser.error('网页动画任务请按references/web-animation.md制作真实Hyperframes工程；当前组装器仅接受纯图片交接，不能把HTML直接当作图片组装')
    backend=selected(cfg,args.backend)
    script='openfilm_backend.py' if backend=='openfilm' else 'assemble_draft.py'
    rc=subprocess.run([sys.executable,str(Path(__file__).with_name(script)),
                       '--job-dir',str(args.job_dir),*remaining]).returncode
    if rc or backend!='capcut':return rc
    from delivery import requested
    if requested(cfg)['target']!='mp4':return 0
    # The CapCut draft API has no exporter. Use the same verified handoff to render
    # a companion MP4 only when the job already has an explicitly configured renderer.
    if cfg.get('openfilm',{}).get('project_dir') and not cfg.get('delivery',{}).get('native_capcut_only'):
        project=args.job_dir.resolve()/'mp4-companion'
        rc=subprocess.run([sys.executable,str(Path(__file__).with_name('openfilm_backend.py')),
                    '--job-dir',str(args.job_dir),'--project-dir',str(project),'--action','all']).returncode
        if rc:return rc
        return subprocess.run([sys.executable,str(Path(__file__).with_name('verify_openfilm.py')),
                    '--project-dir',str(project)]).returncode
    print('awaiting_mp4_export：草稿已保存；请进行剪映实际导出与成片复核，或配置同交接的本机OpenFilm渲染器。')
    return 2


if __name__=='__main__':sys.exit(main())
