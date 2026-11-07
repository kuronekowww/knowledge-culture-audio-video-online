"""An independent, resumable proposal branch; never publishes images or approvals."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess

import subtitle_review
import timing
from artifacts import atomic_json, digest, file_hash, read_json


def request_for(job, cfg, subtitles):
    root = Path(__file__).resolve().parents[1]
    import presentation
    mode = presentation.selected(cfg)['mode']
    web = mode == 'hyperframes'
    visual = read_json(job / 'visual_bible.json', {})
    payload = {'schema': 1, 'subtitle_request_hash': subtitles['request_hash'],
               'audio_path': subtitles['audio_path'], 'context': subtitles['context'],
               'units': subtitles['units'], 'visual_bible': visual,
               'cut_plan': read_json(job / 'cut_plan.json', {}),
               'cut_result': read_json(job / 'cut_result.json', {}),
               'identity': read_json(job / 'highlight_identity.json', {}),
               'audio_sha256': subtitles['basis']['audio_sha256'],
               'duration_us': subtitle_review.duration_us(subtitle_review.audio_path(job)),
               'contract': (root / 'references' / ('web-animation.md' if web else 'storyboard-prompts.md')).read_text(encoding='utf-8'),
               'presentation_mode': mode,
               'branch_version': file_hash(Path(__file__)),
               'command': cfg.get('web_storyboard_prepare_command' if web else 'storyboard_prepare_command'),
               'timeout': cfg.get('storyboard_prepare_timeout', 300)}
    if web:
        payload['scene_budget']={'basis':'semantic_units','per_10_minutes':[24,40],'images':'only_needed_inserts','opening':'image_led_motion_then_reusable_templates'}
    else:
        import image_pacing
        payload['image_pacing']=image_pacing.recommendation(payload['duration_us']/1e6)
    payload['request_hash'] = digest(payload)
    return payload


def prepare(job, cfg, subtitles):
    request = request_for(job, cfg, subtitles)
    atomic_json(job / 'storyboard_prepare_request.json', request)
    atomic_json(job / 'storyboard_preparation' / request['request_hash'] / 'request.json', request)
    return request


def validate_response(request, response):
    if not isinstance(response, dict):
        raise ValueError('分镜预案响应必须为对象')
    if response.get('request_hash') != request['request_hash']:
        raise ValueError('分镜预案不属于当前输入版本')
    if not isinstance(response.get('author'), str) or not response['author'].strip():
        raise ValueError('分镜预案缺少实际执行者')
    shots = response.get('shots')
    if not isinstance(shots, list) or not shots:
        raise ValueError('分镜预案需要非空shots')
    units = {u['id'] for u in request['units']}
    identifiers = set()
    for shot in shots:
        if not isinstance(shot, dict): raise ValueError('分镜预案镜头必须为对象')
        key = shot.get('id')
        if not isinstance(key, str) or not key.strip() or key in identifiers:
            raise ValueError('分镜预案镜头ID为空或重复')
        identifiers.add(key)
        if not isinstance(shot.get('description'), str) or not shot['description'].strip():
            raise ValueError('分镜预案缺少画面描述')
        refs = shot.get('source_unit_ids')
        if not isinstance(refs, list) or not refs or any(not isinstance(u, str) or u not in units for u in refs):
            raise ValueError('分镜预案必须引用当前真实ASR unit，不得虚构剧情依据')


def run(job, cfg, request, retry=False):
    """Writes only preparation artifacts. Existing valid proposals are reusable."""
    with timing.bind(job), timing.span('storyboard_prepare') as measured:
        status_path = job / 'storyboard_prepare_status.json'
        response_path = job / 'storyboard_prepare_response.json'
        result = {'request_hash': request['request_hash'], 'status': 'awaiting_executor'}
        try:
            try:
                saved = read_json(status_path, {})
                response = read_json(response_path, {})
                if response.get('request_hash') != request['request_hash']:
                    response = read_json(job / 'storyboard_preparation' / request['request_hash'] / 'response.json', {})
                if not isinstance(saved, dict) or not isinstance(response, dict):
                    raise ValueError('预案状态和响应必须为对象')
            except (ValueError, TypeError, AttributeError):
                if not retry: raise ValueError('预案状态或响应损坏；检查后显式重试')
                saved, response = {}, {}
            if response.get('request_hash') == request['request_hash']:
                try:
                    validate_response(request, response)
                except (ValueError, KeyError, TypeError):
                    if not retry: raise
                    response = {}
            if response.get('request_hash') != request['request_hash']:
                if not retry and saved.get('request_hash') == request['request_hash'] and saved.get('status') in {'failed', 'running'}:
                    # A terminated process may have invoked a paid text backend. No blind replay.
                    result.update(status='failed', error='上次预案失败或中断；检查输出后用storyboard-prepare --retry显式重试')
                    measured['status'] = 'error'
                    atomic_json(status_path, result)
                    return result
                import presentation
                command = cfg.get('web_storyboard_prepare_command' if presentation.selected(cfg)['mode']=='hyperframes' else 'storyboard_prepare_command')
                if not command:
                    measured['status'] = 'awaiting_executor'
                    atomic_json(status_path, result)
                    return result
                if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
                    raise ValueError('storyboard_prepare_command必须是命令参数数组')
                atomic_json(status_path, {**result, 'status': 'running'})
                folder = job / 'storyboard_preparation' / request['request_hash']
                # Keep output isolated, including on failure. Never overwrite final storyboard/copy.
                output = folder / 'response.json'
                output.unlink(missing_ok=True)
                with timing.span('storyboard_prepare_model'):
                    proc = subprocess.run(command + ['--request', str(folder / 'request.json'), '--output', str(output)],
                                          capture_output=True, text=True, check=False,
                                          timeout=cfg.get('storyboard_prepare_timeout', 300))
                    if proc.returncode: raise ValueError('分镜预案命令失败: ' + proc.stderr[-1000:])
                response = read_json(output, {})
            validate_response(request, response)
            # Recheck inputs after external work; stale work remains only in its history folder.
            latest = subtitle_review.prepare(job, cfg)
            if request_for(job, cfg, latest)['request_hash'] != request['request_hash']:
                raise ValueError('预案执行期间输入版本改变；恢复后重新准备')
            atomic_json(response_path, response)
            atomic_json(job / 'storyboard_preparation' / request['request_hash'] / 'response.json', response)
            result.update(status='prepared', response_hash=digest(response), requires_final_reconciliation=True)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            measured['status'] = 'error'
            result.update(status='failed', error=str(exc))
        atomic_json(status_path, result)
        return result


def alongside_review(job, cfg, subtitles, review):
    """Preparation failures never discard successfully completed subtitle review."""
    try:
        request = prepare(job, cfg, subtitles)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        atomic_json(job / 'storyboard_prepare_status.json', {'status': 'failed', 'error': str(exc)})
        return review()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='storyboard-prepare') as pool:
        future = pool.submit(run, job, cfg, request)
        try:
            return review()
        finally:
            result = future.result()
            print('[storyboard_prepare] ' + result['status'] + '；字幕通过后需核对预案再定稿')
