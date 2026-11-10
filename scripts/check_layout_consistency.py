"""Fail closed on duplicate layout sources, stale builds and altered bound assets."""
import argparse
import json
from pathlib import Path
import subprocess
import layout_profile as layout
from artifacts import read_json, file_hash, atomic_json


def check(project=None):
    base=read_json(layout.ROOT/'spec.json');profile=layout.load();errors=[]
    if base.get('layout_profile')!=layout.PROFILE.name:errors.append('spec必须只引用唯一布局文件')
    for key in ('portrait','canvas','fonts','texts','safe_area'):
        if key in base:errors.append('spec重复定义布局：'+key)
    if set(base.get('branding',{}))-{'enabled'}:errors.append('spec重复定义品牌布局/资产')
    if {'cover_seconds','outro_seconds'} & set(base.get('timing',{})):errors.append('时长重复定义')
    if {'upper_title_font_size','upper_title_max_chars'} & set(base.get('presentation_policy',{})):errors.append('旧字符上限/字号仍有效')
    layout.check_assets()
    import cover_policy
    cp=profile['cover_design'];safe=profile['cover_safe_rect_px']
    crop=profile['cover_center_crop_rect_px'];w=profile['canvas']['width'];h=profile['canvas']['height']
    expected=[0,(h-w*4/3)/2,w,(h+w*4/3)/2]
    if crop!=expected or not cover_policy.contains(crop,safe):errors.append('封面安全区必须位于画布中央3:4区域内')
    for box in [cover_policy.title_box(),cp['subject']['critical_focus_region_px'],*cp['album_short_name']['regions_px'].values()]:
        if not cover_policy.contains(safe,box):errors.append('封面构图区域超出最新安全区')
    if cp['episode_title']['allowed_line_counts']!=[1,2]:errors.append('封面标题须为一行或两行')
    if set(cp['album_short_name']['regions_px'])!={'top','left','right'}:errors.append('封面简称需支持左、右、上')
    spec=layout.compile_spec(base)
    for role,box in spec['portrait']['safe_area']['slots_px'].items():
        if not (0<=box[0]<box[2]<=profile['canvas']['width'] and 0<=box[1]<box[3]<=profile['canvas']['height']):errors.append('元素范围越界：'+role)
    for role in ('album_cover','playback'):
        b=profile['boxes_px'][role]
        if b[2]-b[0]!=b[3]-b[1]:errors.append('小封面与唱片必须为正方形：'+role)
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(layout.ROOT/profile['brand_assets']['outro'])]))
    if round(float(probe['format']['duration'])*1e6)!=profile['outro']['duration_us']:errors.append('片尾时长不匹配')
    release=read_json(layout.ROOT/'release_manifest.json',{})
    if any(k in release for k in ('branding_layout','presentation_policy','canvas')):errors.append('发布清单仍包含第二套布局值')
    if release.get('layout_profile')!=layout.binding():errors.append('发布清单的布局绑定过期')
    forbidden=('固定15号','1～12字符','1–12字符','scale=0.20','transform_x=-734','precomposed兼容模式','1～4行','居中3:4裁切','上方或右侧大号核心标识','大号核心标识在上方或右侧')
    for p in [layout.ROOT/'SKILL.md',*(layout.ROOT/'references').glob('*.md')]:
        text=p.read_text()
        for old in forbidden:
            if old in text:errors.append(f'旧参数说明：{p.name}: {old}')
    if project:
        project=Path(project);m=read_json(project/'knowledge-project.json');line=read_json(project/'knowledge-timeline.json')
        if m.get('layout_profile')!=layout.binding() or line.get('layout_profile')!=layout.binding():errors.append('工程不是当前配置版本')
        for file,sha in m.get('generated_sha256',{}).items():
            if not (project/file).is_file() or file_hash(project/file)!=sha:errors.append('工程被编辑或文件变动：'+file)
        if line.get('frame_fit')!=profile['body_images']['mode']:errors.append('正文仍为旧预合成模式')
        for row in line.get('texts',[]):
            text,style=layout.resolved_style(row['role'],row['text'],row['style_reference']['text_color'])
            if style!=row['style_reference']:errors.append('文字参数漂移：'+row['role'])
        for row in line.get('branding_elements',[]):
            if row['role'] in ('album_cover','source_label') and row.get('box_px')!=profile['boxes_px'][row['role']]:errors.append('品牌位置漂移：'+row['role'])
    return {'status':'failed' if errors else 'passed','layout_profile':layout.binding(),'errors':errors,
            'authoritative_file':str(layout.PROFILE),'derived_spec':'memory_only','project':str(project) if project else None}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project-dir',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=check(args.project_dir)
    if args.output:atomic_json(args.output,result)
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(result['status']!='passed')
