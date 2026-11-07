"""Compile and validate the user's cover hierarchy from the unique layout file.

Pixel measurements in a review are agent visual annotations, never invented OCR.
The attached image is a proportion reference, not a new generated cover.
"""
import math
import re
import layout_profile
from artifacts import digest


def settings():
    p=layout_profile.load()
    return p,p['cover_design']


def binding():
    p,c=settings()
    return {'id':c['policy_id'],'sha256':digest({'cover_design':c,'canvas':p['canvas'],
        'safe_rect':p['cover_safe_rect_px'],'title_width':title_box(p,c)[2]-title_box(p,c)[0],
        'reference_sha256':p['asset_sha256'][c['reference_asset']],'copy_policy':p['copy_policy']})}


def title_box(p=None,c=None):
    if p is None:p,c=settings()
    width=p
    for part in c['episode_title']['max_width_from'].split('.'):width=width[part]
    center=p['canvas']['width']/2
    top,bottom=c['episode_title']['region_y_px']
    return [center-width/2,top,center+width/2,bottom]


def title(copy_text):
    p,c=settings();value=copy_text.get('episode_title');lines=copy_text.get('cover_title_lines')
    if not isinstance(value,str) or not value.strip():raise ValueError('copy.episode_title需要完整本集标题')
    if (not isinstance(lines,list) or len(lines) not in c['episode_title']['allowed_line_counts']
            or any(not isinstance(s,str) or not s.strip() or any(x in s for x in '\r\n\u2028\u2029') for s in lines)):
        raise ValueError('copy.cover_title_lines必须为两行或三行，不能包含隐藏换行')
    if ''.join(lines)!=value:raise ValueError('封面标题换行不得增删字或改变标点')
    return value


def prompt(copy_text,core,design=None):
    p,c=settings();full=title(copy_text);colors=c['episode_title']['line_colors'];short=c['album_short_name']
    safe=p['cover_safe_rect_px'];canvas=p['canvas'];box=title_box(p,c)
    out='\n【当前封面规则优先于旧提示词和历史构图】'
    out+=f"成图{canvas['width']}×{canvas['height']}，背景满幅到四边，不画上下黑带、边框或内嵌卡片；关键人脸、标识和全部标题字形在安全区{safe}。"
    out+=f"参考已持久化比例图{c['reference_asset']}的主体/文字层级（执行者看图提炼，并非声称接口已上传附件）：{c['reference_scope']}。"
    out+=f"专辑核心人物/书籍/事件占主视觉；关键脸部/主体优先在{c['subject']['critical_focus_region_px']}，身体衣服可向外延伸。"
    out+=f"专辑简称逐字为“{core}”，居于顶部区域{short['regions_px']['top']}或右侧区域{short['regions_px']['right']}；默认右侧，单字高度不大于{short['max_glyph_height_px']}px，比旧版巨大专辑字更小，不能压过主体和本集钩子。"
    out+=f"完整视频标题为“{full}”；按已确定的{len(copy_text['cover_title_lines'])}行居中（允许两行或三行，以语义与可读性选择），全部文字在{box}，每行最大宽度{box[2]-box[0]:g}px，不再增加其他行。"
    for i,line in enumerate(copy_text['cover_title_lines']):out+=f"第{i+1}行逐字：“{line}”，主色{colors[i]}。"
    out+='第一行用强调色，后续行用浅色，与第一行明显区分；以粗实、清晰字体突出人物/动作和疑问重点；可以叠在人物衣服或胸像下部，不能遮住脸。只显示专辑简称和完整视频标题，不加播放键、logo或其他文案。'
    if design:
        # Historical geometry, typography and title colors must not override this policy.
        out+='【沿用专辑内容与调性】'+str({k:design[k] for k in ('core_subject','visual_tone') if k in design})+'。'
    return out


def contains(outer,box):
    return (isinstance(box,list) and len(box)==4 and all(isinstance(v,(int,float)) and math.isfinite(v) for v in box)
            and outer[0]<=box[0]<box[2]<=outer[2] and outer[1]<=box[1]<box[3]<=outer[3])


def validate_review(layout,expected_lines=None):
    p,c=settings();lines=layout.get('title_bounds_px',[]);colors=layout.get('title_line_colors',[])
    if layout.get('cover_policy')!=binding():raise ValueError('封面检查未绑定当前两到三行双色规范')
    if (len(lines) not in c['episode_title']['allowed_line_counts'] or (expected_lines is not None and len(lines)!=len(expected_lines))
            or any(not contains(title_box(p,c),b) for b in lines)):
        raise ValueError('封面标题必须为文案对应的两行或三行且每行完整位于当前标题区域/宽度内')
    if any(a[3]>b[1] for a,b in zip(lines,lines[1:])):raise ValueError('封面标题上下顺序错误或重叠')
    if len(colors)!=len(lines) or any(not isinstance(x,str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}',x) for x in colors):
        raise ValueError('需要记录目视核对的每行标题主色')
    rgb=lambda x:[int(x[i:i+2],16) for i in (1,3,5)]
    if max(math.dist(rgb(colors[0]),rgb(x)) for x in colors[1:])<c['episode_title']['min_color_distance_rgb']:
        raise ValueError('封面标题至少两种主色需要明显不同')
    slot=c['album_short_name']['regions_px'].get(layout.get('core_label_placement'))
    if not slot or not layout.get('core_label_bounds_px') or any(not contains(slot,b) for b in layout['core_label_bounds_px']):
        raise ValueError('专辑简称应在当前顶部或右侧区域')
    size=layout.get('core_label_max_glyph_height_px')
    if not isinstance(size,(int,float)) or not 0<size<=c['album_short_name']['max_glyph_height_px']:
        raise ValueError('专辑简称实际字形过大或缺少目视尺寸记录')
    if layout.get('dominant_core_subject') is not True or layout.get('face_or_core_detail_clear') is not True:
        raise ValueError('须实际确认主体占主要画面、标题没有挡住脸部/核心细节')
    return True
