"""Validate source-grounded value units, never infer semantic approval from structure."""
import math
import config
from artifacts import read_json


def seconds(value):
    if isinstance(value, bool): raise ValueError('时间不能是布尔值')
    if isinstance(value, str) and ':' in value:
        total = 0.0
        for part in value.replace(',', '.').split(':'): total = total * 60 + float(part)
    else: total = float(value)
    if not math.isfinite(total): raise ValueError('时间必须有限')
    return total


def duration_ok(duration, *, tolerance=0.001):
    if not math.isfinite(duration) or duration + tolerance < config.load_spec()['timing']['body_min_seconds']:
        raise ValueError('正文需至少300秒；按完整价值单元重新选段，不得拉慢、循环或无关拼接凑长')


def validate(plan):
    clips = plan.get('clips')
    if not isinstance(clips, list) or not clips: raise ValueError('需要clips源音频范围')
    if seconds(plan.get('speed', 1)) != 1: raise ValueError('知识文化原声必须原速，不接受speed变速')
    total = 0; previous = -1
    for clip in clips:
        start, end = seconds(clip['start']), seconds(clip['end'])
        if start < 0 or end <= start or start < previous: raise ValueError('clips必须按源顺序、无重复或重叠')
        if seconds(clip.get('speed', 1)) != 1: raise ValueError('不接受自动或显式变速')
        total += end-start; previous = end
    duration_ok(total)
    content = plan.get('content', {})
    if not isinstance(content, dict): raise ValueError('content需要对象')
    for key in ('problem','demand_reason','delivered_value','conclusion','complete_reason','opening_hook'):
        if not isinstance(content.get(key), str) or not content[key].strip(): raise ValueError('content缺少'+key)
    if total > 600 and not str(content.get('extended_reason','')).strip():
        raise ValueError('超过10分钟允许，但需extended_reason说明保留完整论述的原因')
    if len(clips) > 1 and not str(content.get('splice_reason','')).strip(): raise ValueError('多段拼接需splice_reason说明语义连接与删减')
    evidence = content.get('evidence')
    if not isinstance(evidence, list) or not evidence: raise ValueError('缺少已交付价值的源文稿证据')
    for row in evidence:
        a,b=seconds(row['start']),seconds(row['end'])
        if not isinstance(row.get('text'),str) or not row['text'].strip() or not any(seconds(c['start']) <= a < b <= seconds(c['end']) for c in clips):
            raise ValueError('价值证据必须落在已选择音频范围内')
    hook = content.get('return_hook', {})
    if not isinstance(hook, dict): raise ValueError('return_hook需要对象')
    for key in ('question','evidence'):
        if not isinstance(hook.get(key),str) or not hook[key].strip(): raise ValueError('回站钩子缺少'+key)
    if not str(hook.get('track_id','')).isdigit(): raise ValueError('回站钩子需真实track_id')
    a,b=seconds(hook['start']),seconds(hook['end'])
    if a < 0 or b <= a: raise ValueError('回站来源范围无效')
    if str(hook['track_id']) == str(plan.get('track_id')) and any(a < seconds(c['end']) and b > seconds(c['start']) for c in clips):
        raise ValueError('回站钩子应指向本条之外的真实进阶内容')
    return {'duration_seconds':total,'semantic_review':'agent_required','content':content}


def validate_job(job):
    plan=read_json(job/'cut_plan.json');result=validate(plan)
    hook=plan['content']['return_hook']
    if str(hook['track_id'])==str(plan.get('track_id')):
        import production_checks, subtitle_review
        length=subtitle_review.duration_us(production_checks.source_audio(job))/1e6
        if seconds(hook['end'])>length+.12:raise ValueError('回站证据时码超过同声音真实长度；需核对真实后续声音，不能杜撰超出结尾的区间')
    return result
