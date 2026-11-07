"""Build caption request bodies offline; API calls belong to the editing Skill."""
import argparse
import copy
import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from artifacts import atomic_json, read_json

PROFILE = 'capcut_mate_full_canvas_v1'


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} 必须为有限数值')
    return value


def api_style(style, defaults):
    """Preserve desktop draft values: API itself divides raw x/y by canvas size.

    Do not use transform_reference, convert font units or infer a Y sign flip
    from conflicting prose documentation. This profile targets that implementation.
    """
    value = {**defaults, **style}
    size = number(value['font_size'], 'font_size')
    if size < 1 or int(size) != size:
        raise ValueError('font_size 必须为正整数，不能自行按像素换算')
    if value.get('border_width_ui',40)!=40 or value.get('border_alpha',1.0)!=1.0:
        raise ValueError('当前接口仅经本地实现核对默认描边40/不透明度100%，不能静默忽略其他描边参数')
    font = value.get('font')
    body = {
        'font': None if font in (None, '', '系统') else font,
        'font_size': int(size),
        'text_color': value['text_color'],
        'border_color': value.get('border_color'),
        'alignment': value.get('alignment', 1),
        'alpha': value.get('alpha', 1.0),
        'bold': value.get('bold', True),
        'scale_x': number(value.get('scale_x', 1.0), 'scale_x'),
        'scale_y': number(value.get('scale_y', 1.0), 'scale_y'),
        'transform_x': number(value['x'], 'x'),
        'transform_y': number(value['y'], 'y'),
        'letter_spacing': value.get('letter_spacing', 0),
        'line_spacing': value.get('line_spacing', 0),
        'style_text': False, 'underline': value.get('underline',False), 'italic': False,
        'has_shadow': False, 'text_effect': None,
    }
    return body


def display_cues(cues,hide_before_us=1000000):
    """Hide only the cover interval; never shift audio or any later cue."""
    output=[]
    for cue in cues:
        if cue['end_us']<=hide_before_us: continue
        output.append({**cue,'start_us':max(cue['start_us'],hide_before_us),
                       'source_start_us':cue.get('source_start_us',cue['start_us']),
                       'source_end_us':cue.get('source_end_us',cue['end_us'])})
    return output


def validate_presentation(timeline):
    import config
    policy=config.load_spec()['presentation_policy']
    cutoff=policy['text_start_us']
    roles=[x['role'] for x in timeline['texts']]
    expected={'title_line1','title_line2'}|({'album'} if timeline['branding_enabled'] else set())
    if set(roles)!=expected or len(roles)!=len(expected):
        raise ValueError('独立文字层缺失/重复：需要'+','.join(sorted(expected))+'，实际'+','.join(roles))
    if not timeline['subtitles']:raise ValueError('缺少原声字幕，不能把不完整文字交给下游')
    layers=list(timeline['texts'])+[dict(c,role='subtitle') for c in timeline['subtitles']]
    for layer in layers:
        if layer['role'] in policy['suppressed_text_roles']:raise ValueError('已关闭末尾钩子字幕及AI示意画面，不能重新添加CTA/notice图层')
        if layer['start_us']<cutoff:raise ValueError('首秒只允许封面与品牌贴片，文字从1秒开始显示')
        if layer['role'] in ('title_line1','title_line2'):
            text=layer['text']
            import layout_profile
            layout_profile.fit(text,layer['role'])
            if layer['style_reference'].get('layout_profile') != layout_profile.binding():
                raise ValueError('文字模板版本过期；重新编译交接')
    for element in timeline.get('branding_elements',[]):
        if element['start_us'] < (policy.get('source_label_start_us',cutoff) if element['role']=='source_label' else cutoff):raise ValueError('首秒不显示专辑小封面或播放动图')
        if 'segments' in element:raise ValueError('旧播放GIF循环已停用，请重新prepare/publish为单个原生贴纸')


