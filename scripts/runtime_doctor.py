"""Check the chosen production route once per machine; optionally install missing Python packages."""
import argparse
import json
import os
import re
import platform
import tempfile
from pathlib import Path
import shutil
import subprocess
import sys
from artifacts import atomic_json, read_json, file_lock
import team_fonts

HYPERFRAMES_VERSION = '0.9.19'


def default_record(cfg=None):
    root=(cfg or {}).get('shared_root') or os.environ.get('KNOWLEDGE_SHARED_DIR') or Path(os.environ.get('KNOWLEDGE_JOBS_DIR',Path.cwd()/'knowledge-jobs'))/'_shared'
    return Path(root).expanduser().resolve()/'runtime-environment.json'


def command(argv, *, contains=None):
    executable = shutil.which(str(argv[0]))
    if not executable: raise ValueError('命令不可执行：'+str(argv[0]))
    result = subprocess.run([executable, *map(str, argv[1:])], capture_output=True, text=True, timeout=30)
    output = (result.stdout or result.stderr).strip()
    if result.returncode or not output or (contains and contains not in output):
        raise ValueError('实际执行失败或版本不符：'+str(argv[0])+' '+output[:300])
    return {'executable':str(Path(executable).resolve()), 'version':output.splitlines()[0]}


ROOT = Path(__file__).resolve().parent.parent
PROBE_VERSION = 3
VERSION_RANGES = {'requests': ((2,31),(3,0)), 'PIL': ((10,0),(13,0)), 'numpy': ((1,26),(3,0))}


def python_probe(executable, mp4=False):
    modules = ['requests', 'PIL'] + (['numpy'] if mp4 else [])
    code = """import importlib, importlib.util, json, sys
mods = {n: importlib.import_module(n) for n in sys.argv[1:]}
print(json.dumps({'executable':sys.executable,'version':list(sys.version_info[:3]),
    'dependencies':{n:m.__version__ for n,m in mods.items()},
    'module_files':[m.__file__ for m in mods.values()],
    'pip_available':importlib.util.find_spec('pip') is not None}))"""
    result = subprocess.run([executable, '-c', code, *modules], capture_output=True, text=True, timeout=30)
    if result.returncode: raise ValueError('Python依赖导入失败：'+result.stderr[-400:])
    value = json.loads(result.stdout)
    if value['version'] < [3,12,0]: raise ValueError('需要Python 3.12+')
    for name in modules:
        lo, hi = VERSION_RANGES[name]
        version = tuple(map(int, re.findall(r'\d+',value['dependencies'][name])[:2]))
        if not lo <= version < hi: raise ValueError(name+'版本不兼容：'+value['dependencies'][name])
    return value


def venv_python(folder):
    return Path(folder) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def discovered(saved, output, python):
    if python or os.environ.get('KNOWLEDGE_PYTHON'):
        chosen = str(python or os.environ['KNOWLEDGE_PYTHON'])
        return [os.path.abspath(shutil.which(chosen) or chosen)]
    candidates = [(saved.get('python') or {}).get('executable')]
    for folder in (output.parent/'python-runtime', os.environ.get('VIRTUAL_ENV'), Path.cwd()/'.venv', Path.cwd()/'capcut-mate/.venv'):
        if folder and venv_python(folder).is_file(): candidates.append(str(venv_python(folder)))
    return list(dict.fromkeys(x for x in (*candidates,sys.executable,shutil.which('python3.12')) if x))


def stamps(paths):
    try:
        result = {}
        for path in paths:
            stat = Path(path).stat()
            result[str(path)] = [stat.st_size,stat.st_mtime_ns]
        return result
    except (OSError,TypeError):
        return None


def inspect(output, python=None, runtime_dir=None, web=False, browser=None, *, mp4=False, refresh=False):
    output = Path(output).expanduser().resolve()
    with file_lock(output.with_suffix('.probe.lock')):
        return _inspect(output,python,runtime_dir,web,browser,mp4,refresh)


