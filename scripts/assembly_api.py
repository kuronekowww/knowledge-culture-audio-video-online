"""Compile every editing request offline. The editing Skill only sends these bodies."""
import argparse
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import caption_api
from artifacts import atomic_json, read_json, digest


def compile_timeline(timeline, assets):
    caption_api.validate_presentation(timeline)
    body_end = timeline['body_duration_us']
    canvas = timeline['canvas']
    branding = timeline['branding_enabled']
    if type(branding) is not bool:
        raise ValueError('缺少明确品牌选项')
    if (bool(timeline.get('overlay')) != branding or bool(timeline.get('outro')) != branding
            or any(x['role']=='album' for x in timeline['texts']) != branding
            or any(x['role']=='source' for x in timeline['texts'])
            or bool(timeline.get('branding_elements')) != branding):
        raise ValueError('品牌图片、专辑名、片尾必须与品牌开关一致')
    if timeline['audio']['start_us'] != 0 or timeline['audio']['end_us'] != body_end:
        raise ValueError('正文音轨必须从0开始，封面占用正文首秒，不额外推迟声音或字幕')
    cursor = 0
    for frame in timeline['frames']:
        if frame['start_us'] != cursor or frame['end_us'] <= cursor:
            raise ValueError('图片时间线有空档或重叠')
        cursor = frame['end_us']
    if cursor != body_end or timeline['frames'][0]['end_us'] != min(1000000,body_end):
        raise ValueError('封面应为正文首1秒，全部画面连续覆盖正文')
    expected_end = body_end
    if branding:
        outro = timeline['outro']
        if (outro['start_us'] != body_end or outro['end_us'] != body_end+__import__('layout_profile').load()['outro']['duration_us']
                or outro['source_start_us'] != 0 or outro['volume'] != 1
                or not outro['preserve_original_audio']):
            raise ValueError('品牌片尾须紧接正文，源起点0，完整素材时长且保留100%音量')
        if (timeline['overlay']['start_us'],timeline['overlay']['end_us']) != (0,outro['end_us']):
            raise ValueError('品牌贴片必须覆盖正文')
        expected_end = outro['end_us']
    if timeline['total_duration_us'] != expected_end:
        raise ValueError('总时长不匹配')

    def asset(aid, kind):
        row = assets[aid]
        if row['kind'] != kind or urlsplit(row['url']).scheme not in ('http','https'):
            raise ValueError('素材类型或URL错误：'+aid)
        return row['url']

    steps = []
    def add(key, endpoint, body, ids=()):
        steps.append({'step_id':key,'endpoint':endpoint,'method':'POST','body':body,'asset_ids':list(ids)})
    def serial(items): return json.dumps(items,ensure_ascii=False,separators=(',',':'))
    transform = {'alpha':1.0,'scale_x':1.0,'scale_y':1.0,'transform_x':0,'transform_y':0}
    create_body={'width':canvas['width'],'height':canvas['height']}
    name_field=timeline.get('create_draft_name_field')
    if name_field is not None:
        if name_field not in ('draft_name','name') or not str(timeline.get('draft_name','')).strip():
            raise ValueError('草稿名称或服务命名字段无效')
        create_body[name_field]=timeline['draft_name']
    add('create','create_draft',create_body)
    import layout_profile
    background=timeline.get('album_background')
    if not background or 'asset_id' not in background:
        raise ValueError('缺少独立专辑底图；先按当前模板重新prepare')
    placement=layout_profile.api_media_transform(background,assets,canvas)
    add('background','add_images',{**placement,'image_infos':serial([{'image_url':asset(background['asset_id'],'image'),'start':background['start_us'],'end':background['end_us']}])},[background['asset_id']])
    # Group only adjacent frames with identical transforms; raw file bytes stay unchanged.
    import request_chunks
    groups=[]
    for f in timeline['frames']:
        placement=layout_profile.api_media_transform(f,assets,canvas)
        if not groups or groups[-1][0]!=placement:groups.append((placement,[]))
        groups[-1][1].append(f)
    index=0
    for placement,items in groups:
        rows=[{'image_url':asset(f['asset_id'],'image'),'start':f['start_us'],'end':f['end_us']} for f in items]
        offset=0
        for body in request_chunks.partition(rows,'image_infos',placement):
            count=len(json.loads(body['image_infos']))
            add('frames-'+str(index),'add_images',body,[f['asset_id'] for f in items[offset:offset+count]])
            offset+=count;index+=1
    audio = timeline['audio']
    add('audio','add_audios',{'audio_infos':serial([{'audio_url':asset(audio['asset_id'],'audio'),
        'start':0,'end':body_end,'volume':audio['volume']}])},[audio['asset_id']])
    if branding:
        overlay = timeline['overlay']
        add('overlay','add_images',{**transform,'image_infos':serial([{
            'image_url':asset(overlay['asset_id'],'image'),'start':0,'end':overlay['end_us']}])},[overlay['asset_id']])
        elements=timeline.get('branding_elements',[])
        expected_roles=['playback','album_cover','source_label'] if timeline.get('album_cover_available',True) else ['playback','source_label']
        if [e['role'] for e in elements]!=expected_roles:raise ValueError('品牌图层不完整或顺序错误；只有明确无真实封面才略过album_cover')
        for element in elements:
            style=element['style_reference']
            placement={'alpha':style['alpha'],'scale_x':style['scale_x'],'scale_y':style['scale_y'],'transform_x':style['x'],'transform_y':style['y']}
            if element.get('box_px'):placement=layout_profile.api_media_transform(element,assets,canvas)
            if element['role'] in ('playback','album_cover'):
                if element['role']=='playback' and (element['kind']!='image' or element['asset_id']!='portrait__playback'):
                    raise ValueError('请用当前版重新准备单帧唱片素材')
                add(element['role'].replace('_','-'),'add_images',{**placement,'image_infos':serial([{'image_url':asset(element['asset_id'],'image'),'start':element['start_us'],'end':body_end}])},[element['asset_id']])
            elif element['role']=='source_label':
                add('source-label','add_images',{**placement,'image_infos':serial([{'image_url':asset(element['asset_id'],'image'),'start':element['start_us'],'end':body_end}])},[element['asset_id']])
        add('outro','add_videos',{**layout_profile.api_media_transform(outro,assets,canvas),'video_infos':serial([{
            'video_url':asset(outro['asset_id'],'video'),'start':body_end,'end':expected_end,
            'duration':outro['end_us']-outro['start_us'],'volume':1.0}])},[outro['asset_id']])
    compiled = caption_api.compile_timeline(timeline)
    if timeline.get('caption_api') != compiled:
        raise ValueError('文字参数过期')
    for index, request in enumerate(compiled['requests']):
        add('text-'+str(index),'add_captions',request['body'])
    add('save','save_draft',{})
    return {'schema':'knowledge-assembly-api/v1','profile':caption_api.PROFILE,
            'steps':steps,'sequence_hash':digest(steps),
            'draft_url_binding':'latest_successful_response_verbatim',
            'post_save_required':'draft_verify.py; saved_unverified until passed'}


