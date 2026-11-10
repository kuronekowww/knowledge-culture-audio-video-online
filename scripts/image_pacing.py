"""Plan and check requested image density using final audio length, before billing."""
import math
from artifacts import read_json


def recommendation(duration):
    if not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=1:raise ValueError('音频时长无效')
    minimum=max(1,math.ceil(duration*80/600));maximum=max(minimum,math.ceil(duration*100/600))
    target=min(maximum,max(minimum,round((duration-1)/7)))
    return {'body_seconds':duration,'body_images_min':minimum,'body_images_target':target,'body_images_max':maximum,
            'target_seconds_per_image':7,'cover_images':1,'album_background':'one local solid colour per episode; no image request'}


def validate(shots,duration):
    plan=recommendation(duration)
    if not plan['body_images_min']<=len(shots)<=plan['body_images_max']:
        raise ValueError(f"本条{duration:g}秒应有{plan['body_images_min']}～{plan['body_images_max']}张正文图，建议{plan['body_images_target']}张（约7秒一镜），实际{len(shots)}张；先补齐语义分镜再生图，不重复同图凑数")
    for row in shots:
        span=float(row['end'])-float(row['start'])
        if not math.isfinite(span) or span<=0:raise ValueError('分镜时长必须为正')
        if (span<4 or span>10) and not str(row.get('pacing_reason','')).strip():
            raise ValueError('分镜明显偏离约7秒：'+str(row.get('id'))+'；需调整或记录具体语义时长原因pacing_reason')
    return plan


def validate_job(job):
    import subtitle_review
    return validate(read_json(job/'storyboard.json')['shots'],subtitle_review.duration_us(subtitle_review.audio_path(job))/1e6)
