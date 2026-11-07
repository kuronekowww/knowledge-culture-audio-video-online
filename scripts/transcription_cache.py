"""Reuse ASR only when audio, model, glossary and decoding options match."""
import subprocess
import tempfile
from pathlib import Path

import config
import timing
import resident_asr
import asr_backend
import shared_album
import subtitle_review
from artifacts import atomic_copy, atomic_json, digest, file_hash, file_lock, read_json, verified_file


def transcribe(job: Path, cfg: dict) -> str:
    audio = subtitle_review.audio_path(job)
    kind = asr_backend.backend(cfg)
    if kind == 'bailian_paraformer':
        import bailian_asr
        return bailian_asr.transcribe(job, cfg)
    model = asr_backend.model_path(cfg)
    if not audio.is_file():
        return '缺少最终音频'
    identity = asr_backend.identity(cfg)
    if not identity['model_sha256']:
        return ('缺少本地Paraformer模型目录，设置FUNASR_MODEL或asr.model' if kind == 'funasr'
                else '缺少WHISPER_MODEL或config.asr.model')
    settings = cfg.get('asr', {})
    glossary = shared_album.dictionary(cfg)
    prompt = (' ' if kind == 'funasr' else '，').join(e['canonical'] for e in glossary['entries'])
    extra = settings.get('whisper_args', [])
    if not isinstance(extra, list) or any(not isinstance(x, str) for x in extra):
        return 'asr.whisper_args必须为字符串数组'
    reserved = {'-f', '--file', '-of', '--output-file', '-m', '--model', '-tr', '--translate', '-nt', '--no-timestamps'}
    if any(x.split('=')[0] in reserved for x in extra):
        return 'asr.whisper_args不可改变输入、输出、模型或转写任务'
    if kind == 'funasr' and extra:
        return 'FunASR不接受whisper_args；请移除旧后端参数'
    audio_sha = file_hash(audio)
    key = digest({'schema': 2, 'audio_sha256': audio_sha, 'backend_identity': identity,
                  'prompt': prompt, 'settings': settings})
    entry = shared_album.album_root(cfg) / 'asr' / key
    cached = entry / 'transcript.json'
    with file_lock(entry / 'asr.lock'):
        saved = read_json(entry / 'record.json', {})
        if not verified_file(cached, saved.get('sha256')):
            entry.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=entry, prefix='attempt-') as td:
                out = Path(td) / 'transcript.json'
                if kind == 'funasr':
                    cmd = [identity['python'], str(config.SCRIPT_DIR / 'transcribe_funasr.py'),
                           str(audio.resolve()), str(out), '--model', str(model.resolve()),
                           '--device', settings.get('device', 'cpu'), '--threads', str(settings.get('threads', 4))]
                    if prompt:
                        cmd.extend(['--hotword', prompt])
                else:
                    cmd = [config.PY, str(config.SCRIPT_DIR / 'transcribe.py'), str(audio.resolve()), str(out),
                           '--model', str(model.resolve()), '--whisper-cli', config.WHISPER_CLI,
                           '--language', settings.get('language', 'zh'), '--format', 'json',
                           '--threads', str(settings.get('threads', 4))]
                    if settings.get('fine_timestamps', True):
                        cmd.append('--fine-timestamps')
                    if prompt:
                        cmd.extend(['--prompt', prompt])
                    if settings.get('gpu', True) is False:
                        cmd.append('--whisper-arg=-ng')
                    cmd.extend('--whisper-arg=' + x for x in extra)
                # Different highlights share the GPU budget, not only identical audio keys.
                with timing.span('asr_device_queue'):
                    device_lock = file_lock(shared_album.album_root(cfg).parent.parent / 'asr-device.lock', timeout=3600)
                    device_lock.__enter__()
                try:
                    if kind == 'funasr' and resident_asr.ACTIVE is not None:
                        resident_asr.ACTIVE.transcribe(identity, model, settings, audio, out, prompt)
                        r = subprocess.CompletedProcess(cmd, 0, '', '')
                    else:
                        with timing.span('asr_process'):
                            r = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.get('timeout_seconds', 900))
                        measured = read_json(out.with_suffix('.timings.json'), {})
                        for name, seconds in measured.get('timings', {}).items():
                            timing.emit(name, seconds, measured.get('status', 'ok'))
                finally:
                    device_lock.__exit__(None, None, None)
                if r.returncode or not out.is_file():
                    return f'{kind} ASR失败，保留旧稿且不自动切换后端: {(r.stderr + r.stdout)[-2000:]}'
                data = read_json(out)
                if kind == 'funasr':
                    from transcribe_funasr import validate_result
                    validate_result([data])
                    subtitle_review.units_from_asr(data, subtitle_review.duration_us(audio))
                elif not data.get('transcription'):
                    return 'ASR结果为空，保留旧稿'
                if file_hash(audio) != audio_sha:
                    return '转写期间音频发生变化，结果未应用'
                atomic_copy(out, cached)
                atomic_json(entry / 'record.json', {'sha256': file_hash(cached), 'key': key})
        else:
            timing.emit('asr_cache_hit', 0)
        if file_hash(audio) != audio_sha or asr_backend.identity(cfg) != identity:
            return '转写期间音频或模型变化，结果未应用'
        atomic_copy(cached, job / 'final_transcript.json')
        atomic_json(job / 'final_transcript.meta.json', {'schema': 1, 'request_hash': key,
                    'audio_sha256': audio_sha, 'transcript_sha256': file_hash(cached),
                    'model_sha256': identity['model_sha256'], 'backend_identity': identity,
                    'asr_settings_hash': digest(settings),
                    'prompt_hash': digest(prompt)})
    return ''
