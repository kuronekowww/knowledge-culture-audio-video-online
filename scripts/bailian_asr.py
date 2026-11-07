"""Resumable Paraformer-v2 REST backend. Never automatically repeat unknown submissions."""
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit, unquote

import requests
import bailian_hotwords
import shared_album
import timing
from artifacts import atomic_copy, atomic_json, digest, file_hash, file_lock, read_json, verified_file

GATE = threading.BoundedSemaphore(3)
DEFAULT_BASE = 'https://dashscope.aliyuncs.com/api/v1'
MODEL = 'paraformer-v2'


def settings(cfg):
    raw = cfg.get('asr', {})
    model = raw.get('model', MODEL)
    if model != MODEL: raise ValueError('百炼后端当前仅支持paraformer-v2，迁移时移除本地model路径')
    base = raw.get('api_base', DEFAULT_BASE).rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme != 'https' or parsed.path != '/api/v1' or parsed.query or parsed.fragment
        or parsed.username or parsed.port not in (None,443)
        or not (parsed.hostname == 'dashscope.aliyuncs.com' or re.fullmatch(r'[A-Za-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com', parsed.hostname or ''))):
        raise ValueError('百炼api_base须为北京官方HTTPS域名，路径/api/v1')
    return {'model':model,'api_base':base}


def key():
    value = os.environ.get('DASHSCOPE_API_KEY', '').strip()
    if not value: raise ValueError('缺少DASHSCOPE_API_KEY环境变量；不写入config或skill')
    return value


def identity(cfg):
    return {'backend':'bailian_paraformer', **settings(cfg),
            'adapter_sha256': file_hash(Path(__file__)),
            'hotwords_adapter_sha256': file_hash(Path(__file__).with_name('bailian_hotwords.py'))}


def sanitize(value, credential=''):
    if isinstance(value, dict):
        return {k:('[redacted URL]' if k in {'file_url','transcription_url'} else sanitize(v,credential)) for k,v in value.items()}
    if isinstance(value, list): return [sanitize(v,credential) for v in value]
    if isinstance(value,str):
        if credential: value=value.replace(credential,'[redacted]')
        return re.sub(r'sk-[A-Za-z0-9._-]+','[redacted]',value)
    return value


def private_json(path, value):
    atomic_json(path,value)
    try: path.chmod(0o600)
    except OSError: pass


