"""Read skill-local credential values without shell execution or secret output."""
import os
from pathlib import Path

CREDENTIAL_KEYS = frozenset(('GPT_IMAGE_2_TOKEN', 'DASHSCOPE_API_KEY', 'XIMALAYA_XQL_SIGNATURE'))


def load_skill_env(path=None, environ=None):
    path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / '.env'
    target = os.environ if environ is None else environ
    if not path.is_file():
        return ()
    values = []
    for number, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        key, sep, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not sep:
            raise ValueError(f'.env第{number}行格式无效，应为KEY=VALUE')
        if key not in CREDENTIAL_KEYS:
            continue
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'.env第{number}行引号未闭合')
            value = value[1:-1]
        if value:
            values.append((key, value))
    loaded = []
    for key, value in values:
        if key not in target:
            target[key] = value
            loaded.append(key)
    return tuple(loaded)
