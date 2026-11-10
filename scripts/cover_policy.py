"""Confirmed episode cover composition and its human visual inspection."""
import math
from pathlib import Path
from io import BytesIO
import re

from PIL import Image

import layout_profile
from artifacts import atomic_bytes, digest, file_hash, read_json


PREVIOUS_BINDING = {'id':'two-or-three-line-cover-20261107',
                    'sha256':'a2e2e71149510e7be54ccfca46bb379129fb475ea2523db73f7bac037144a6f9'}


R24_BINDING = {'id':'central-subject-cover-20261110-r24',
               'sha256':'6e7ab84b54a4465983ddaeed62424e86c7c39eddae93d810dfcb89c0a8f0602e'}
R24_PROFILE = 'portrait-team-20261110-solid-still-cover-r24'
R24_TITLE_BOX = [54,1060,1026,1650]


def settings():
    p = layout_profile.load()
    return p, p['cover_design']


def binding():
    p, c = settings()
    return {'id':c['policy_id'], 'sha256':digest({'cover_design':c,'canvas':p['canvas'],
            'crop':p['cover_center_crop_rect_px'],'safe_rect':p['cover_safe_rect_px'],
            'title_box':title_box(p,c),'reference_sha256':p['asset_sha256'][c['reference_asset']],
            'copy_policy':p['copy_policy']})}


def title_hash(copy_text):
    return digest({'title':copy_text.get('episode_title'),'lines':copy_text.get('cover_title_lines')})


def legacy(job, cfg):
    """Only an actual r23 recipe bound to its original words can keep the old cover."""
    if cfg.get('cover_policy_version') == 2:
        return False
    value = read_json(job/'image_manifest.json', {})
    old=value.get('cover_policy',{})
    return (old.get('id')==PREVIOUS_BINDING['id'] and old.get('sha256') in {
            PREVIOUS_BINDING['sha256'],'75bd5e42c3b830d955310427d17d5a125fc4986fbc3b821e250d10905f5d2527'
            } and bool(value.get('cover_title_suffix'))
            and value.get('cover_title_hash')==title_hash(read_json(job/'copy.json',{})))


def submitted_r24_cover(job, cfg, manifest):
    # Look up the original request, including interrupted or pending downloads.
    if manifest.get('cover_policy') != R24_BINDING:
        return None
    import generate_images
    import shared_album
    row = next((r for r in manifest.get('images', []) if r.get('id')=='cover_portrait'), None)
    if not row:
        return None
    fingerprint = generate_images.image_fingerprint(
        manifest.get('api_base',generate_images.DEFAULT_API_BASE), row, manifest.get('quality','low'))
    output = job/Path(manifest.get('output_dir','images/generated'))
    receipts = [output/'.receipts/cover_portrait.json',
                output/'.cache'/fingerprint/'request.json',
                shared_album.album_root(cfg)/'images'/fingerprint/'request.json']
    if any(read_json(path, {}).get('fingerprint') == fingerprint for path in receipts):
        return row
    report = read_json(output/'image_generation_report.json', {})
    if any(r.get('fingerprint') == fingerprint and r.get('attempts',0)>0 for r in report.get('results', [])):
        return row
    return None


def title_box(p=None, c=None):
    if p is None:
        p, c = settings()
    left,_,right,_ = p[c['episode_title']['width_from']]
    top,bottom = c['episode_title']['region_y_px']
    return [left,top,right,bottom]


def title(copy_text, *, historical=False):
    value=copy_text.get('episode_title')
    lines=copy_text.get('cover_title_lines')
    if not isinstance(value,str) or not value.strip():
        raise ValueError('copy.episode_title 需要完整本集标题')
    counts = (2,3) if historical else settings()[1]['episode_title']['allowed_line_counts']
    if (not isinstance(lines,list) or len(lines) not in counts or
            any(not isinstance(s,str) or not s.strip() or len(s.splitlines())!=1 or s!=s.strip() for s in lines)):
        raise ValueError('封面标题按完整语义排一行或两行')
    if ''.join(lines)!=value:
        raise ValueError('封面标题换行不得增删字或改变标点')
    return value