def compile_timeline(timeline):
    """Return grouped top-level styles and already-serialized caption lists."""
    validate_presentation(timeline)
    canvas = timeline['canvas']
    for key in ('width', 'height'):
        if type(canvas[key]) is not int or canvas[key] <= 0:
            raise ValueError('画布尺寸必须为正整数')
    convention = timeline['transform_convention']
    if (convention.get('origin'), convention.get('x_positive'), convention.get('y_positive'),
            convention.get('scale')) != ('center', 'right', 'up', 'full_canvas'):
        raise ValueError('未知模板坐标约定，不能套用当前兼容映射')
    defaults = timeline['text_defaults']
    layers = list(timeline['texts']) + [
        {**cue, 'role': 'subtitle', 'style_reference': timeline['subtitle_style_reference']}
        for cue in timeline['subtitles']
    ]
    groups = {}
    for layer in layers:
        start, end, text = layer['start_us'], layer['end_us'], layer['text']
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= timeline['body_duration_us']
                or not isinstance(text, str) or not text.strip()):
            raise ValueError('文字为空或时码非法；要求正文范围内的整数微秒')
        import safe_layout
        body = api_style(safe_layout.text_style(text, layer['style_reference']), defaults)
        key = (layer['role'], json.dumps(body, sort_keys=True, ensure_ascii=False))
        if key not in groups:
            groups[key] = {'role':layer['role'],'body':body,'captions':[],
                'verification':{**{k:layer['style_reference'][k] for k in ('border_alpha','border_width_ui','font_resource_id','font_file','font_sha256','font_postscript','pixel_style','measurement','layout_profile') if k in layer['style_reference']},
                **({'single_line':True} if layer['role'] in ('title_line1','title_line2') else {})}}
        groups[key]['captions'].append({'start': start, 'end': end, 'text': text})
    requests = []
    for group in groups.values():
        cues = sorted(group['captions'], key=lambda c: c['start'])
        if any(a['end'] > b['start'] for a, b in zip(cues, cues[1:])):
            raise ValueError('同一文字组内时码重叠')
        import request_chunks
        for body in request_chunks.partition(cues, 'captions', group['body']):
            requests.append({'endpoint':'add_captions','role':group['role'],'body':body,'verification':group['verification']})
    return {
        'schema': 'knowledge-caption-api/v1', 'profile': PROFILE,
        'create_draft_body': {'width': canvas['width'], 'height': canvas['height']},
        'draft_url_binding': 'latest_successful_response_verbatim',
        'requests': requests,
    }


def bind_request(timeline, index, draft_url):
    """Add the latest real response URL, without rewriting or prebinding all steps."""
    parsed = urlsplit(draft_url)
    if (parsed.scheme not in ('http', 'https') or not parsed.netloc
            or not parse_qs(parsed.query).get('draft_id')):
        raise ValueError('draft_url 必须使用接口返回的完整草稿URL')
    compiled = compile_timeline(timeline)
    if timeline.get('caption_api') != compiled:
        raise ValueError('文字请求与当前时间线不符，请重新prepare/publish')
    if type(index) is not int or not 0 <= index < len(compiled['requests']):
        raise ValueError('request-index 越界（从0开始）')
    return {**copy.deepcopy(compiled['requests'][index]['body']), 'draft_url': draft_url}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir', required=True, type=Path)
    parser.add_argument('--orientation', required=True, choices=['portrait'])
    parser.add_argument('--request-index', required=True, type=int)
    parser.add_argument('--draft-url', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        import handoff
        job = args.job_dir.resolve()
        value = handoff.validate(job, read_json(job/'config.json'))
        timeline = next(t for t in value['timelines'] if t['orientation'] == args.orientation)
        body = bind_request(timeline, args.request_index, args.draft_url)
        atomic_json(args.output, body)
        print(json.dumps({'endpoint': 'add_captions', 'request_file': str(args.output)}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as exc:
        print('文字请求未生成：' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
