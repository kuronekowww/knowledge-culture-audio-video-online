"""Server-only preflight and actual handoff delivery checks."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import config
from artifacts import atomic_json, file_hash, read_json


def path_at(job, value):
    path=Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (job/path).resolve()


def preflight(jobs):
    checks=[]
    def check(name, call):
        try: checks.append({'name':name,'ok':True,'detail':call()})
        except Exception as exc: checks.append({'name':name,'ok':False,'error':str(exc)})
    def runtime():
        result=subprocess.run([config.PY,'-c','import requests,PIL,sys;print(sys.version)'],capture_output=True,text=True,timeout=30)
        if result.returncode: raise ValueError('运行环境缺少 requests/Pillow 或解释器不可用')
        return result.stdout.strip()
    def writable(path):
        path.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryFile(dir=path) as f: f.write(b'preflight')
        return str(path)
    def binary(name):
        if not shutil.which(name): raise ValueError('缺少 '+name)
        return shutil.which(name)
    check('runtime',runtime)
    for name in ('ffmpeg','ffprobe'): check(name,lambda n=name:binary(n))
    spec=config.load_spec()
    if any(read_json(job/'config.json',{}).get('creative_options',{}).get('branding_enabled',True) is True for job in jobs):
        for kind in ('overlay','outro'):
            for direction,raw in spec['branding'][kind].items():
                check(f'brand/{kind}/{direction}',lambda p=config.SKILL_DIR/raw:file_hash(p))
    if any(read_json(job/'config.json',{}).get('creative_options',{}).get('branding_enabled',True) is True for job in jobs):
        import branding_assets
        check('brand/playback_sticker',lambda:branding_assets.playback_sticker(spec['branding']['elements']['playback'],2_000_000))
    import shared_album, xql_catalog, bailian_asr, bailian_hotwords
    for job in jobs:
        cfg=read_json(job/'config.json',{})
        check('write/'+job.name,lambda j=job:writable(j))
        check('cache/'+job.name,lambda c=cfg:writable(shared_album.album_root(c)))
        def catalog(c=cfg,j=job):
            if c.get('master_catalog'):
                import master_catalog
                row=master_catalog.selected_row(j,c)
                return {'source':'master_catalog','selected_track_id':row['track_id'],'rows':1}
            if c.get('csv'): return {'rows':len(shared_album.input_rows(j,c))}
            if (j/xql_catalog.SNAPSHOT).is_file(): return {'rows':len(xql_catalog.read_snapshot(j/xql_catalog.SNAPSHOT,c['album_id']))}
            xql_catalog.signature();return '签名存在；未发起查询'
        check('catalog/'+job.name,catalog)
        def asr(c=cfg):
            if c.get('asr',{}).get('backend','bailian_paraformer')!='bailian_paraformer':
                raise ValueError('线上入口使用百炼；旧本地任务请先显式迁移')
            bailian_asr.key();return '密钥存在；未调用识别'
        check('asr/'+job.name,asr)
    return {'ok':bool(jobs) and all(x['ok'] for x in checks),'checks':checks,
            'note':'此处仅预检素材生产；组装另查本机项目/服务或线上服务。未验证网络、额度或MOSN访问权限；本地组装不要求上传。'}


def delivery(manifest):
    import handoff
    import draft_verify
    import assemble_draft, assembly_runtime
    path=Path(manifest).resolve();rows=[]
    for raw in read_json(path,{}).get('jobs',[]):
        job=path_at(path.parent,raw)
        try:
            completed=assemble_draft.current(job)
            if completed:
                rows.append({'job':str(job),'ok':True,'draft_dir':completed['draft_dir'],'drafts_verified':{'portrait':True}})
                continue
            if assembly_runtime.profile()['mode']=='local':
                handoff.current_prepared(job,read_json(job/'config.json'))
                rows.append({'job':str(job),'ok':True,'prepared_locally':True,'drafts_verified':{'portrait':False}})
                continue
            payload=handoff.validate(job,read_json(job/'config.json'))
            verified={direction:draft_verify.report_current(job,direction,payload) for direction in ('portrait',)}
            rows.append({'job':str(job),'ok':True,'handoff':str(job/'capcut_handoff.json'),'drafts_verified':verified})
        except (ValueError,OSError,KeyError,TypeError) as exc:
            rows.append({'job':str(job),'ok':False,'error':str(exc)})
    result={'status':('draft_verified' if rows and all(x['ok'] and x.get('drafts_verified',{}).get('portrait') for x in rows) else
                      'ready_for_local_assembly' if assembly_runtime.profile()['mode']=='local' and rows and all(x['ok'] for x in rows) else
                      'ready_for_capcut' if rows and all(x['ok'] for x in rows) else 'incomplete'),
            'ready':sum(x['ok'] for x in rows),'expected':len(rows),'jobs':rows,
            'drafts_verified':sum(sum(r.get('drafts_verified',{}).values()) for r in rows),
            'drafts_expected':len(rows),'drafts_created_here':sum(bool(r.get('drafts_verified',{}).get('portrait')) for r in rows),'mp4_exported_here':0}
    atomic_json(path.with_suffix('.delivery.json'),result);return result