def _inspect(output, python, runtime_dir, web, browser, mp4, refresh):
    saved = read_json(output, {}) or {}
    if saved.get('probe_key',{}).get('host',platform.node()) != platform.node():
        saved = {}
    candidates = discovered(saved,output,python)
    explicit_browser = browser or os.environ.get('KNOWLEDGE_BROWSER')
    root = Path(runtime_dir or saved.get('runtime_dir') or output.parent/'web-runtimes'/'hyperframes-0.9.19-gsap-3.15.1').resolve() if web else None
    probe_key = {'probe':PROBE_VERSION,'host':platform.node(),'platform':platform.platform(),'python':candidates[0],
           'web':web,'mp4':mp4,'runtime_dir':str(root) if web else None,'browser':str(explicit_browser) if web and explicit_browser else None,
           'binaries':{name:shutil.which(name) for name in (('ffmpeg','ffprobe','node') if web else ('ffmpeg','ffprobe'))},
           'python_env':{key:os.environ.get(key) for key in ('PYTHONHOME','PYTHONPATH','PYTHONNOUSERSITE')}}
    font_settings = team_fonts.config_path()
    probe_key['font_settings'] = {str(font_settings):stamps([font_settings])}
    probe_key['font_overrides'] = {name:os.environ.get(name) for name in ('KNOWLEDGE_DISPLAY_FONT','KNOWLEDGE_SUBTITLE_FONT')}
    watched = saved.get('watched_files')
    if not refresh and saved.get('ok') and saved.get('probe_key') == probe_key and watched and stamps(watched) == watched:
        return {**saved,'cache_hit':True,'install_performed':False}
    attempts = []; chosen = None
    for candidate in dict.fromkeys(c for c in candidates if c):
        try:
            chosen = python_probe(candidate, mp4); break
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            attempts.append({'executable':candidate, 'error':str(exc)})
    result = {'schema':'knowledge-runtime/v2', 'python':chosen, 'attempts':attempts,
              'checks':{}, 'errors':[], 'warnings':[], 'install_performed':False}
    try:
        result['checks']['fonts'] = {role:str(path) for role,path in team_fonts.resolve_all().items()}
    except (OSError, ValueError) as exc:
        result['errors'].append(str(exc))
    if not chosen: result['errors'].append('没有通过实际导入的Python；用--python指定既有环境，或在工作区建立专用环境')
    if chosen and not chosen['pip_available']: result['warnings'].append('当前Python无pip；依赖导入已通过，可运行；不能假定可安装新包')
    for name in ('ffmpeg','ffprobe'):
        try: result['checks'][name] = command([name, '-version'])
        except (OSError, ValueError, subprocess.SubprocessError) as exc: result['errors'].append(str(exc))
    if web:
        try:
            result['checks']['node'] = command(['node', '--version'])
            if tuple(map(int, re.findall(r'\d+',result['checks']['node']['version'])[:2])) < (22,12):
                raise ValueError('此网页运行库需要Node 22.12+')

            cli = root/'node_modules/hyperframes/bin/hyperframes.mjs'
            gsap = root/'node_modules/gsap/dist/gsap.min.js'
            if not cli.is_file() or not gsap.is_file(): raise ValueError('运行目录缺真实Hyperframes/GSAP文件；不能仅依据.bin链接判定已安装')
            version = command(['node', str(cli), '--version'])['version']
            if version != HYPERFRAMES_VERSION: raise ValueError(f'生产模板锁定Hyperframes {HYPERFRAMES_VERSION}，实际{version}；新版本需重新验证')
            gsap_version=read_json(root/'node_modules/gsap/package.json',{}).get('version')
            if gsap_version!='3.15.1':raise ValueError('模板需要已验证的GSAP 3.15.1')
            for lib in ('hyperframes-player.global.js','hyperframe.runtime.iife.js'):
                if not (root/'node_modules/hyperframes/dist'/lib).is_file():raise ValueError('未找到本地播放器资源：'+lib)
            # Module resolution must succeed in the selected runtime, not just in PATH.
            puppeteer = command(['node','-e',"console.log(require.resolve('puppeteer-core',{paths:[process.argv[1]]}))",str(root)])['version']
            explicit=browser or os.environ.get('KNOWLEDGE_BROWSER')
            recorded=(saved.get('checks',{}).get('browser') or {}).get('executable')
            browsers=[explicit] if explicit else [recorded, shutil.which('chromium'),shutil.which('google-chrome'),
                '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                *sorted((Path.home()/'.cache/hyperframes/chrome').glob('chrome-headless-shell/*/*/chrome-headless-shell'),reverse=True)]
            result['checks']['browser']=None
            for candidate in dict.fromkeys(x for x in browsers if x):
                try:
                    actual=command([str(candidate),'--version'])
                    if not any(name in actual['version'].lower() for name in ('chrome','chromium')):raise ValueError('不是Chrome/Chromium')
                    result['checks']['browser']=actual;break
                except (OSError,ValueError,subprocess.SubprocessError) as exc:
                    if explicit:raise ValueError('指定浏览器不能实际执行：'+str(exc))
            if not result['checks']['browser']:raise ValueError('未找到实际可执行的Chrome/Chromium；用--browser指定本机路径')
            result.update(runtime_dir=str(root), hyperframes_cli=str(cli), hyperframes_version=version,
                          gsap_file=str(gsap), gsap_version=gsap_version)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc: result['errors'].append(str(exc))
    if not web and saved.get('runtime_dir'):
        # An image-only preflight must not erase the saved web installation.
        for key in ('runtime_dir','hyperframes_cli','hyperframes_version','gsap_file','gsap_version'):
            if key in saved:result[key]=saved[key]
        for key in ('browser','node'):
            if (saved.get('checks') or {}).get(key):result['checks'][key]=saved['checks'][key]
        result['web_probe']='saved_paths_only; revalidated before web preparation'
    result['ok'] = not result['errors']
    result.update(cache_hit=False,probe_key=probe_key)
    if result['ok']:
        if not python and not os.environ.get('KNOWLEDGE_PYTHON'):
            probe_key['python'] = chosen['executable']
        paths = [Path(__file__), ROOT/'requirements.txt', chosen['executable'], *chosen['module_files']]
        paths += [ROOT/'layout-profile.json', ROOT/'scripts/team_fonts.py', *result['checks']['fonts'].values()]
        if mp4: paths.append(ROOT/'requirements-mp4.txt')
        paths += [result['checks'][name]['executable'] for name in probe_key['binaries']]
        if web:
            paths += [result['checks']['browser']['executable'],cli,gsap,puppeteer,
                      root/'node_modules/gsap/package.json',root/'node_modules/hyperframes/package.json']
            paths += [root/'node_modules/hyperframes/dist'/lib for lib in ('hyperframes-player.global.js','hyperframe.runtime.iife.js')]
        result['watched_files'] = stamps(paths)
    result['note'] = '同机、同模式、工具和依赖未变时复用检查；环境修复可用--refresh重测。'
    atomic_json(output, result)
    return result


def install_missing(output, python, mp4):
    """Populate one shared venv, without changing an existing external interpreter."""
    folder = output.parent/'python-runtime'
    target = venv_python(folder)
    with file_lock(output.parent/'python-runtime.install.lock'):
        try:
            python_probe(str(target), mp4)
            return str(target)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        if not target.is_file():
            base = python or os.environ.get('KNOWLEDGE_PYTHON') or shutil.which('python3.12') or sys.executable
            subprocess.run([base,'-c',"import sys; sys.exit(0 if sys.version_info >= (3,12) else '需要Python 3.12+')"],check=True)
            subprocess.run([base,'-m','venv',str(folder)],check=True)
        requirements = ROOT/('requirements-mp4.txt' if mp4 else 'requirements.txt')
        subprocess.run([str(target),'-m','pip','install','--disable-pip-version-check','-r',str(requirements)],check=True)
    return str(target)


def install_web_runtime(parent):
    # Only create our named shared runtime. An operator-supplied install is never replaced.
    folder = parent/'web-runtimes'/'hyperframes-0.9.19-gsap-3.15.1'
    with file_lock(parent/'web-runtime.install.lock'):
        if folder.is_dir():
            return folder
        npm = shutil.which('npm')
        if not npm: raise ValueError('网页环境需要实际可运行的 npm')
        folder.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder.parent) as tmp:
            target=Path(tmp)/'runtime'
            shutil.copytree(ROOT/'assets/web-runtime',target)
            subprocess.run([npm,'ci','--no-audit','--no-fund','--prefer-offline'],cwd=target,check=True)
            os.replace(target,folder)
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=default_record())
    parser.add_argument('--python')
    parser.add_argument('--runtime-dir',type=Path)
    parser.add_argument('--web',action='store_true')
    parser.add_argument('--browser',type=Path)
    parser.add_argument('--mp4',action='store_true',help='额外检查成片音频对齐依赖')
    parser.add_argument('--refresh',action='store_true',help='忽略当前主机与模式的探测缓存')
    parser.add_argument('--install-missing',action='store_true',help='只在共享目录补齐缺失Python库，网页首次可按锁文件配置运行库')
    args = parser.parse_args()
    result = inspect(args.output, args.python, args.runtime_dir, args.web,args.browser,mp4=args.mp4,refresh=args.refresh)
    if not result['python'] and args.install_missing:
        managed = install_missing(args.output.expanduser().resolve(),args.python,args.mp4)
        result = inspect(args.output, managed, args.runtime_dir, args.web,args.browser,mp4=args.mp4,refresh=True)
        result['install_performed'] = True
    if args.web and args.install_missing and result['python'] and result.get('checks',{}).get('node') and not args.runtime_dir:
        folder=args.output.expanduser().resolve().parent/'web-runtimes'/'hyperframes-0.9.19-gsap-3.15.1'
        if not folder.exists() and any('运行目录缺' in err for err in result['errors']):
            install_web_runtime(folder.parent.parent)
            result=inspect(args.output,result['python']['executable'],folder,True,args.browser,mp4=args.mp4,refresh=True)
            result['install_performed']=True
    print(json.dumps({k:v for k,v in result.items() if k not in ('watched_files','probe_key')}, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit('环境配置未完成：'+str(exc)) from None
