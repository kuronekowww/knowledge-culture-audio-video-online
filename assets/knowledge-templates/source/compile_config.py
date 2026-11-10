"""Compile editable scene inputs against the installed story-layout authority."""
import colorsys, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def read(name):
    return json.loads((ROOT/name).read_text())
def write(name, value):
    (ROOT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')

binding = read('config/layout-binding.json')
profile = (ROOT/binding['source_path']).resolve()

sys.path.insert(0, str(profile.parent/'scripts'))
import layout_profile as lp
from PIL import ImageColor
from solid_frame import choose_color
lp.check_assets()
layout = lp.load()
if set(layout['font_files']) != {'display','subtitle'}:
    raise ValueError('模板需要标题与字幕两种校准字体')
css=[]
for family, role in [('StoryTitle','display'),('StorySubtitle','subtitle'),('Body','subtitle')]:
    font=layout['font_files'][role]
    source=json.dumps(font['postscript'])
    css.append(f"@font-face{{font-family:{family};src:local({source});font-weight:{font['weight']}}}")
(ROOT/'gallery-fonts.css').write_text('\n'.join(css)+'\n',encoding='utf-8')
examples = read('config/scene-inputs.json')
albums = read('config/album-inputs.json')
types = {'concept','dialogue','compare','chain','steps','boundary','sample','trend'}
def text(value, limit, name):
    assert isinstance(value,str) and 0<len(value)<=limit, f'{name}为空或过长（最多{limit}字），请调整文案'
def color(value):
    assert re.fullmatch(r'#[0-9a-fA-F]{6}',value), '颜色格式应为 #RRGGBB'
def palette(accent):
    color(accent);h,l,s=colorsys.rgb_to_hls(*(int(accent[i:i+2],16)/255 for i in (1,3,5)))
    def shade(sat,light):return '#'+''.join(f'{round(v*255):02x}' for v in colorsys.hls_to_rgb(h,light,sat))
    paper=shade(min(.45,s*.75),.925)
    frame=choose_color([ImageColor.getrgb(paper)])
    return {'accent':accent,'background':frame,'paper':paper,'ink':shade(min(.3,s),.23),'secondary':shade(min(.3,s*.45),.58),'highlight':'#ECC76F'}
for a in albums.values():
    a['display'], a['style'] = lp.resolved_style('album',a['display'],lp.album_color(ROOT/a['cover'])['hex'])
    a['theme']=palette(a['theme']['accent'])
    for value in a['theme'].values(): color(value)
for e in examples:
    assert e['type'] in types
    assert str(e['album_id']) in albums
    assert len(e['title'])==2
    text(e['heading'],20,'画面小标题')
    e['styles']={}
    for role,value in [('title_line1',e['title'][0]),('title_line2',e['title'][1]),('subtitle',e['caption'])]:
        value,style=lp.resolved_style(role,value)
        e['styles'][role]={'text':value,'style':style}
    d=e['data'];text(d['takeaway'],24,'归纳句')
    if e['type']=='concept':
        assert 1<=len(d['inferred'])<=2
        text(d['observed'],5,'观察到的特征')
        for s in d['inferred']:text(s,6,'推断词')
    if e['type']=='dialogue':
        assert len(d['lines'])==len(d['notes'])==2
        for s in d['lines']:text(s,11,'话语卡')
        for s in d['notes']:text(s,12,'对话说明')
    if e['type']=='compare':
        for side in ('left','right'):
            text(d[side]['title'],5,'对照标题');assert len(d[side]['items'])<=2
            for s in d[side]['items']:text(s,10,'对照要点')
    if e['type']=='chain':
        assert 2<=len(d['nodes'])<=4
        for n in d['nodes']:text(n['label'],5,'角色名')
        for n in d['nodes'][:-1]:text(n['verb'],4,'连接词')
    if e['type']=='steps':
        assert len(d['items'])==3
        for s in d['items']:text(s.replace('\n',''),10,'步骤')
    if e['type']=='boundary':
        for k in ('self','other'):text(d[k],7,'范围标题')
        for k in ('self_items','other_items'):
            assert len(d[k])<=3
            for s in d[k]:text(s,4,'范围条目')
        text(d['center'],8,'连接关系')
    if e['type']=='sample':
        for k in ('visible','missing'):text(d[k],12,'样本组标题')
        assert d.get('note'), '示意样本必须保留非统计说明'
    if e['type']=='trend':
        for k in ('up','down','axis'):text(d[k],9,'变化标签')
        assert d.get('note'), '无数据示意必须保留观点说明'
    if 'note' in d:text(d['note'],38,'说明')
    times=e['stage_times'];assert len(times)==5 and times==sorted(times) and 0<=times[0] and times[-1]+.45<=e['demo_duration']
    for c in e.get('theme_override',{}).values():color(c)
    e['timing_status']='design_preview_only_not_audio_aligned'
write('config/examples.json',examples);write('config/albums.json',albums)
write('config/story-layout.snapshot.json',layout)
binding.update(lp.binding())
write('config/layout-binding.json',binding)
kit={'layout':layout,'albums':albums,'examples':examples}
(ROOT/'config.js').write_text('window.KNOWLEDGE_KIT = '+json.dumps(kit,ensure_ascii=False)+';\n')
print(json.dumps({'examples':len(examples),'albums':len(albums),'layout':binding['id'],'font_and_geometry':'verified'},ensure_ascii=False))
