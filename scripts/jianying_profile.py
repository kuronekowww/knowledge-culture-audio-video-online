"""Bind exact local fonts after API save, then validate the downloaded draft.

The HTTP API accepts catalogue font names only and silently falls back. This
local finalizer uses actual font files; it must run before draft verification.
"""
import json
from pathlib import Path
from artifacts import atomic_json, file_hash
import layout_profile


def finalize(folder,line,assets):
    from team_fonts import resolve_all
    folder=Path(folder);layout_profile.check_assets()
    local_fonts=resolve_all()
    if line.get('layout_profile')!=layout_profile.binding():raise ValueError('草稿布局绑定过期')
    expected={}
    for request in line['caption_api']['requests']:
        for cue in json.loads(request['body']['captions']):
            key=(cue['text'],cue['start'],cue['end']-cue['start'])
            expected[key]={**request['body'],**request['verification']}
    font_files={}
    for role,desc in layout_profile.load()['font_files'].items():
        font_files[desc['sha256']]=str(local_fonts[role])
    for name in ('draft_content.json','draft_info.json'):
        path=folder/name
        if not path.is_file():continue
        data=json.loads(path.read_text());materials={m['id']:m for m in data['materials']['texts']}
        from draft_verify import local_media
        media_materials={m['id']:m for m in data['materials'].get('videos',[])}
        rows=list(line['frames'])+[line['album_background']]
        rows += [line[r] for r in ('overlay','outro') if line.get(r)]
        rows += [r for r in line.get('branding_elements',[]) if r['kind']=='image']
        media_expected={(assets[r['asset_id']]['sha256'],r['start_us'],r['end_us']-r['start_us']):r for r in rows}
        for track in data['tracks']:
            if track['type']!='video':continue
            for seg in track['segments']:
                mat=media_materials[seg['material_id']];src=local_media(mat['path'],folder.resolve());t=seg['target_timerange']
                key=(file_hash(src),t['start'],t['duration'])
                if key not in media_expected:
                    # CapCut Mate trims to video-stream length although the same
                    # file's audio runs slightly longer. Restore the full bound outro.
                    out=line.get('outro',{})
                    duration=out.get('end_us',0)-out.get('start_us',0)
                    if (out and key[0]==assets[out['asset_id']]['sha256'] and t['start']==out['start_us']
                            and 0 < duration-t['duration'] < 100000):
                        seg['target_timerange']['duration']=duration
                        seg['source_timerange']['duration']=duration
                        mat['duration']=max(mat.get('duration',0),duration)
                        key=(key[0],t['start'],duration)
                    else:raise ValueError('未知素材片段，不能修改坐标')
                value=layout_profile.media_transform(media_expected[key],assets,line['canvas'])
                seg['clip']['transform']={'x':value['transform_x']/line['canvas']['width'],'y':value['transform_y']/line['canvas']['height']}
                seg['clip']['scale']={'x':value['scale_x'],'y':value['scale_y']}
        count=0
        for track in data['tracks']:
            if track['type']!='text':continue
            for seg in track['segments']:
                mat=materials[seg['material_id']];content=json.loads(mat['content']);t=seg['target_timerange']
                key=(content['text'],t['start'],t['duration'])
                if key not in expected:raise ValueError('未知文字片段，不能套用新模板')
                s=expected[key];px=s['pixel_style'];font=font_files[s['font_sha256']]
                seg['clip']['transform']={'x':s['transform_x']/line['canvas']['width'],'y':s['transform_y']/line['canvas']['height']}
                seg['clip']['scale']={'x':s['scale_x'],'y':s['scale_y']}
                mat.update(font_path=font,font_name=s['font_postscript'],font_title=s['font_postscript'],font_id='',font_resource_id='',font_size=s['font_size'],
                           letter_spacing=s['letter_spacing']*.05,line_spacing=.02+s['line_spacing']*.05,
                           line_max_width=px.get('jianying_max_width_px',px['max_width_px'])/line['canvas']['width'],
                           force_apply_line_max_width=px.get('jianying_force_width',False))
                seg['uniform_scale']={'on':True,'value':1.0}
                for part in content['styles']:
                    part['font']={'id':'','path':font};part['size']=s['font_size']
                    part['strokes']=[{'content':{'solid':{'alpha':px['stroke_alpha'],'color':[0,0,0]}},'width':px['stroke_px']/px['font_size_px']}]
                    if px.get('shadow'):
                        sh=px['shadow'];part['shadows']=[{'alpha':sh['alpha'],'angle':-90,'distance':sh['y_px'],
                            'diffuse':sh['blur_px']/px['font_size_px']/6,'content':{'solid':{'color':[0,0,0]}}}]
                        mat['check_flag']=mat.get('check_flag',15)|32
                mat['content']=json.dumps(content,ensure_ascii=False)
                count+=1
        if count!=len(expected):raise ValueError('需绑定字体的文字数量不一致')
        atomic_json(path,data)
    atomic_json(folder/'knowledge-layout.json',{'layout_profile':layout_profile.binding(),
        'fonts':font_files, 'font_policy':'receiver_local', 'font_sha256':{v:file_hash(Path(v)) for v in font_files.values()},
        'scope':'font binding and deterministic draft parameters; native editor visual review separate'})
