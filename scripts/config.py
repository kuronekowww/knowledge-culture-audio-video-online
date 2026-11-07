"""Server paths only; no desktop editor or model auto-discovery."""
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
from skill_env import load_skill_env
load_skill_env(SKILL_DIR / '.env')
JOBS_DIR = Path(os.environ.get('KNOWLEDGE_JOBS_DIR', Path.cwd() / 'knowledge-jobs'))
SHARED_ROOT = Path(os.environ.get('KNOWLEDGE_SHARED_DIR', JOBS_DIR / '_shared'))
SPEC_PATH = SKILL_DIR / 'spec.json'
PY = os.environ.get('KNOWLEDGE_PYTHON') or sys.executable
ASR_BACKEND = 'bailian_paraformer'
# Optional legacy readers remain importable; never load or discover local models by default.
FUNASR_PYTHON = os.environ.get('FUNASR_PYTHON', '')
FUNASR_MODEL = os.environ.get('FUNASR_MODEL', '')
WHISPER_MODEL = os.environ.get('WHISPER_MODEL', '')
WHISPER_CLI = os.environ.get('WHISPER_CLI', 'whisper-cli')
MOSN_BASE_URL = os.environ.get('MOSN_BASE_URL', 'http://ops.ximalaya.com/mosn')
AUDIO_CDN_BASE = os.environ.get('AUDIO_CDN_BASE', 'https://aod.cos.tx.xmcdn.com/')
GPT_IMAGE_TOKEN_ENV = 'GPT_IMAGE_2_TOKEN'


def load_spec():
    import layout_profile
    return layout_profile.compile_spec(json.loads(SPEC_PATH.read_text(encoding='utf-8')))


def effective_spec(cfg):
    import creative_options
    spec = load_spec()
    spec['branding']['enabled'] = creative_options.selected(cfg)['branding_enabled']
    return spec