def options(copy_text):
    label = copy_text.get('album_core_label') or ''
    if not isinstance(label,str) or len(label.splitlines())>1:
        raise ValueError('专辑简称应为单行文字或留空')
    placement = copy_text.get('cover_label_placement','auto')
    if placement not in ('left','right','top','auto','none'):
        raise ValueError('简称位置可选 left、right、top、auto、none')
    label = label.strip() if placement!='none' else ''
    accent = copy_text.get('cover_accent_line')
    if accent is not None and (type(accent)!=int or not 0<=accent<=len(copy_text['cover_title_lines'])):
        raise ValueError('cover_accent_line 从1起计，0表示单色；行号应存在')
    return {'label':label, 'placement':placement if label else 'none', 'accent':accent}


def source_box(box):
    """Project a final-canvas box inward into the provider's 1024x1536 frame."""
    scale=1024/1080
    return [max(0,math.ceil(scale*box[0])), max(0,math.ceil(scale*(box[1]-150))),
            min(1024,math.floor(scale*box[2])), min(1536,math.floor(scale*(box[3]-150)))]


def prompt(copy_text, core=None, design=None, *, previous=False):
    p,c=settings()
    full=title(copy_text)
    opt=options(copy_text)
    slot=c['album_short_name']
    lines=copy_text['cover_title_lines']
    title_rect = R24_TITLE_BOX if previous else title_box(p,c)
    location = '下方' if previous else '画面中下部'
    out = [
        '【本集封面】生成1024×1536原生竖图，背景满幅至四边。后期保留完整横向画面，等比放大，'
        '用顶部与底部的背景延展为9:16；上下边缘各留安静背景。下列坐标均为原生图像素。中央3:4区'
        f"{source_box(p['cover_center_crop_rect_px'])}；主体、关键道具与所有字形（含描边花饰）收在{source_box(p['cover_safe_rect_px'])}。",
        ('主要人物、事件、物品、概念示意或书籍置中形成视觉焦点。主要对象、关键动作及道具完整可辨；'
         + ('自然出画的衣袖边缘和背景纹理可延展。' if previous else '完整前景主体、冠饰与耳坠均在中央3:4内，仅无识别要点的背景纹理可延展。')
         + '可参考案例的主次层级，背景自然延伸到顶部和底部。'),
        f"完整标题“{full}”在{location}居中排{len(lines)}行，全部字形落在{source_box(title_rect)}；"
        '可将文字叠在服装、桌面或画面下部；留出人脸与物品/概念的关键识别细节。'
        '可用清晰易辨的花体或艺术字，必须保留标点与笔画。'
    ]
    if not previous:
        title_center = c['episode_title']['group_center_y_px']
        native_center = round((title_center - 150) * 1024 / 1080)
        out.append(f'以整组标题字形的中心对准原生图 y={native_center}px。两行作为一组，维持清晰的大字、原有字号层级与行距。'
                   '提前将人物面孔、书名主图或关键动作错开这条标题带，从服饰或桌面留出完整排字空间。')
    for i,line in enumerate(lines,1):
        out.append(f'第{i}行逐字：“{line}”。')
    if opt['accent'] is None:
        out.append('可统一使用高对比文字，也可仅突出其中一行重点色。')
    elif opt['accent']:
        out.append(f"第{opt['accent']}行使用一行重点色，其余行用清晰的中性色。")
    else:
        out.append('全部标题使用同一主色。')
    if opt['label']:
        where = opt['placement']
        slots = {k:source_box(b) for k,b in slot['regions_px'].items() if where in (k,'auto')}
        out.append(f"小号专辑简称逐字为“{opt['label']}”；从指定区域{slots}中选择一处，"
                   f"单字高度至多{math.floor(1024/1080*slot['max_glyph_height_px'])}px，与主体和标题留出空间。")
    else:
        out.append('省略专辑简称，画中只写本集标题。')
    out.append('画中不附加账号、水印、播放键或说明。喜马拉雅品牌由外层标识负责。')
    if not previous:
        out.append('高品质出版物式构图，人物不过大，金红配色克制、留白充分、细节清洁。全部前景主体轮廓、冠饰、头发、耳坠、动作与道具以及全部字形均收入中央3:4，主体缩小给标题留空间；只让无识别要点的背景满幅延展。')
    if design and design.get('visual_tone'):
        out.append('专辑已有视觉调性：'+str(design['visual_tone'])+'。')
    return '\n'+'\n'.join(out)


