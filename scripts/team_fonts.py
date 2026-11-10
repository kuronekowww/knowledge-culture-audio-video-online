"""Locate the exact original team fonts on this computer without distributing them."""
import argparse
import json
import os
from pathlib import Path

from artifacts import atomic_json, file_hash, read_json

ROOT = Path(__file__).resolve().parents[1]
ENV_KEYS = {'display': 'KNOWLEDGE_DISPLAY_FONT', 'subtitle': 'KNOWLEDGE_SUBTITLE_FONT'}
NAMES = {'display': ('江西拙楷.ttf', 'jiangxizhuokai.ttf'), 'subtitle': ('PingFangUI.ttc',)}
LABELS = {'display': '江西拙楷 Regular', 'subtitle': '苹方简 Medium'}


def config_path():
    return Path(os.environ.get('KNOWLEDGE_FONT_SETTINGS', '~/.knowledge-video/fonts.json')).expanduser()


def candidates(role):
    if role == 'subtitle':
        yield Path('/System/Library/PrivateFrameworks/FontServices.framework/Resources/Reserved/PingFangUI.ttc')
    directories = [Path.home()/'Library/Fonts', Path('/Library/Fonts'), Path('/System/Library/Fonts'),
                   Path.home()/'.local/share/fonts', Path.home()/'.fonts']
    if os.name == 'nt':
        for env, subfolder in [('WINDIR','Fonts'), ('LOCALAPPDATA','Microsoft/Windows/Fonts')]:
            if os.environ.get(env):
                directories.append(Path(os.environ[env])/subfolder)
    for directory in directories:
        for filename in NAMES[role]:
            yield directory/filename


def resolve_all(overrides=None):
    profile = read_json(ROOT/'layout-profile.json')['font_files']
    settings = read_json(config_path(), {})
    output = {}
    errors = []
    for role, descriptor in profile.items():
        override = (overrides or {}).get(role) or os.environ.get(ENV_KEYS[role]) or settings.get(role)
        options = [Path(override).expanduser()] if override else candidates(role)
        for candidate in options:
            if candidate.is_file() and file_hash(candidate) == descriptor['sha256']:
                output[role] = candidate.resolve()
                break
        if role not in output:
            detail = '缺少或版本不符'
            errors.append(f"{LABELS[role]}（{detail}，SHA256 {descriptor['sha256'][:12]}…，--{role} 文件路径）")
    if errors:
        raise ValueError('请一次补齐本机原版字体：' + '；'.join(errors) +
                         '。在字体册安装原版后运行 python scripts/team_fonts.py；自定义路径可在该命令附上述参数保存。')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--display', type=Path, help='本机字体册已安装的原版江西拙楷 TTF')
    parser.add_argument('--subtitle', type=Path, help='本机原版 PingFangUI.ttc')
    parser.add_argument('--json', action='store_true', help='输出检查结果')
    args = parser.parse_args()
    explicit = {k: v.resolve() for k,v in vars(args).items() if k in ENV_KEYS and v}
    try:
        resolved = resolve_all(explicit)
        if explicit:
            current = read_json(config_path(), {})
            atomic_json(config_path(), {**current, **{k: str(v) for k,v in explicit.items()}})
        value = {'status':'ready', 'fonts':{k:str(v) for k,v in resolved.items()}}
    except (OSError, ValueError) as exc:
        value = {'status':'missing_fonts', 'message':str(exc)}
    print(json.dumps(value, ensure_ascii=False))
    return int(value['status'] != 'ready')


if __name__ == '__main__':
    raise SystemExit(main())
