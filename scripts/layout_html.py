"""OpenFilm pages generated only from resolved pixel styles."""
import html
import json


def font_css(descriptor):
    source=(f'local({json.dumps(descriptor["postscript"])})' if descriptor.get('embedding')=='local'
            else f'url("assets/{descriptor["project_name"]}")')
    return f'@font-face{{font-family:KnowledgeBound;src:{source};font-weight:{descriptor["weight"]}}}'


def text_page(text,style,font):
    s=style['pixel_style'];box=style['safe_box_px'];w,h=box[2]-box[0],box[3]-box[1]
    align='flex-start' if style['alignment']==0 else 'center'
    # Measured user transforms are flattened into a single pixel box and font size.
    return f'''<!doctype html><meta charset="utf-8"><style>{font_css(font)}
html,body{{margin:0;width:100%;height:100%;background:transparent;overflow:visible}}
#wrap{{width:{w}px;height:{h}px;display:flex;box-sizing:border-box;padding-left:{s['stroke_px']/2 if align=='flex-start' else 0}px;align-items:center;justify-content:{align}}}
#text{{display:block;flex-shrink:0;font-family:KnowledgeBound;font-synthesis:none;font-size:{s['font_size_px']}px;font-weight:{s['weight']};line-height:{s['line_height']};letter-spacing:{s['letter_spacing_px']}px;color:{s['color']};white-space:pre;-webkit-text-stroke:{s['stroke_px']}px {s['stroke_color']};paint-order:stroke fill;transform:scaleX({s['resolved_scale_x']});transform-origin:{'left' if align=='flex-start' else 'center'} center;}}
</style><div id="wrap"><span id="text">{html.escape(text)}</span></div>
<script>window.film={{width:{w},height:{h},frame(){{}},ready:async()=>{{const fonts=await document.fonts.load('{s['weight']} {s['font_size_px']}px KnowledgeBound');if(!fonts.length)throw new Error('Required font missing');await document.fonts.ready;const width=document.getElementById('text').getBoundingClientRect().width+{s['stroke_px']*s['resolved_scale_x']};if(width>{s['max_width_px']}+.25)throw new Error('Text exceeds canonical width: '+width);}}}};</script>'''


def caption_css(style,font):
    s=style['pixel_style'];sh=s.get('shadow');shadow='none' if not sh else f'{sh["x_px"]}px {sh["y_px"]}px {sh["blur_px"]}px rgba(0,0,0,{sh["alpha"]})'
    return font_css(font)+f'''\n::cue {{font-family:KnowledgeBound;font-synthesis:none;font-size:{s['font_size_px']}px;font-weight:{s['weight']};line-height:{s['line_height']};color:{s['color']};background:transparent;padding:0;white-space:pre;letter-spacing:{s['letter_spacing_px']}px;-webkit-text-stroke:{s['stroke_px']}px rgba(0,0,0,{s['stroke_alpha']});paint-order:stroke fill;text-shadow:{shadow};}}\n'''
