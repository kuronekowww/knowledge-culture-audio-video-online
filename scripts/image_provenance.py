"""Generation failure categories and explicit external-image provenance."""
import argparse
import json
from pathlib import Path
import requests
from artifacts import atomic_copy, atomic_json, digest, file_hash, file_lock, read_json


def error_details(exc, phase='generation'):
    status=getattr(exc,'http_status',None)
    if phase=='download':kind='download_failed'
    elif isinstance(exc,requests.Timeout):kind='request_timeout'
    elif isinstance(exc,requests.ConnectionError):kind='connection_failed'
    elif status in (401,403):kind='authentication_or_permission'
    elif status==429:kind='rate_limited'
    elif status and status>=500:kind='service_error'
    elif status:kind='http_error'
    else:kind='invalid_response_or_local_error'
    return {'category':kind,'phase':phase,'http_status':status,'request_id':getattr(exc,'request_id',None),
            'billing_state':'unknown' if phase=='generation' else 'generation_result_received',
            'next_action':'retry_download' if phase=='download' else 'inspect_receipt_or_query_provider_before_resubmit'}


def import_image(manifest,image_id,source,provider,model,reviewer,evidence):
    import generate_images as images
    if not all(isinstance(x,str) and x.strip() for x in (provider,model,reviewer,evidence)):
        raise ValueError('需要实际provider/model（未知时明确unknown）/reviewer/evidence')
    manifest=Path(manifest).resolve();source=Path(source).resolve()
    with file_lock(manifest.parent/'.pipeline.lock',timeout=2):
        raw=read_json(manifest);data=images.validate_manifest(raw,manifest)
        item=next((i for i in raw['images'] if i['id']==image_id),None)
        if not item:raise ValueError('未知镜头ID')
        images.verify_image(source)
        with images.Image.open(source) as im:dimensions=list(im.size)
        portrait=image_id in {'cover_portrait','album_background'}
        if (portrait and dimensions[0]>=dimensions[1]) or (not portrait and dimensions[0]<=dimensions[1]):raise ValueError('实际图片方向错误')
        target=data['output_dir']/item['filename']
        # Save history before changing either provenance or manifest; user approvals will invalidate by fingerprint/hash.
        old=read_json(target.parent/'.receipts'/f'{image_id}.json',{})
        if target.is_file():
            backup=target.parent/'.history'/image_id/(file_hash(target)+target.suffix)
            atomic_copy(target,backup);atomic_json(backup.with_suffix('.receipt.json'),old)
        item['backend']={'provider':provider,'model':model,'mode':'external_import'}
        fingerprint=images.image_fingerprint(raw.get('api_base',images.DEFAULT_API_BASE),item,data['quality'])
        provenance={'provider':provider,'model':model,'mode':'external_import','source_path':str(source),
                    'source_sha256':file_hash(source),'requested_size':item['size'],'actual_size':dimensions,
                    'prompt':item['prompt'],'prompt_hash':digest(item['prompt']),'reviewer':reviewer,'evidence':evidence}
        if source!=target.resolve():atomic_copy(source,target)
        atomic_json(target.parent/'.receipts'/f'{image_id}.json',
                    {'fingerprint':fingerprint,'sha256':file_hash(target),'provenance':provenance})
        atomic_json(manifest,raw)
        return {'status':'imported_requires_image_approval','image_id':image_id,'path':str(target),'provenance':provenance}


def resolve_url(manifest,image_id,cache_dir,url,request_id,evidence):
    import generate_images as images
    if not url.startswith(('https://','http://')) or not request_id.strip() or not evidence.strip():raise ValueError('需要已核实结果URL、request_id和查询依据')
    manifest=Path(manifest).resolve();raw=read_json(manifest);data=images.validate_manifest(raw,manifest)
    item=next(i for i in data['images'] if i['id']==image_id)
    fp=images.image_fingerprint(raw.get('api_base',images.DEFAULT_API_BASE),item,data['quality'])
    entry=Path(cache_dir)/fp
    with file_lock(entry/'task.lock'):
        saved=read_json(entry/'request.json',{})
        if saved.get('fingerprint')!=fp or saved.get('status') not in ('submitting','generation_uncertain','generation_failed'):
            raise ValueError('没有匹配的未知结果请求')
        if saved.get('request_id') and saved['request_id']!=request_id:raise ValueError('请求ID不匹配')
        atomic_json(entry/'request.json',{**saved,'status':'download_pending','url':url,'request_id':request_id,
                                        'resolution_evidence':evidence})
    return {'status':'download_pending','next_action':'运行原images命令，仅下载，不重新生成'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['import','resolve-url'])
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--id',required=True)
    p.add_argument('--source',type=Path);p.add_argument('--provider');p.add_argument('--model');p.add_argument('--reviewer');p.add_argument('--evidence',required=True)
    p.add_argument('--cache-dir',type=Path);p.add_argument('--url');p.add_argument('--request-id');a=p.parse_args()
    try:
        result=import_image(a.manifest,a.id,a.source,a.provider,a.model,a.reviewer,a.evidence) if a.command=='import' else resolve_url(a.manifest,a.id,a.cache_dir,a.url,a.request_id,a.evidence)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,OSError,TypeError,KeyError,StopIteration) as exc:print(str(exc));return 2


if __name__=='__main__':raise SystemExit(main())