def trusted_media_url(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.username or not (p.hostname or '').endswith('.aliyuncs.com'):
        raise ValueError('百炼返回了非预期媒体域名')
    return url


def same_audio_url(returned,submitted):
    if returned==submitted:return True
    # OSS resolution changes scheme/host, but the complete object key must match.
    p=urlsplit(returned)
    return (submitted.startswith('oss://') and p.scheme in {'http','https'} and not p.username
        and p.hostname=='dashscope-file-mgr.oss-cn-beijing.aliyuncs.com'
        and unquote(p.path).lstrip('/')==submitted.removeprefix('oss://'))


def recognition_parameters():
    return {'channel_id':[0],'language_hints':['zh'],'disfluency_removal_enabled':False,
            'timestamp_alignment_enabled':True,'special_word_filter':'{"system_reserved_filter":false}'}


def remote_entry(cfg,audio_sha,names,credential):
    # Adapter/normalizer updates invalidate normalized output, not the paid remote request.
    signature={'audio_sha256':audio_sha,**settings(cfg),'hotwords':names,'parameters':recognition_parameters()}
    return shared_album.album_root(cfg)/'asr-requests'/digest(signature)/'tasks'/digest(credential)


class APIError(RuntimeError):
    def __init__(self,status,code,message):
        self.status=status;self.code=code
        self.definite_rejection=status in {400,401,403,404,413,415,422,429}
        super().__init__(f'百炼HTTP {status} [{code}]: '+str(message)[:500])


def rejected_receipt(path,state,error):
    if error.definite_rejection:
        state.update(status='rejected',http_status=error.status,error_code=error.code,error=str(error))
        private_json(path,state)


class Client:
    def __init__(self,cfg):
        self.base=settings(cfg)['api_base'];self.credential=key()
        self.timeout=float(cfg.get('asr',{}).get('http_timeout_seconds',30))
        if not math.isfinite(self.timeout) or not 1<=self.timeout<=120:raise ValueError('HTTP超时须为1–120秒')

    def request(self,method,path,**kwargs):
        extra=kwargs.pop('headers',{})
        try:
            response=requests.request(method,self.base+path,headers={'Authorization':'Bearer '+self.credential,**extra},
                                      timeout=self.timeout,allow_redirects=False,**kwargs)
        except requests.RequestException as exc:
            raise RuntimeError('百炼网络异常: '+type(exc).__name__) from None
        try:body=response.json()
        except ValueError:body={}
        if response.status_code!=200 or not isinstance(body,dict) or body.get('code'):
            message=sanitize(body.get('message','') if isinstance(body,dict) else '',self.credential)
            raise APIError(response.status_code,str(body.get('code','')) if isinstance(body,dict) else '',message)
        return body

    def vocabulary(self,action,**fields):
        return self.request('POST','/services/audio/asr/customization',json={
            'model':'speech-biasing','input':{'action':action,**fields}})


def ensure_vocabulary(client,cfg,names):
    if not names:return None
    scope=digest({'base':client.base,'credential':digest(client.credential),'model':MODEL,'names':names})
    root=shared_album.album_root(cfg)/'bailian-vocabularies'/scope;path=root/'record.json'
    with file_lock(root/'vocabulary.lock'):
        saved=read_json(path,{})
        identifier=cfg.get('asr',{}).get('vocabulary_id') or saved.get('vocabulary_id')
        if (saved.get('status')=='ready' and saved.get('vocabulary_id')==identifier
            and saved.get('names')==names and time.time()-saved.get('checked_at',0)<900):return identifier
        if not identifier:
            if saved.get('status')=='submitting':raise ValueError('热词创建结果未知，不自动重建；核实后在asr.vocabulary_id指定已有ID')
            private_json(path,{'status':'submitting','names':names,'model':MODEL})
            try:
                with timing.span('asr_hotwords_create'):
                    created=client.vocabulary('create_vocabulary',target_model=MODEL,prefix='nvf'+scope[:7],vocabulary=names)
            except APIError as exc:
                rejected_receipt(path,{'names':names,'model':MODEL},exc)
                raise
            identifier=created['output']['vocabulary_id']
            private_json(path,{'status':'created','names':names,'model':MODEL,'vocabulary_id':identifier})
        deadline=time.monotonic()+30
        with timing.span('asr_hotwords_check'):
            while True:
                query=client.vocabulary('query_vocabulary',vocabulary_id=identifier)['output']
                actual=[{k:row.get(k) for k in ('text','weight','lang')} for row in query.get('vocabulary',[])]
                if query.get('target_model')!=MODEL or sorted(actual,key=lambda x:x['text'])!=sorted(names,key=lambda x:x['text']):
                    raise ValueError('云端热词表与本专辑当前人名/模型不符，不混用')
                if query.get('status')=='OK':break
                if time.monotonic()>=deadline:raise ValueError('热词表尚不可用；保留ID，下次仅查询')
                time.sleep(1)
        private_json(path,{'status':'ready','names':names,'model':MODEL,'vocabulary_id':identifier,'checked_at':time.time()})
        return identifier


def normalize(raw):
    if not isinstance(raw,dict):raise ValueError('百炼结果必须为JSON对象')
    transcripts=raw.get('transcripts',[])
    selected=[t for t in transcripts if t.get('channel_id')==0]
    if len(selected)!=1:raise ValueError('百炼缺少唯一的channel_id=0结果')
    segments=[];last=0
    from subtitle_review import normalized
    for sentence in selected[0].get('sentences',[]):
        words=sentence.get('words',[])
        if not words:raise ValueError('百炼缺少词级时码，不按字数补造')
        joined=''.join(w['text']+w.get('punctuation','') for w in words)
        if normalized(joined)!=normalized(sentence['text']):raise ValueError('百炼句段与词级文字覆盖不一致')
        for w in words:
            a,b=w['begin_time'],w['end_time']
            if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in (a,b)) or a<last or b<=a:
                raise ValueError('百炼词级时码倒序/重叠/无效')
            last=b
        segments.append({'start':words[0]['begin_time']/1000,'end':words[-1]['end_time']/1000,'text':sentence['text'],
                         'words':[{'word':w['text']+w.get('punctuation',''),'start':w['begin_time']/1000,'end':w['end_time']/1000} for w in words]})
    if not segments:raise ValueError('百炼没有有效识别内容')
    return {'backend':'bailian_paraformer','model':MODEL,'segments':segments}


