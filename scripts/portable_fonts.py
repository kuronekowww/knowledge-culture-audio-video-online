"""Relocate the exact fonts recorded by a knowledge-video draft."""
import json
from pathlib import Path, PureWindowsPath

from artifacts import file_hash


class FontBindingError(ValueError):
    pass


def basename(raw):
    return PureWindowsPath(raw).name


def private_roles():
    from team_fonts import ROOT
    profile = json.loads((ROOT/'layout-profile.json').read_text(encoding='utf-8'))
    return {font['sha256']: role for role, font in profile['font_files'].items()
            if font.get('private_local_font')}


def font_map(layout, folder):
    """Map producer font paths to receiver paths, checking each binary only once."""
    folder = Path(folder).resolve()
    local_fonts = folder / 'assets' / 'fonts'
    replacements = {}
    roles = private_roles()
    local = {}
    if set(layout['fonts']).intersection(roles):
        from team_fonts import resolve_all
        try:
            local = resolve_all()
        except ValueError as exc:
            raise FontBindingError(str(exc)) from exc
    for sha, old in layout['fonts'].items():
        if sha in roles:
            replacements[old] = str(local[roles[sha]])
            continue
        filename = basename(old)
        target = (local_fonts / filename).resolve()
        if not filename or not target.is_relative_to(local_fonts):
            raise FontBindingError('草稿字体清单包含无效路径')
        if not target.is_file():
            raise FontBindingError(f'接收机缺少原工程字体 {filename}')
        if file_hash(target) != sha:
            raise FontBindingError(f'字体版本与原工程不符：{filename}')
        replacements[old] = str(target)
    return replacements


def update_text_fonts(data, replacements, check_only=False):
    """Rewrite both fields, including every rich-text run inside serialized content."""
    known_targets = set(replacements.values())

    def resolved(raw):
        if check_only:
            if raw not in known_targets:
                raise FontBindingError('文字仍指向其他位置的字体：' + str(raw))
            return raw
        if raw in replacements:
            return replacements[raw]
        if raw in known_targets:
            return raw
        raise FontBindingError('文字使用了原工程清单外的字体：' + str(raw))

    active = {
        segment.get('material_id')
        for track in data.get('tracks', []) if track.get('type') == 'text'
        for segment in track.get('segments', [])
    }
    count = 0
    for material in data.get('materials', {}).get('texts', []):
        # Some editor templates contain unused catalogue-only text materials.
        if not material.get('font_path'):
            if material.get('id') in active:
                raise FontBindingError('活动文字缺少材料字体绑定')
            continue
        expected = resolved(material['font_path'])
        content = json.loads(material['content'])
        if not content.get('styles'):
            raise FontBindingError('绑定字体的文字缺少样式')
        for part in content['styles']:
            font = part.get('font', {})
            path = resolved(font.get('path'))
            if path != expected:
                raise FontBindingError('材料与样式的字体绑定不同')
            if not check_only:
                font['path'] = path
        if not check_only:
            material['font_path'] = expected
            material['content'] = json.dumps(content, ensure_ascii=False)
        count += 1
    return count
