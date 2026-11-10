"""Assemble the compiled portrait draft locally or online, save, verify and localize."""
import argparse
import contextlib
import copy
import json
from pathlib import Path, PurePosixPath
import sys
import time
from urllib.parse import urlsplit, parse_qs, unquote
import uuid
import requests

import config
import assembly_api
import assembly_runtime as runtime
import draft_verify
import draft_naming
import handoff
from artifacts import atomic_json, read_json, digest, file_hash, file_lock, hash_session
from portable_prepare import prepare
from portable_validate import validate


def relative_file(url, identifier):
    parts=PurePosixPath(unquote(urlsplit(url).path)).parts
    if '..' in parts or identifier not in parts:raise ValueError('下载文件不属于本草稿')
    value=Path(*parts[parts.index(identifier)+1:])
    if not value.parts or value.is_absolute() or any(x in ('.','..') or '\\' in x for x in value.parts):
        raise ValueError('下载路径无效')
    return value


def mutate(client,folder,index,step,body):
    name=f"{index:03d}-{step['step_id']}";receipt=folder/(name+'.json')
    saved=read_json(receipt,{});signature=digest(body)
    if saved:
        if saved.get('request_hash')!=signature:raise ValueError('已有步骤请求变化：'+name)
        if saved.get('status')!='done':raise ValueError('写入结果未知：'+name+'；核实服务端后恢复回执，或显式--new-build新建')
        return saved['response']
    atomic_json(folder/(name+'.request.json'),body)
    atomic_json(receipt,{'status':'submitting','request_hash':signature,'endpoint':step['endpoint'],'started_at':time.time()})
    # No retries. A disconnect or crash leaves submitting until reconciled.
    response=client.call(step['endpoint'],body)
    atomic_json(receipt,{'status':'done','request_hash':signature,'endpoint':step['endpoint'],'response':response,'finished_at':time.time()})
    return response


def download(client,entry,identifier):
    data=client.call('get_draft',{'draft_id':identifier})
    atomic_json(entry/'get_draft.response.json',data)
    urls=data.get('files');assert isinstance(urls,list) and urls,'get_draft未返回文件清单'
    folder=entry/'downloaded';folder.mkdir(exist_ok=True)
    receipts=read_json(entry/'downloads.json',{});seen=set()
    for url in urls:
        client.valid_draft_url(url)
        rel=relative_file(url,identifier);key=rel.as_posix()
        if key in seen:raise ValueError('下载文件清单重复路径')
        seen.add(key);target=folder/rel
        if target.is_file() and receipts.get(key,{}).get('sha256')==file_hash(target):continue
        target.parent.mkdir(parents=True,exist_ok=True);part=target.with_name(target.name+'.part')
        with requests.get(url,stream=True,allow_redirects=False,timeout=(10,120)) as response:
            if response.is_redirect:raise ValueError('草稿下载重定向未执行')
            response.raise_for_status()
            with part.open('wb') as stream:
                for chunk in response.iter_content(262144):
                    if chunk:stream.write(chunk)
        if not part.stat().st_size:raise ValueError('草稿下载文件为空')
        part.replace(target);receipts[key]={'sha256':file_hash(target),'bytes':target.stat().st_size};atomic_json(entry/'downloads.json',receipts)
    return folder


def checked_report(job,payload,folder):
    line=payload['timelines'][0];value=draft_verify.verify(line,payload['assets'],folder)
    value.update(source_version=payload['source_version'],handoff_sha256=file_hash(job/'assembly_handoff.json'),
                 timeline_hash=digest(line),orientation='portrait',verifier_sha256=file_hash(Path(draft_verify.__file__)))
    atomic_json(job/'draft_verification.assembly.portrait.json',value)
    if value['status']!='passed':raise ValueError('保存草稿核验失败：'+'；'.join(value['errors'][:5]))
    return value


def install_root(cfg,mode):
    raw=cfg.get('capcut',{}).get('draft_root')
    if raw:return Path(raw).expanduser().resolve()
    if mode=='local':
        candidate=Path.home()/'Movies/JianyingPro/User Data/Projects/com.lveditor.draft'
        if candidate.is_dir():return candidate
    return None


def current(job):
    value=read_json(job/'draft_result.json',{})
    if value.get('status')!='draft_verified' or value.get('mode')!=runtime.profile()['mode']:return None
    try:
        payload=read_json(job/'assembly_handoff.json')
        if payload['source_version']!=handoff.source_fingerprint(job,read_json(job/'config.json')):return None
        if value.get('handoff_sha256')!=file_hash(job/'assembly_handoff.json'):return None
        check=read_json(Path(value['verification_report']))
        if check.get('verifier_sha256')!=file_hash(Path(draft_verify.__file__)):return None
        if not all(file_hash(Path(p))==sha for p,sha in check['draft_files_sha256'].items()):return None
        if not all(file_hash(Path(p))==sha for p,sha in check['media_sha256'].items()):return None
        if check.get('status')!='passed':return None
        if value.get('draft_name')!=draft_naming.requested(job,read_json(job/'config.json')):return None
        if check.get('draft_meta_path') and file_hash(Path(check['draft_meta_path']))!=check.get('draft_meta_sha256'):return None
        return value
    except (OSError,KeyError,ValueError,TypeError):return None


