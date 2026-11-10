"""Keep presentation choice separate from delivery backend and paid image work."""
from pathlib import Path
import re
from artifacts import atomic_json, read_json

ROOT = Path(__file__).resolve().parent.parent
MODES = ('hyperframes', 'images')
OPENING_STYLES = ('guochao', 'flat-vector', 'comic-pop', 'paper-cut', 'editorial-collage', 'ukiyo-e')
WEB_HANDOFF = ('cut_plan.json','cut_result.json','copy.json','subtitle_review_request.json','subtitle_review.json',
               'final_transcript.json','final_transcript.meta.json','subtitles.csv')

def selected(cfg):
    value = cfg.get('presentation')
    if value is None:
        # Legacy jobs predate the new entry question; do not invalidate them.
        return {'mode': 'images', 'legacy': True}
    if not isinstance(value, dict) or value.get('mode') not in MODES:
        raise ValueError('presentation.mode须为hyperframes或images；先取得本次选择，不按题材擅自代选')
    if not isinstance(value.get('choice_evidence'), str) or not value['choice_evidence'].strip():
        raise ValueError('presentation.choice_evidence须记录实际用户选择或明确沿用的已有选择')
    style = value.get('opening_style', 'auto')
    if style != 'auto' and (not isinstance(style,str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',style)
            or not (ROOT/'vendor/motion-showreel/references/styles'/f'{style}.md').is_file()):
        raise ValueError('未找到指定开场风格：'+str(style))
    if 'image_inserts' in value and not isinstance(value['image_inserts'],bool):
        raise ValueError('presentation.image_inserts须为true或false')
    return value

def web_request(job, cfg):
    choice = selected(cfg)
    if choice['mode'] != 'hyperframes':
        return None
    from content_selection import speed
    payload = {
        'status': 'awaiting_agent_web_animation',
        'presentation': choice,
        'instructions': str(ROOT/'references/web-animation.md'),
        'template_assets': str(ROOT/'assets/knowledge-templates'),
        'opening_style_root': str(ROOT/'vendor/motion-showreel/references/styles'),
        'next_script': str(ROOT/'scripts/prepare_web_video.py'),
        'audio_policy': 'reuse_prepared_narration_at_timeline_speed_1_and_final_word_timestamps',
        'narration_speed': speed(read_json(Path(job)/'cut_plan.json', {}), cfg),
        'automatic_story_image_batch': False,
        'delivery':__import__('delivery').requested(cfg),
        'required_inputs':list(WEB_HANDOFF),
        'note': '转入网页动画制作待办；这不是再次询问用户，也不是成片就绪。'
    }
    atomic_json(Path(job)/'web_animation_request.json', payload)
    return payload

def image_pipeline_guard(job, cfg):
    if not web_request(job, cfg):
        return ''
    return ('AWAITING_REVIEW:网页动画路线已选择；按web_animation_request.json和references/web-animation.md'
            '准备图像动画第一分镜、知识模板正文及按需插图。不要继续纯图片80–100张批处理；'
            '只对明确需要的插图调用generate_images.py，保留原声时码。')
