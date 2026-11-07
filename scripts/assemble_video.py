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
    import presentation
    if presentation.selected(cfg)['mode']=='hyperframes':
        parser.error('网页动画任务请按references/web-animation.md制作真实Hyperframes工程；当前组装器仅接受纯图片交接，不能把HTML直接当作图片组装')
    backend=selected(cfg,args.backend)
    script='openfilm_backend.py' if backend=='openfilm' else 'assemble_draft.py'
    return subprocess.run([sys.executable,str(Path(__file__).with_name(script)),
                           '--job-dir',str(args.job_dir),*remaining]).returncode


if __name__=='__main__':sys.exit(main())