def contains(outer,box):
    return (isinstance(box,list) and len(box)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in box)
            and outer[0]<=box[0]<box[2]<=outer[2] and outer[1]<=box[1]<box[3]<=outer[3])


def validate_review(layout, expected_lines=None, expected_copy=None, *, previous=False):
    p,c=settings()
    lines=layout.get('title_bounds_px',[])
    active_binding = R24_BINDING if previous else binding()
    title_rect = R24_TITLE_BOX if previous else title_box(p,c)
    if layout.get('cover_policy')!=active_binding:
        raise ValueError('封面检查未绑定当前封面规范')
    if (not isinstance(lines,list) or len(lines) not in c['episode_title']['allowed_line_counts']
            or (expected_lines is not None and len(lines)!=len(expected_lines))
            or any(not contains(title_rect,b) for b in lines)
            or any(a[3]>b[1] for a,b in zip(lines,lines[1:]))):
        raise ValueError('逐行核对标题边界、行数及上下顺序，标题须在下方区域内')
    if not previous:
        group_y = (lines[0][1] + lines[-1][3]) / 2
        rule = c['episode_title']
        if abs(group_y - rule['group_center_y_px']) > rule['group_center_tolerance_px']:
            raise ValueError(f"标题整组的中心高度应在成品 y={rule['group_center_y_px']}px 附近，请核对实际上移位置")
    subjects=layout.get('subject_bounds_px')
    if (not isinstance(subjects,list) or not subjects or
            any(not contains(p['cover_safe_rect_px'],b) for b in subjects)):
        raise ValueError('请标注中央3:4内主要对象、关键动作或关键道具的完整识别区域；冠饰、耳坠和前景轮廓须完整，只有背景纹理可略去')
    focus = layout.get('focus_point_px')
    x0,y0,x1,y1=c['subject']['critical_focus_region_px']
    if (not isinstance(focus,list) or len(focus)!=2 or not all(type(v) in (int,float) and math.isfinite(v) for v in focus)
            or not (x0<=focus[0]<=x1 and y0<=focus[1]<=y1)
            or not any(b[0]<=focus[0]<=b[2] and b[1]<=focus[1]<=b[3] for b in subjects)):
        raise ValueError('标注主体内的一个中央视觉焦点，如主角面部、书籍或物件核心')
    if not previous and any(box[0]<=focus[0]<=box[2] and box[1]<=focus[1]<=box[3] for box in lines):
        raise ValueError('标题遮挡了已标注的主体视觉焦点，请调整人物或可排字区域')
    if layout.get('dominant_core_subject') is not True or layout.get('face_or_core_detail_clear') is not True:
        raise ValueError('须目视确认主体占主视觉且标题没有遮挡关键细节')
    opt = options(expected_copy) if expected_copy is not None else {'label':layout.get('core_label',''),'placement':'auto','accent':None}
    if opt['label']:
        label_slot=layout.get('core_label_placement')
        slot=c['album_short_name']['regions_px'].get(label_slot)
        bounds=layout.get('core_label_bounds_px')
        height=layout.get('core_label_max_glyph_height_px')
        if (layout.get('core_label')!=opt['label'] or layout.get('core_label_exact') is not True
                or not slot or opt['placement'] not in ('auto',label_slot)
                or not isinstance(bounds,list) or not bounds or any(not contains(slot,b) for b in bounds)
                or type(height) not in (int,float) or not 0<height<=c['album_short_name']['max_glyph_height_px']):
            raise ValueError('简称的文字、位置或大小与当前封面设置不符')
    elif layout.get('core_label') or layout.get('core_label_bounds_px') or layout.get('core_label_placement') not in (None,'none'):
        raise ValueError('本集已省略简称，请核对实际图中未额外出现简称')
    colors=layout.get('title_line_colors',[])
    if opt['accent'] is not None and len(lines)>1:
        if len(colors)!=len(lines) or any(not isinstance(x,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',x) for x in colors):
            raise ValueError('有明确颜色选择时，记录两行实际主色')
        rgb=lambda s:tuple(int(s[n:n+2],16) for n in (1,3,5))
        distance=math.dist(rgb(colors[0]),rgb(colors[1]))
        if opt['accent'] and (distance<c['episode_title']['min_color_distance_rgb'] or layout.get('accent_line')!=opt['accent']):
            raise ValueError('请核对指定的重点色行')
        if opt['accent']==0 and distance>=c['episode_title']['min_color_distance_rgb']:
            raise ValueError('本集选择了单色标题')
    return True


def crop_preview(job, image, sha):
    path=job/'cover-review'/('center-3x4-'+sha[:16]+'.jpg')
    if not path.is_file():
        with Image.open(image) as im:
            if im.size!=(1080,1920):
                raise ValueError('请使用已裁成1080×1920的最终封面')
            crop=im.crop(tuple(layout_profile.load()['cover_center_crop_rect_px'])).convert('RGB')
            crop.thumbnail((540,720), Image.Resampling.LANCZOS)
            stream=BytesIO()
            crop.save(stream,format='JPEG',quality=90)
            atomic_bytes(path,stream.getvalue())
    return str(path)


def web_review_request(job,cfg,image):
    from title_review import approved_version
    from image_finish import REVIEW
    version=approved_version(job,cfg)
    copy=read_json(job/'copy.json')
    title(copy)
    with Image.open(image) as im:
        if im.size!=(1080,1920):
            raise ValueError('网页封面须为真实1080×1920竖图')
    basis = {'image_sha256':file_hash(image),'cover_policy':binding(),'title_version':version,'options':options(copy)}
    previous_basis = {**basis,'cover_policy':R24_BINDING}
    received = read_json(job/'web-cover-review.json', {}).get('basis', {})
    manifest = read_json(job/'web-cover-manifest.json', {})
    row = submitted_r24_cover(job, cfg, manifest)
    original = None if not row else job/manifest['output_dir']/row['filename']
    if received==previous_basis or (original and original.is_file() and file_hash(original)==basis['image_sha256']
                                    and manifest.get('approved_title_version')==version):
        basis = previous_basis
    return {'cover_path':str(Path(image).resolve()),'basis':basis,
            'expected_title_lines':copy['cover_title_lines'],'review_output':str(job/'web-cover-review.json'),
            'instructions':'对照真实图片逐字读标题，记录 basis、title_exact、reviewer、evidence 和 layout。'
            'layout 字段见 references/cover-generation.md。'+REVIEW}


def validate_web_cover(job,cfg,image):
    if cfg.get('cover_policy_version')!=2 and not (job/'web-cover-review.json').exists():
        return
    request=web_review_request(job,cfg,image)
    review=read_json(job/'web-cover-review.json',{})
    if (review.get('basis')!=request['basis'] or review.get('title_exact') is not True
            or not str(review.get('reviewer','')).strip() or not str(review.get('evidence','')).strip()):
        raise ValueError('网页封面尚需逐字核对当前图片，或标题/图片已改变，请更新 web-cover-review.json')
    validate_review(review.get('layout',{}), request['expected_title_lines'],read_json(job/'copy.json'),
                    previous=request['basis']['cover_policy']==R24_BINDING)
