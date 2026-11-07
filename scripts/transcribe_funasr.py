#!/usr/bin/env python3
"""Recognize final audio with local Paraformer; optional batch-owned pipe worker."""
import argparse
import contextlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from artifacts import atomic_json


def validate_result(result):
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise ValueError('单条音频必须返回一个FunASR结果，不能静默丢弃额外结果')
    data = result[0]; text = data.get('text', ''); stamps = data.get('timestamp')
    if not isinstance(text, str) or not text.strip() or not isinstance(stamps, list) or not stamps:
        raise ValueError('FunASR未返回文字和真实timestamp；不能按字数均分时间')
    if len(text.split()) != len(stamps):
        raise ValueError('FunASR文字单元与timestamp数量不一致，需重新识别或真实对齐')
    for pair in stamps:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError('FunASR timestamp必须为毫秒起止对')
    return data


@contextlib.contextmanager
def measure(metrics, name):
    start = time.perf_counter()
    try: yield
    finally: metrics[name] = time.perf_counter()-start


def recognize(model, input_path, hotword, metrics):
    if not Path(input_path).is_file(): raise ValueError('输入音频不存在')
    with tempfile.TemporaryDirectory(prefix='novel-funasr-') as tmp:
        wav = Path(tmp)/'audio.wav'
        with measure(metrics, 'audio_prepare'):
            r = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-i', str(input_path), '-map', '0:a:0', '-vn', '-ar', '16000', '-ac', '1',
                '-c:a', 'pcm_s16le', str(wav)], capture_output=True, text=True, timeout=120)
            if r.returncode or not wav.is_file(): raise ValueError('临时WAV转换失败: '+r.stderr[-1000:])
        options = {'input': str(wav), 'output_timestamp': True, 'cache': {}, 'hotword': hotword or None}
        # Non-streaming calls: no previous audio cache or hotwords retained in request options.
        with measure(metrics, 'recognition'):
            data = validate_result(model.generate(**options))
        return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path, nargs='?'); p.add_argument('output', type=Path, nargs='?')
    p.add_argument('--model', type=Path, required=True); p.add_argument('--device', default='cpu')
    p.add_argument('--threads', type=int, default=4); p.add_argument('--hotword', default='')
    p.add_argument('--worker', action='store_true')
    args = p.parse_args(); metrics = {}; channel = sys.stdout
    def send(value): channel.write(json.dumps(value, ensure_ascii=False)+'\n'); channel.flush()
    try:
        if not (args.model/'model.pt').is_file() or args.threads <= 0: raise ValueError('需要有效本地模型目录及正数threads')
        if not args.worker and (not args.input or not args.input.is_file() or not args.output or args.output.exists() or args.input.resolve()==args.output.resolve()):
            raise ValueError('输入必须存在，输出必须是另一个尚不存在的文件')
        with contextlib.redirect_stdout(sys.stderr), measure(metrics, 'model_load'):
            from funasr import AutoModel
            model = AutoModel(model=str(args.model.resolve()), device=args.device, ncpu=args.threads,
                              disable_update=True, check_latest=False, trust_remote_code=False)
        if args.worker:
            send({'ready': True, 'model_load_seconds': metrics['model_load']})
            for line in sys.stdin:
                metrics = {}
                try:
                    request = json.loads(line)
                    with contextlib.redirect_stdout(sys.stderr):
                        data = recognize(model, request['input'], request.get('hotword', ''), metrics)
                    send({'data': data, 'timings': metrics})
                except Exception as exc:
                    send({'error': str(exc), 'timings': metrics}); return 2
        else:
            with contextlib.redirect_stdout(sys.stderr): data = recognize(model, args.input, args.hotword, metrics)
            atomic_json(args.output, data)
            atomic_json(args.output.with_suffix('.timings.json'), {'status': 'ok', 'timings': metrics})
            print(str(args.output))
        return 0
    except Exception as exc:
        if args.worker: send({'error': str(exc), 'timings': metrics})
        else:
            if args.output: atomic_json(args.output.with_suffix('.timings.json'), {'status': 'error', 'timings': metrics})
            print('FunASR转写失败: '+str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__': raise SystemExit(main())
