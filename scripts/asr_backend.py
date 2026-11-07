"""Backend selection and shared provenance for recognition and review caches."""
import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import config
from artifacts import digest, file_hash


def backend(cfg):
    value = cfg.get('asr', {}).get('backend', config.ASR_BACKEND)
    if value not in {'funasr', 'whisper', 'bailian_paraformer'}:
        raise ValueError('asr.backend必须为bailian_paraformer/funasr/whisper')
    return value


def model_path(cfg):
    default = config.FUNASR_MODEL if backend(cfg) == 'funasr' else config.WHISPER_MODEL
    return Path(cfg.get('asr', {}).get('model') or default).expanduser()


@lru_cache(maxsize=256)
def _content_hash(path, stat_key):
    return file_hash(Path(path))


def content_hash(path):
    path = Path(path).resolve()
    st = path.stat()
    return _content_hash(str(path), (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns))


def model_hash(path, kind):
    if kind == 'whisper':
        return content_hash(path) if path.is_file() else None
    if not path.is_dir() or not (path / 'model.pt').is_file():
        return None
    return digest({str(p.relative_to(path)): content_hash(p) for p in sorted(path.rglob('*'))
                   if p.is_file() and not any(part.startswith('.') for part in p.relative_to(path).parts)})


def funasr_python(cfg):
    value = cfg.get('asr', {}).get('python') or config.FUNASR_PYTHON
    found = shutil.which(str(Path(value).expanduser())) if value else None
    if not found:
        raise ValueError('缺少FUNASR_PYTHON或asr.python；应指向已安装FunASR的Python环境')
    # Keep the venv path: resolving this symlink would select the base interpreter.
    return str(Path(found).absolute())


def runtime_versions(python):
    script = ('import importlib.metadata as m,json; '
              'print(json.dumps({n:m.version(n) for n in ["funasr","torch","torchaudio","modelscope"]}))')
    r = subprocess.run([python, '-c', script], capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise ValueError('FunASR环境依赖不完整，请在FUNASR_PYTHON对应环境检查funasr/torch/torchaudio/modelscope')
    return json.loads(r.stdout)


def identity(cfg):
    kind = backend(cfg)
    if kind == 'bailian_paraformer':
        import bailian_asr
        return bailian_asr.identity(cfg)
    path = model_path(cfg)
    result = {'backend': kind, 'model_path': str(path.resolve()), 'model_sha256': model_hash(path, kind)}
    if kind == 'funasr':
        python = funasr_python(cfg)
        result.update(python=python, packages=runtime_versions(python),
                      adapter_sha256=content_hash(config.SCRIPT_DIR / 'transcribe_funasr.py'))
    else:
        cli = shutil.which(config.WHISPER_CLI)
        result.update(cli=cli or config.WHISPER_CLI,
                      cli_sha256=content_hash(cli) if cli else None,
                      adapter_sha256=content_hash(config.SCRIPT_DIR / 'transcribe.py'))
    return result


def prompt(cfg, dictionary):
    if backend(cfg) == 'bailian_paraformer':
        import bailian_hotwords
        return bailian_hotwords.prompt(cfg)
    return (' ' if backend(cfg) == 'funasr' else '，').join(e['canonical'] for e in dictionary['entries'])