@hash_session()
def assemble(job,cfg,mode=None,new_build=False,no_install=False):
    settings=runtime.backend(cfg,mode);selected=settings['mode']
    if selected=='local':
        request=handoff.prepare(job,cfg);plan=read_json(job/'handoff_plan.json')
        asset_context=runtime.local_media(request,settings['local_asset_port'])
        api_context=runtime.local_api(job,cfg,settings)
    else:
        import run
        error=run.stage_publish_handoff(job,cfg,mode="online")
        if error:raise ValueError(error)
        plan=handoff.validate(job,cfg);asset_context=contextlib.nullcontext(plan['assets']);api_context=contextlib.nullcontext()
    with asset_context as assets, api_context:
        client=runtime.Client(settings)
        capability=runtime.create_name_capability(client)
        line=copy.deepcopy(plan['timelines'][0])
        if capability['field']:line['create_draft_name_field']=capability['field']
        line['api_sequence']=assembly_api.compile_timeline(line,assets)
        payload={**plan,'assets':assets,'timelines':[line],'assembly_mode':selected,'draft_naming_capability':capability}
        atomic_json(job/'assembly_handoff.json',payload)
        seq=line['api_sequence']
        version=digest({'source':plan['source_version'],'sequence':seq['sequence_hash'],'mode':selected,'base':settings['api_base']})
        root=job/'draft_runs'/version;root.mkdir(parents=True,exist_ok=True)
        pointer=root/'active.json';active=read_json(pointer,{})
        if new_build or not active:
            active={'attempt':uuid.uuid4().hex};atomic_json(pointer,active)
        entry=root/active['attempt'];entry.mkdir(exist_ok=True)
        draft_url=None
        for index,step in enumerate(seq['steps']):
            endpoint,body=assembly_api.request_body(line,assets,index,draft_url)
            response=mutate(client,entry,index,step,body)
            if response.get('draft_url'):draft_url=response['draft_url']
            if not draft_url:raise ValueError('创建响应缺少draft_url')
            client.valid_draft_url(draft_url)
            print(json.dumps({'step':index+1,'total':len(seq['steps']),'endpoint':endpoint,'status':'done'},ensure_ascii=False),flush=True)
        identifier=parse_qs(urlsplit(draft_url).query)['draft_id'][0]
        result={'status':'saved_unverified','mode':selected,'draft_url':draft_url,'draft_id':identifier,'entry':str(entry),
                'source_version':plan['source_version'],'draft_name':line['draft_name'],'server_name_capability':capability,'mp4_exported':False,'body_duration_seconds':line['body_duration_us']/1e6,
                'total_duration_seconds':line['total_duration_us']/1e6,'handoff_sha256':file_hash(job/'assembly_handoff.json')}
        atomic_json(job/'draft_result.json',result)
        source=download(client,entry,identifier)
        import jianying_profile
        jianying_profile.finalize(source,line,assets)
        result['server_name_matches']=read_json(source/'draft_meta_info.json',{}).get('draft_name')==line['draft_name']
        target_root=None if no_install else install_root(cfg,selected)
        target_root=target_root or entry/'portable'
        previous=read_json(entry/'portable.json',{})
        target=Path(previous['target_dir']) if previous else None
        if target is None or not validate(target)['ok']:
            name=draft_naming.directory_name(line['draft_name'])
            if (target_root/name).exists():name=draft_naming.directory_name(line['draft_name'])+'_'+identifier
            portable=prepare(source,target_root,name);atomic_json(entry/'portable.json',portable);target=Path(portable['target_dir'])
        meta=draft_naming.apply_metadata(target,line['draft_name'])
        report=checked_report(job,payload,target)
        report.update(draft_name=line['draft_name'],draft_meta_path=str(meta),draft_meta_sha256=file_hash(meta))
        atomic_json(job/'draft_verification.assembly.portrait.json',report)
        result.update(status='draft_verified',draft_dir=str(target),verification_report=str(job/'draft_verification.assembly.portrait.json'),
                      installed_in_editor=(target_root!=entry/'portable'),rendered_visual_review='not_performed')
        atomic_json(job/'draft_result.json',result)
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir',type=Path,required=True)
    parser.add_argument('--mode',choices=['local','online'])
    parser.add_argument('--new-build',action='store_true',help='explicitly create a separate new draft')
    parser.add_argument('--no-install',action='store_true')
    args=parser.parse_args();job=args.job_dir.resolve()
    try:
        with file_lock(job/'.pipeline.lock',timeout=2),file_lock(job/'.assembly.lock',timeout=2):
            result=assemble(job,read_json(job/'config.json'),args.mode,args.new_build,args.no_install)
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as exc:
        # Do not echo remote bodies/URLs or environment values in error output.
        print(json.dumps({'status':'needs_attention','error_type':type(exc).__name__,'message':str(exc)[:500]},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
