"""Keep generated story images and the album backdrop as independent timeline layers."""
from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageOps
from artifacts import file_hash, read_json

MODE = 'raw_image_full_width_centered_on_separate_background'


def centered_box(source, canvas):
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        width, height = image.size
    if width <= height:
        raise ValueError('正文原图必须为横图：' + str(source))
    shown_height = canvas['width'] * height / width
    top = (canvas['height'] - shown_height) / 2
    return [0, top, canvas['width'], top + shown_height], [width, height]


def for_openfilm(job, line, assets):
    """Accept layered handoffs, or recover hash-bound originals from older baked handoffs."""
    line, assets = deepcopy(line), deepcopy(assets)
    already_layered = line.get('frame_fit') == MODE
    if not already_layered:
        import album_visuals
        import job_versions
        originals = job_versions.image_assets(job)
        background = album_visuals.current(job, read_json(job / 'config.json', {}))
        if background['sha256'] != line.get('album_background', {}).get('sha256'):
            raise ValueError('原交接底图与专辑底图哈希不同；请明确选择同一底图版本')
        aid = 'portrait__album_background'
        if aid in assets:
            raise ValueError('底图素材ID冲突')
        path = Path(background['path'])
        assets[aid] = {'asset_id': aid, 'path': str(path), 'kind': 'image',
                       'sha256': background['sha256'], 'bytes': path.stat().st_size,
                       'role': 'album_background'}
        line['album_background']['asset_id'] = aid
    else:
        originals = {}
    background = line['album_background']
    aid = background.get('asset_id')
    if aid not in assets or assets[aid]['sha256'] != background['sha256']:
        raise ValueError('独立底图素材缺失或哈希不匹配')
    if file_hash(Path(assets[aid]['path'])) != background['sha256']:
        raise ValueError('独立底图文件哈希变化')
    for frame in line['frames']:
        if frame.get('role') != 'story':
            continue
        item = assets[frame['asset_id']]
        if not already_layered:
            original = originals.get(frame.get('shot_id'), {})
            if not original.get('sha256') or original['sha256'] != item.get('source_sha256'):
                raise ValueError('正文原图与已核验交接不匹配：' + str(frame.get('shot_id')))
            item.update(path=original['path'], sha256=original['sha256'])
        path = Path(item['path'])
        if file_hash(path) != item['sha256']:
            raise ValueError('正文原图文件哈希变化：' + frame['asset_id'])
        box, size = centered_box(path, line['canvas'])
        item.update(width=size[0], height=size[1], bytes=path.stat().st_size,
                    role='raw_story_image', source_sha256=item['sha256'])
        frame.update(box_px=box, original_size_px=size)
    background.update(start_us=line['frames'][0]['end_us'], end_us=line['body_duration_us'],
                      box_px=[0, 0, line['canvas']['width'], line['canvas']['height']], fit='cover')
    line['frame_fit'] = MODE
    line['image_layers_policy'] = {
        'mode': MODE, 'background_track': 'track-background', 'story_track': 'track-pictures',
        'source_files_unchanged': True, 'fit': 'width', 'center': 'canvas',
        'story_safe_area_override': 'User requested full canvas width; text and cover safety rules remain.'}
    return line, assets
