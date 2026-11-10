"""Convert a supported GPT Image proxy portrait into the editable timeline's cover asset."""
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageOps

from artifacts import file_hash

FIT = 'full_width_9x16'
NATIVE_SIZE = (1024, 1536)
CONTENT = (0, 150, 1080, 1770)
OUTPUT = (1080, 1920)


def finish(source, target):
    """Keep the entire native composition at a uniform scale. Continue only the outer background."""
    with Image.open(source) as image:
        if image.size != NATIVE_SIZE:
            raise ValueError(f'封面接口请求 1024×1536，实际返回 {image.width}×{image.height}；保留原图及回执供核对')
        content = image.convert('RGB').resize((CONTENT[2], CONTENT[3]-CONTENT[1]), Image.Resampling.LANCZOS)
    w, h = content.size
    # Sample the narrow margins reserved for background in the cover prompt.
    # Reflect outward from each edge so side labels, faces and titles retain their full width.
    band = CONTENT[1]
    cover = Image.new('RGB', OUTPUT)
    for top, region in ((0, (0,0,w,48)), (CONTENT[3], (0,h-48,w,h))):
        extension = ImageOps.flip(content.crop(region)).resize((w,band), Image.Resampling.BICUBIC)
        cover.paste(extension, (0,top))
    cover.paste(content, (0,band))
    fd, tmp = tempfile.mkstemp(prefix='.cover-', dir=Path(target).parent)
    os.close(fd)
    try:
        cover.save(tmp, 'PNG')
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {'cover_fit':FIT,'native_size':list(NATIVE_SIZE),'content_rect_xyxy':list(CONTENT),
            'source_sha256':file_hash(source),'actual_size':list(OUTPUT)}
