"""Explicit migration of existing jobs, keeping unrelated configuration and media."""
import argparse
from pathlib import Path
import time
import json

from artifacts import read_json, atomic_json, atomic_copy, file_lock


def migrate(job):
    job=job.expanduser().resolve()
    with file_lock(job/'.pipeline.lock',timeout=2):
        path=job/'config.json';cfg=read_json(path)
        if not cfg or not cfg.get('album_id'):raise ValueError('无有效job')
        if cfg.get('asr',{}).get('backend')=='bailian_paraformer' and cfg.get('subtitle_review_mode')=='full':
            return {'status':'already_configured','job':str(job)}
        backup=job/'config_history'/f'before-bailian-{time.time_ns()}.json'
        atomic_copy(path,backup)
        cfg['asr']={'backend':'bailian_paraformer','model':'paraformer-v2','use_hotwords':False}
        cfg['subtitle_review_mode']='full'
        atomic_json(path,cfg)
        # Proactively invalidate the ASR-dependent stages, preserving cuts and image bytes/receipts.
        import state
        saved=state.load_state(job)
        for stage in ('subtitles','storyboard','prepare_handoff','publish_handoff'):saved['stages'][stage]='pending'
        state.save_state(job,saved)
        return {'status':'configured','job':str(job),'backup':str(backup)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job-dir',required=True,type=Path)
    p.add_argument('--recover-task',help='仅接管已核实的当前未知提交task_id，不提交新任务')
    a=p.parse_args()
    if a.recover_task:
        import bailian_asr
        with file_lock(a.job_dir/'.pipeline.lock',timeout=2):
            result=bailian_asr.recover_task(a.job_dir,read_json(a.job_dir/'config.json'),a.recover_task)
    else:result=migrate(a.job_dir)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
