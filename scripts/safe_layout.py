"""User-defined Channels safe slots; pixel geometry is separate from API units."""
import copy
import math
import unicodedata
from pathlib import Path

from PIL import Image, ImageOps, ImageStat


def slot(spec, role):
    portrait = spec['portrait']
    safe = [0, 0, portrait['canvas']['width'], portrait['canvas']['height']]
    box = portrait['safe_area']['slots_px'][role]
    if not (0 <= safe[0] < safe[2] <= portrait['canvas']['width']
            and 0 <= safe[1] < safe[3] <= portrait['canvas']['height']
            and safe[0] <= box[0] < box[2] <= safe[2]
            and safe[1] <= box[1] < box[3] <= safe[3]):
        raise ValueError('安全区或元素区域越界：' + role)
    return tuple(box)


def save_png(image, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    image.save(temporary, format='PNG')
    temporary.replace(target)


def transparent_canvas(source, target, size, box, crop_alpha=False):
    """Preserve source art and transparency, contain it in a full-canvas layer."""
    with Image.open(source) as raw:
        image = ImageOps.exif_transpose(raw).convert('RGBA')
    if crop_alpha:
        bounds = image.getchannel('A').getbbox()
        if bounds is None:
            raise ValueError('透明图片没有可见内容')
        image = image.crop(bounds)
    fg = ImageOps.contain(image, (box[2]-box[0], box[3]-box[1]),
                          method=Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', size)
    canvas.alpha_composite(fg, (box[0]+(box[2]-box[0]-fg.width)//2,
                               box[1]+(box[3]-box[1]-fg.height)//2))
    save_png(canvas, target)


def label_theme(background_path, style, size, box):
    override = style.get('theme')
    if override in ('dark', 'light'):
        return override
    if override is not None:
        raise ValueError('source_label.theme只允许dark或light')
    with Image.open(background_path) as raw:
        image = ImageOps.fit(ImageOps.exif_transpose(raw).convert('RGB'), size,
                             method=Image.Resampling.LANCZOS)
        luma = ImageStat.Stat(ImageOps.grayscale(image.crop(box))).mean[0]
    return 'light' if luma >= style['light_background_luma_threshold'] else 'dark'


def text_style(text, style):
    """Conservative fit estimate, not a claim of measured editor glyph bounds.

    Keep characters, type size, ink, stroke and timing; scale uniformly. Saved
    draft/export still requires visual bounds review with its actual font.
    """
    result = copy.deepcopy(style)
    if result.get('layout_profile'):
        return result
    box = result.get('safe_box_px')
    if box is None:
        return result
    lines = text.splitlines() or ['']
    units = max(sum(1 if unicodedata.east_asian_width(c) in ('W','F') else .65
                    for c in line) for line in lines)
    em = result['font_size'] * 4.0
    scale = min(result.get('scale_x', 1), result.get('scale_y', 1),
                (box[2]-box[0]-12)/(max(1, units)*em+8),
                (box[3]-box[1]-8)/(len(lines)*em+8))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('文字安全区域无效')
    result.update(scale_x=scale, scale_y=scale)
    return result