def recognize(client,cfg,audio,entry,names):
    state_path=entry/'task.json';state=read_json(state_path,{})
    raw_path=entry/'asr_raw.json'
    if verified_file(raw_path,state.get('raw_sha256')):return read_json(raw_path)
    if not state.get('task_id'):
        if state.get('status')=='submitting':raise ValueError('ASR提交结果未知，禁止自动重发；核实服务端任务后使用recover-task')
        vocabulary_id=ensure_vocabulary(client,cfg,names) if names else None
        params=recognition_parameters()
        if vocabulary_id:params['vocabulary_id']=vocabulary_id
        if not state.get('uploaded_url') or state.get('expires_at',0)<time.time()+300:
            with timing.span('asr_upload_policy'):
                policy=client.request('GET','/uploads',params={'action':'getPolicy','model':MODEL})['data']
            if audio.stat().st_size>min(int(policy['max_file_size_mb'])*1024*1024,1024**3):raise ValueError('音频超过上传限制')
            obj=policy['upload_dir']+'/'+file_hash(audio)[:24]+audio.suffix
            fields={'OSSAccessKeyId':policy['oss_access_key_id'],'Signature':policy['signature'],'policy':policy['policy'],
                    'key':obj,'success_action_status':'200','x-oss-object-acl':policy['x_oss_object_acl'],
                    'x-oss-forbid-overwrite':str(policy['x_oss_forbid_overwrite']).lower()}
            with audio.open('rb') as source,timing.span('asr_upload'):
                uploaded=requests.post(trusted_media_url(policy['upload_host']),data=fields,
                    files={'file':(Path(obj).name,source,'application/octet-stream')},timeout=60,allow_redirects=False)
                if uploaded.status_code not in (200,201,204):raise ValueError('百炼音频上传失败 HTTP '+str(uploaded.status_code))
            state.update(status='uploaded',uploaded_url='oss://'+obj,expires_at=time.time()+48*3600)
            private_json(state_path,state)
        state.update(status='submitting',parameters=params);private_json(state_path,state)
        try:
            with timing.span('asr_submit'):
                submitted=client.request('POST','/services/audio/asr/transcription',headers={
                    'X-DashScope-Async':'enable','X-DashScope-OssResourceResolve':'enable'},
                    json={'model':MODEL,'input':{'file_urls':[state['uploaded_url']]},'parameters':params})
        except APIError as exc:
            rejected_receipt(state_path,state,exc)
            raise
        state.update(task_id=submitted['output']['task_id'],request_id=submitted.get('request_id'),status='submitted')
        private_json(state_path,state)
    timeout=float(cfg.get('asr',{}).get('timeout_seconds',600))
    if not math.isfinite(timeout) or not 1<=timeout<=900:raise ValueError('ASR轮询超时须为1–900秒')
    deadline=time.monotonic()+timeout
    with timing.span('asr_poll'):
        while not state.get('result_url'):
            response=client.request('POST','/tasks/'+state['task_id'])
            output=response['output'];status=output['task_status']
            state.update(status=status,usage=response.get('usage'),request_id=response.get('request_id'))
            private_json(state_path,state)
            if status=='SUCCEEDED':
                items=output.get('results',[])
                if len(items)!=1 or items[0].get('subtask_status')!='SUCCEEDED':raise ValueError('百炼子任务未成功')
                if state.get('uploaded_url') and not same_audio_url(items[0].get('file_url',''),state['uploaded_url']):
                    raise ValueError('任务返回的音频URL与本次提交不一致')
                state['result_url']=trusted_media_url(items[0]['transcription_url']);private_json(state_path,state);break
            if status not in {'PENDING','RUNNING'}:raise ValueError('百炼任务未成功: '+status+'；保留任务，不重发')
            if time.monotonic()>=deadline:raise ValueError('百炼识别仍在等待；task_id已保存，resume仅继续查询')
            time.sleep(2)
    with timing.span('asr_result_download'):
        response=requests.get(trusted_media_url(state['result_url']),timeout=client.timeout,allow_redirects=False)
        if response.status_code!=200:raise ValueError('百炼结果下载失败；resume仅重试下载，结果链接通常24小时过期')
        raw=response.json()
    # Archive provider output before normalization; remove signed/temporary URLs.
    raw=sanitize(raw,client.credential);private_json(raw_path,raw)
    state['raw_sha256']=file_hash(raw_path);private_json(state_path,state)
    return raw


