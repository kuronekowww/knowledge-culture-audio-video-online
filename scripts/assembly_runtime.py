"""Select explicit local/online backend; serve only current reviewed local assets."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import urlsplit, quote, unquote
import requests
import config
from artifacts import read_json, file_hash

PREFIX = '/openapi/capcut-mate/v1'


def profile(mode=None):
    value = read_json(config.SKILL_DIR/'runtime_profile.json')
    selected = mode or value['mode']
    if selected not in ('local','online'):
        raise ValueError('组装模式必须为local或online')
    return {**value,'mode':selected}


def loopback(host):
    if host == 'localhost': return True
    try: return ipaddress.ip_address(host).is_loopback
    except ValueError: return False


def backend(cfg, mode=None):
    p = profile(mode); raw = cfg.get('capcut',{})
    base = (raw.get('local_api_base') or p['local_api_base']) if p['mode']=='local' else raw.get('api_base')
    if not base: raise ValueError('线上版需要明确配置capcut.api_base；不猜测或自动切换服务')
    url = urlsplit(base)
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('CapCut服务地址无效')
    if p['mode']=='local' and not loopback(url.hostname):
        raise ValueError('本地版只连接loopback地址；不会回退到线上')
    if p['mode']=='online' and loopback(url.hostname):
        raise ValueError('线上版需要远程服务地址；不会启动本机服务')
    return {**p,'api_base':base.rstrip('/'),'timeout':raw.get('timeout',240)}


class Client:
    def __init__(self, settings):
        self.settings=settings;self.base=settings['api_base']
        self.root=self.base if self.base.endswith(PREFIX) else self.base+PREFIX
    def call(self, endpoint, body):
        try:
            if endpoint=='get_draft':
                response=requests.get(self.root+'/'+endpoint,params=body,timeout=(10,self.settings['timeout']),allow_redirects=False)
            else:
                response=requests.post(self.root+'/'+endpoint,json=body,timeout=(10,self.settings['timeout']),allow_redirects=False)
            if response.is_redirect:raise ValueError('服务重定向未执行')
            response.raise_for_status();value=response.json()
        except (requests.RequestException,ValueError) as exc:
            raise ValueError('CapCut '+endpoint+'请求未确认；检查回执，禁止自动重发') from exc
        if not isinstance(value,dict) or value.get('code',0)!=0 or value.get('success',True) is False:
            raise ValueError('CapCut '+endpoint+'业务失败；检查响应回执')
        return value.get('data',value)
    def valid_draft_url(self,url):
        p=urlsplit(url);base=urlsplit(self.base)
        if (p.scheme,p.hostname,p.port)!=(base.scheme,base.hostname,base.port):
            raise ValueError('服务返回草稿URL的origin变化，保留回执，不切换目标')


def find_project(job,cfg):
    configured=cfg.get('capcut',{}).get('local_project_dir')
    candidates=[Path(configured).expanduser()] if configured else [p/'capcut-mate' for p in [Path.cwd(),*job.parents]]
    for path in candidates:
        if (path/'src/router/v1.py').is_file() and (path/'config.py').is_file():return path.resolve()
    raise ValueError('找不到本机capcut-mate；配置capcut.local_project_dir为仓库目录')


@contextlib.contextmanager
def local_api(job,cfg,settings):
    """Reuse an available service, otherwise start the installed project on loopback."""
    process=None;log=None
    p=urlsplit(settings['api_base']);origin=p.scheme+'://'+p.netloc
    try:
        response=requests.get(origin+'/openapi.json',timeout=3,allow_redirects=False)
    except requests.ConnectionError:
        if not settings.get('auto_start_local'):raise ValueError('本机服务未启动') from None
        project=find_project(job,cfg)
        python=cfg.get('capcut',{}).get('local_python')
        if not python:
            choices=[project/'.venv/bin/python',project/'.venv/Scripts/python.exe']
            python=next((str(q) for q in choices if q.is_file()),None)
        if not python:raise ValueError('capcut-mate虚拟环境缺失；先按仓库安装依赖，或配置capcut.local_python')
        folder=job/'assembly_runtime';folder.mkdir(exist_ok=True)
        log=(folder/'local_service.log').open('ab')
        args=[python,str(config.SCRIPT_DIR/'local_capcut_service.py'),'--project-dir',str(project),'--port',str(p.port or 30000)]
        if p.scheme!='http':raise ValueError('自动启动的本机服务仅支持HTTP loopback')
        process=subprocess.Popen(args,cwd=project,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:raise ValueError('本机服务启动失败，见assembly_runtime/local_service.log')
                try:
                    response=requests.get(origin+'/openapi.json',timeout=.5,allow_redirects=False);break
                except requests.ConnectionError:time.sleep(.1)
            else:raise ValueError('本机服务启动超时')
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            log.close();raise
    try:
        response.raise_for_status();paths=response.json().get('paths',{})
        required=('create_draft','add_images','add_audios','add_captions','save_draft','get_draft')
        if not all(any(k.endswith('/'+name) for k in paths) for name in required):
            raise ValueError('本机服务接口不完整；不要切换到远端补做')
        yield
    finally:
        if process is not None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        if log is not None:log.close()


@contextlib.contextmanager
def local_media(request,port=30002):
    assets={a['asset_id']:a for a in request['assets']}
    for row in assets.values():
        if file_hash(Path(row['path']))!=row['sha256']:raise ValueError('本地素材字节已变化')
    version=request['source_version'];prefix='/skill-assets/'+version+'/'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):self.send_asset(False)
        def do_HEAD(self):self.send_asset(True)
        def send_asset(self,head):
            path=urlsplit(self.path).path
            if not path.startswith(prefix):self.send_error(404);return
            row=assets.get(unquote(path[len(prefix):]))
            if row is None:self.send_error(404);return
            file=Path(row['path'])
            self.send_response(200);self.send_header('Content-Length',str(file.stat().st_size))
            self.send_header('Content-Type',mimetypes.guess_type(file.name)[0] or 'application/octet-stream');self.end_headers()
            if not head:
                import shutil
                with file.open('rb') as f:shutil.copyfileobj(f,self.wfile)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        yield {aid:{**row,'url':'http://127.0.0.1:'+str(server.server_port)+prefix+quote(aid,safe='')} for aid,row in assets.items()}
    finally:server.shutdown();server.server_close();worker.join(timeout=5)


def create_name_capability(client):
    """Send only naming fields actually declared by the configured service."""
    p=urlsplit(client.base);url=p.scheme+'://'+p.netloc+'/openapi.json'
    try:
        response=requests.get(url,timeout=(5,10),allow_redirects=False)
        if response.is_redirect:return {'status':'schema_unavailable','field':None}
        response.raise_for_status();schema=response.json();schemas=schema.get('components',{}).get('schemas',{})
        for path,methods in schema.get('paths',{}).items():
            if path.endswith('/create_draft') and 'post' in methods:
                body=methods['post'].get('requestBody',{}).get('content',{}).get('application/json',{}).get('schema',{})
                if '$ref' in body:body=schemas.get(body['$ref'].split('/')[-1],{})
                for field in ('draft_name','name'):
                    prop=body.get('properties',{}).get(field,{})
                    if prop.get('type')=='string' or any(x.get('type')=='string' for x in prop.get('anyOf',[])):
                        return {'status':'supported','field':field}
                return {'status':'unsupported','field':None}
    except (requests.RequestException,ValueError,KeyError,TypeError):pass
    return {'status':'schema_unavailable','field':None}
