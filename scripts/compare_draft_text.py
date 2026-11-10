"""Compare one editable draft before and after transfer, when a colleague reports drift."""
import argparse
import json
from pathlib import Path

from artifacts import file_hash, read_json


def snapshot(folder):
    folder = folder.resolve()
    data = read_json(folder/'draft_content.json')
    if not data:
        raise ValueError('请指定包含 draft_content.json 的工程目录')
    fonts = {}
    recorded = read_json(folder/'knowledge-layout.json', {}).get('fonts', {})

    def font_id(raw):
        if not raw:
            return None
        if raw not in fonts:
            path = Path(raw)
            if not path.is_absolute():
                path = folder/path
            found = next((sha for sha,name in recorded.items() if name==raw), None)
            fonts[raw] = {'actual_sha256':file_hash(path) if path.is_file() else 'missing',
                          'declared_sha256':found}
        return fonts[raw]

    material = {row['id']:row for row in data.get('materials', {}).get('texts', [])}
    rows = {}
    for track in data['tracks']:
        if track.get('type')!='text':
            continue
        for seg in track.get('segments', []):
            text = material[seg['material_id']]
            content = json.loads(text['content'])
            styles = []
            for run in content.get('styles', []):
                styles.append({**run,'font':{**run.get('font', {}),
                    'path':font_id((run.get('font') or {}).get('path'))}})
            style = {key:text.get(key) for key in ('font_name','font_size','alignment','line_max_width',
                                                    'letter_spacing','line_spacing','bold_width')}
            rows[seg['id']] = {**style, 'text':content.get('text'), 'font':font_id(text.get('font_path')),
                               'styles':styles, 'clip':seg.get('clip'), 'timerange':seg.get('target_timerange')}
    return {'canvas':data.get('canvas_config'), 'text_segments':rows}


def compare(original, receiver):
    before, after = snapshot(original), snapshot(receiver)
    a,b = before['text_segments'],after['text_segments']
    missing, added = a.keys()-b.keys(),b.keys()-a.keys()
    differences = []
    for sid in a.keys() & b.keys():
        changed = [key for key in a[sid] if a[sid][key]!=b[sid][key]]
        if changed:
            differences.append({'time_seconds':a[sid]['timerange']['start']/1e6,
                                'text':a[sid]['text'],'changed_fields':changed,'segment':sid})
    differences.sort(key=lambda diff:(diff['time_seconds'],diff['segment']))
    return {'same':before['canvas']==after['canvas'] and not (missing or added or differences),
            'canvas_equal':before['canvas']==after['canvas'], 'before':len(a),'after':len(b),
            'missing':len(missing),'added':len(added),'changed':len(differences),'examples':differences[:5]}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('original', type=Path)
    p.add_argument('receiver', type=Path)
    args=p.parse_args()
    try:
        result=compare(args.original,args.receiver)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        raise SystemExit(str(exc))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['same'] else 1)
