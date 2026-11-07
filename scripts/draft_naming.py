"""Keep the requested display name separate from the service's unique draft ID."""
import re
from pathlib import Path
from artifacts import read_json,atomic_json


def requested(job,cfg):
    copy=read_json(job/'copy.json');plan=read_json(job/'cut_plan.json')
    album=cfg.get('album_name') or copy.get('album_name');track=str(plan.get('track_id') or cfg.get('requested_track_id') or '')
    title=copy.get('episode_title')
    if not all(isinstance(x,str) and x.strip() for x in (album,title)) or not track.isdigit():
        raise ValueError('草稿命名需要真实专辑名称、声音ID和视频标题')
    return f'{album.strip()}_{track}_{title.strip()}'


def directory_name(name):
    result=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',name).strip(' .') or '剪映草稿'
    while len(result.encode('utf-8'))>220:result=result[:-1]
    return result


def apply_metadata(folder,name):
    folder=Path(folder);meta=folder/'draft_meta_info.json'
    if not meta.is_file():raise ValueError('草稿缺少draft_meta_info.json，无法核验显示名称')
    data=read_json(meta);data['draft_name']=name;atomic_json(meta,data)
    for filename in ('draft_content.json','draft_info.json'):
        path=folder/filename;value=read_json(path)
        if 'name' in value:value['name']=name;atomic_json(path,value)
    if read_json(meta).get('draft_name')!=name:raise ValueError('草稿名称写入失败')
    return meta