def transcribe(job,cfg):
    import subtitle_review
    try:
        audio=subtitle_review.audio_path(job);audio_sha=file_hash(audio);ident=identity(cfg)
        names=bailian_hotwords.vocabulary(cfg);prompt=bailian_hotwords.prompt(cfg)
        signature={'audio_sha256':audio_sha,'backend_identity':ident,'hotwords':names,'settings':cfg.get('asr',{})}
        cache_key=digest(signature);entry=shared_album.album_root(cfg)/'asr'/cache_key
        cached=entry/'transcript.json'
        with file_lock(entry/'asr.lock'):
            record=read_json(entry/'record.json',{})
            if not verified_file(cached,record.get('sha256')):
                client=Client(cfg)
                # Account-specific task receipts avoid querying another account's in-flight task.
                task_entry=remote_entry(cfg,audio_sha,names,client.credential);task_entry.mkdir(parents=True,exist_ok=True)
                with timing.span('asr_cloud_queue'):GATE.acquire()
                try:
                    with file_lock(task_entry/'request.lock'):
                        raw=recognize(client,cfg,audio,task_entry,names)
                finally:GATE.release()
                normalized=normalize(raw)
                subtitle_review.units_from_asr(normalized,subtitle_review.duration_us(audio))
                if file_hash(audio)!=audio_sha or bailian_hotwords.vocabulary(cfg)!=names:raise ValueError('识别期间音频或人名词典发生变化')
                atomic_json(cached,normalized)
                atomic_json(entry/'record.json',{'sha256':file_hash(cached),'key':cache_key,
                    'raw_path':str(task_entry/'asr_raw.json'),'raw_sha256':file_hash(task_entry/'asr_raw.json'),
                    'usage':read_json(task_entry/'task.json',{}).get('usage')})
                record=read_json(entry/'record.json')
            else:timing.emit('asr_cache_hit',0)
            if file_hash(audio)!=audio_sha or bailian_hotwords.vocabulary(cfg)!=names:raise ValueError('识别期间输入变化，旧稿未覆盖')
            raw=Path(record.get('raw_path',''))
            if not verified_file(raw,record.get('raw_sha256')):raise ValueError('百炼缓存缺少有效原始响应，不能仅凭转写文件放行')
            atomic_copy(raw,job/'bailian_asr_raw.json');atomic_copy(cached,job/'final_transcript.json')
            atomic_json(job/'final_transcript.meta.json',{'schema':1,'request_hash':cache_key,
                'audio_sha256':audio_sha,'transcript_sha256':file_hash(cached),'backend_identity':ident,
                'asr_settings_hash':digest(cfg.get('asr',{})),'prompt_hash':digest(prompt),
                'hotwords':names,'usage':record.get('usage')})
        return ''
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,requests.RequestException) as exc:
        # Do not emit request headers, signed URLs, credentials or raw network exceptions.
        return '百炼ASR未完成；保留原任务和旧稿，不自动切换本地后端: '+sanitize(
            type(exc).__name__ if isinstance(exc,requests.RequestException) else str(exc),os.environ.get('DASHSCOPE_API_KEY',''))



def recover_task(job,cfg,task_id):
    import subtitle_review
    if not re.fullmatch(r'[A-Za-z0-9-]{8,128}',task_id):raise ValueError('task_id格式无效')
    client=Client(cfg);audio=subtitle_review.audio_path(job);names=bailian_hotwords.vocabulary(cfg)
    entry=remote_entry(cfg,file_hash(audio),names,client.credential)
    with file_lock(entry/'request.lock'):
        path=entry/'task.json';state=read_json(path,{})
        if state.get('status')!='submitting' or not state.get('uploaded_url'):raise ValueError('当前输入没有待核实提交，不替换已有任务')
        response=client.request('POST','/tasks/'+task_id)
        output=response['output']
        if output.get('task_status') not in {'SUCCEEDED','PENDING','RUNNING'}:raise ValueError('指定任务不可恢复')
        results=output.get('results',[])
        if results and any(not same_audio_url(x.get('file_url',''),state['uploaded_url']) for x in results):raise ValueError('指定任务不属于本次音频')
        state.update(task_id=task_id,status='recovered');private_json(path,state)
    return {'status':'recovered','task_id':task_id,'note':'resume继续查询/下载，不重新提交'}
