"""One batch-owned FunASR process, communicating over private stdin/stdout pipes."""
import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from artifacts import atomic_json, digest
import config
import timing

ACTIVE = None


class Pool:
    def __init__(self):
        self.lock = threading.Lock(); self.process = None; self.key = None
        self.messages = None; self.reader = None

    def _read(self, pipe, messages):
        try:
            for line in pipe:
                try: messages.put(json.loads(line))
                except ValueError: messages.put({'error': 'worker输出不是JSON'})
        finally: messages.put({'error': 'FunASR worker已退出'})

    def _receive(self, timeout):
        try: response = self.messages.get(timeout=timeout)
        except queue.Empty: raise TimeoutError('FunASR worker等待超时')
        if response.get('error'):
            for name, seconds in response.get('timings', {}).items(): timing.emit(name, seconds, 'error')
            raise RuntimeError(response['error'])
        return response

    def _stop(self):
        process = self.process
        self.process = None; self.key = None
        if process:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            process.stdin.close(); process.stdout.close()
            if self.reader: self.reader.join(timeout=2)

    def close(self):
        with self.lock: self._stop()

    def transcribe(self, identity, model, settings, audio, output, hotword):
        key = digest({'identity': identity, 'device': settings.get('device', 'cpu'), 'threads': settings.get('threads', 4)})
        timeout = settings.get('timeout_seconds', 900)
        with timing.span('asr_queue'):
            self.lock.acquire()
        try:
            if self.key != key or not self.process or self.process.poll() is not None:
                self._stop()
                self.messages = queue.Queue()
                with timing.span('asr_process_startup'):
                    self.process = subprocess.Popen([identity['python'], '-u', str(config.SCRIPT_DIR / 'transcribe_funasr.py'),
                        '--worker', '--model', str(model.resolve()), '--device', settings.get('device', 'cpu'),
                        '--threads', str(settings.get('threads', 4))], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1)
                    self.reader = threading.Thread(target=self._read, args=(self.process.stdout, self.messages), daemon=True)
                    self.reader.start(); ready = self._receive(timeout)
                    if ready.get('ready') is not True: raise RuntimeError('worker未准备就绪')
                timing.emit('model_load', ready['model_load_seconds'])
                self.key = key
            self.process.stdin.write(json.dumps({'input': str(audio.resolve()), 'hotword': hotword}) + '\n')
            self.process.stdin.flush()
            result = self._receive(timeout)
            for name, seconds in result['timings'].items(): timing.emit(name, seconds)
            atomic_json(output, result['data'])
        except BaseException:
            self._stop(); raise
        finally: self.lock.release()
