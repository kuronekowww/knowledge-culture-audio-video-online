"""Choose one quiet, contrasting surround from representative story images."""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageColor, ImageOps
from artifacts import atomic_bytes, atomic_json, digest, file_hash, read_json


def luminance(rgb):
    channels = [v / 255 for v in rgb]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))


def contrast(a, b):
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)


def choose_color(rgb_samples, policy=None, text_colors=None):
    import layout_profile
    policy = policy or layout_profile.load()['body_frame']
    colors = [ImageColor.getrgb(c) for c in (text_colors or policy['foreground_colors'])]
    samples = list(rgb_samples)
    if not samples:
        raise ValueError('选择包框底色需要正文图片采样或网页画布底色')
    best, score = None, -1
    for hex_color in policy['palette']:
        rgb = ImageColor.getrgb(hex_color)
        if min(contrast(rgb, fg) for fg in colors) < policy['min_text_contrast']:
            continue
        # Downweight the closest quarter to avoid blending into similar shots.
        distances = sorted((sum((a-b)**2*w for a,b,w in zip(rgb,other,(.28,.46,.26))))**.5
                           for other in samples)
        quality = .55*distances[len(distances)//4] + .45*sum(distances)/len(distances)
        if quality > score:
            best, score = hex_color, quality
    if best is None:
        raise ValueError('包框色板无法满足标题与来源图可读性')
    return best


def representative(items, count):
    if len(items) <= count:
        return items
    return [items[round(i*(len(items)-1)/(count-1))] for i in range(count)]


def materialize(folder, pictures, center_colors=()):
    """Persist a one-colour image and bind the decision to sampled middle artwork."""
    import layout_profile
    profile = layout_profile.load()
    policy = profile['body_frame']
    sample = representative(pictures, policy['sample_shots'])
    sources = [{'id':r['id'], 'sha256':r.get('sha256') or file_hash(Path(r['path']))} for r in sample]
    key = digest([file_hash(Path(__file__)), policy, profile['canvas'], sources, list(center_colors)])
    dest = Path(folder)/'frame-colors'
    record_path = dest/(key+'.json')
    image = dest/(key+'.png')
    saved = read_json(record_path,{})
    if saved.get('key') == key and image.is_file() and file_hash(image) == saved.get('sha256'):
        return {**saved,'path':str(image.resolve())}
    if policy.get('algorithm') == 'fixed-black-v1':
        color = policy.get('color','#000000')
        out = BytesIO(); Image.new('RGB',(profile['canvas']['width'],profile['canvas']['height']),color).save(out,format='PNG')
        atomic_bytes(image,out.getvalue())
        record={'key':key,'revision':1,'mode':'episode_solid','color':color,
                'sha256':file_hash(image),'sampled_scenes':sources,'center_colors':list(center_colors),
                'selection_basis':'explicit_default_black; no palette selection'}
        atomic_json(record_path,record)
        return {**record,'path':str(image.resolve())}
    pixels = [ImageColor.getrgb(c) for c in center_colors]
    for row in sample:
        with Image.open(row['path']) as source:
            im = ImageOps.exif_transpose(source).convert('RGB')
            tile=im.resize((4,4),Image.Resampling.BOX)
            pixels.extend(tile.getpixel((x,y)) for y in range(4) for x in range(4))
    color = choose_color(pixels,policy)
    size = (profile['canvas']['width'], profile['canvas']['height'])
    out = BytesIO(); Image.new('RGB',size,color).save(out,format='PNG')
    atomic_bytes(image,out.getvalue())
    record = {'key':key,'revision':1,'mode':'episode_solid','color':color,
              'sha256':file_hash(image),'sampled_scenes':sources,'center_colors':list(center_colors)}
    atomic_json(record_path,record)
    return {**record,'path':str(image.resolve())}
