"""Package a named, credential-free team release with a verified file inventory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

from artifacts import atomic_json, file_hash, read_json

ROOT = Path(__file__).resolve().parent.parent
NAME = 'knowledge-culture-audio-video-online'
TOP = {'SKILL.md','.gitignore','.env.example','requirements.txt','requirements-mp4.txt','runtime_profile.json','spec.json','layout-profile.json'}
DIRECTORIES = {'agents','assets','references','scripts','tests','vendor'}
FONT_TYPES = {'.otf','.ttf','.ttc','.woff','.woff2','.otc'}
SKIP = {'node_modules','__pycache__','.venv','knowledge-jobs','knowledge-shared'}
ENV_TEMPLATE = b'GPT_IMAGE_2_TOKEN=\nDASHSCOPE_API_KEY=\nXIMALAYA_XQL_SIGNATURE=\n'


def release_files(root):
    for path in sorted(root.rglob('*')):
        rel=path.relative_to(root)
        if rel.as_posix() in TOP:
            if path.is_symlink() or not path.is_file(): raise ValueError('发布文件不是普通文件：'+str(rel))
            yield rel,path
        elif rel.parts[0] in DIRECTORIES:
            if any(part in SKIP or part.startswith('.') for part in rel.parts): continue
            if path.is_symlink(): raise ValueError('发布资源含外部链接：'+str(rel))
            if path.is_file() and path.suffix.lower() not in FONT_TYPES | {'.pyc','.pyo','.log','.zip','.pem','.key','.p12'}:
                yield rel,path


def package(output, version):
    output=output.expanduser().resolve()
    if ROOT in output.parents: raise ValueError('ZIP应保存在技能目录以外')
    if (ROOT/'.env.example').read_bytes()!=ENV_TEMPLATE: raise ValueError('凭据示例必须只含空值')
    files=list(release_files(ROOT))
    present={rel.as_posix() for rel,_ in files}
    if missing:=TOP-present: raise ValueError('发布输入缺失：'+', '.join(sorted(missing)))
    layout=read_json(ROOT/'layout-profile.json')
    if any(font.get('embedding') != 'local' for font in layout['font_files'].values()):
        raise ValueError('团队包只分发原字体的本地绑定，请检查布局配置')
    manifest={'skill':NAME,'version':version,'spec_version':read_json(ROOT/'spec.json')['version'],
              'deployment_variant':read_json(ROOT/'runtime_profile.json')['mode'],
              'layout_profile':{'id':layout['id'],'sha256':file_hash(ROOT/'layout-profile.json')},
              'authoritative_configuration':'layout-profile.json','mutable_user_files':['.env'],
              'files':[{'path':rel.as_posix(),'sha256':file_hash(path),'bytes':path.stat().st_size} for rel,path in files]}
    atomic_json(ROOT/'release_manifest.json',manifest)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        pending=Path(temp)/'release.zip'
        with zipfile.ZipFile(pending,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for rel,path in [*files,(Path('release_manifest.json'),ROOT/'release_manifest.json')]:
                archive.write(path,NAME+'/'+rel.as_posix())
        with zipfile.ZipFile(pending) as archive:
            expected={NAME+'/'+r['path']:r['sha256'] for r in manifest['files']}
            expected[NAME+'/release_manifest.json']=file_hash(ROOT/'release_manifest.json')
            if set(archive.namelist())!=expected.keys() or len(archive.namelist())!=len(expected):
                raise ValueError('压缩包条目与清单不一致')
            for member,sha in expected.items():
                if hashlib.sha256(archive.read(member)).hexdigest()!=sha:
                    raise ValueError('压缩包校验失败：'+member)
        os.replace(pending,output)
    return {'zip':str(output),'bytes':output.stat().st_size,'file_count':len(expected),'sha256':file_hash(output)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--version',required=True)
    args=parser.parse_args()
    print(json.dumps(package(args.output,args.version),ensure_ascii=False))
