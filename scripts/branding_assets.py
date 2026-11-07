"""Plan one native playback sticker; no local media or repeated GIF cycles."""
import math


def playback_sticker(style, body_us, start_us=1_000_000):
    if type(body_us) is not int or type(start_us) is not int or not 1_000_000 <= start_us < body_us:
        raise ValueError('贴纸显示区间必须从封面首秒之后到音频结束')
    sid=style.get('sticker_id')
    if not isinstance(sid,str) or not sid.isascii() or not sid.isdecimal():
        raise ValueError('贴纸ID必须为数字字符串')
    for key in ('x','y','scale_x','scale_y','alpha'):
        value=style.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('贴纸位置/缩放无效：'+key)
    if style['scale_x'] <= 0 or style['scale_x'] != style['scale_y'] or style['alpha'] != 1:
        raise ValueError('原生贴纸接口要求等比缩放且不透明度为1')
    return {'role':'playback','kind':'sticker','sticker_id':sid,'start_us':start_us,'end_us':body_us,
            'style_reference':{k:style[k] for k in ('x','y','scale_x','scale_y','alpha')}}