def request_body(timeline, assets, step_index, draft_url=None):
    sequence = compile_timeline(timeline, assets)
    if timeline.get('api_sequence') != sequence:
        raise ValueError('请求序列与交接不一致，请重新publish')
    if not 0 <= step_index < len(sequence['steps']):
        raise ValueError('step-index越界（从0开始）')
    step = sequence['steps'][step_index]
    body = copy.deepcopy(step['body'])
    if step['endpoint'] != 'create_draft':
        p = urlsplit(draft_url or '')
        if p.scheme not in ('http','https') or not p.netloc or not parse_qs(p.query).get('draft_id'):
            raise ValueError('必须提供本草稿最新成功响应中的完整draft_url')
        body['draft_url'] = draft_url
    return step['endpoint'],body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir',required=True,type=Path)
    parser.add_argument('--orientation',required=True,choices=['portrait'])
    parser.add_argument('--step-index',required=True,type=int)
    parser.add_argument('--draft-url')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    try:
        import handoff
        job=args.job_dir.resolve()
        value=handoff.validate(job,read_json(job/'config.json'))
        line=next(t for t in value['timelines'] if t['orientation']==args.orientation)
        endpoint,body=request_body(line,value['assets'],args.step_index,args.draft_url)
        atomic_json(args.output,body)
        print(json.dumps({'endpoint':endpoint,'request_file':str(args.output)},ensure_ascii=False))
        return 0
    except (ValueError,OSError,KeyError,TypeError,StopIteration) as exc:
        print('请求未生成：'+str(exc));return 1


if __name__=='__main__':raise SystemExit(main())
