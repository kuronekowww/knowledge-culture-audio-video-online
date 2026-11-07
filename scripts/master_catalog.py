"""Import a supplied master table once; resolve only the requested audio and cover."""
import argparse
import csv
import io
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit, urljoin
import zipfile
import xml.etree.ElementTree as ET
import requests
import config
from artifacts import atomic_json, file_hash, digest, read_json, file_lock, atomic_copy

ALIASES={
 'album_id':['album_id','albumId','专辑ID','专辑编号'],
 'album_name':['album_name','albumName','专辑名称','专辑名'],
 'track_id':['track_id','trackId','声音ID','音频ID','声音编号'],
 'track_name':['track_name','trackName','声音名称','声音名','音频名称','声音标题'],
 'audio_url':['audio_url','track_play_path','play_url','声音下载地址','音频下载地址','声音地址','音频地址','下载地址'],
 'album_cover_url':['album_cover_url','album_cover_path','cover_url','cover_path','专辑封面地址','专辑封面URL','专辑封面','封面地址'],
 'transcript':['transcript','文稿','逐字稿','声音文稿']}


def normalize(value):return re.sub(r'[\s_\-]','',str(value)).lower()


def identifier(value,name):
    if isinstance(value,bool):raise ValueError(name+'不能是布尔值')
    if isinstance(value,float):
        if not value.is_integer() or abs(value)>2**53:raise ValueError(name+'存在精度风险，请保存为文本数字')
        value=int(value)
    value=str(value).strip()
    if not value.isascii() or not value.isdigit():raise ValueError(name+'必须是完整数字，不接受科学计数或小数文本')
    return value


def media_url(raw,base):
    value=str(raw or '').strip()
    if not value:return None
    parsed=urlsplit(value)
    if parsed.scheme:
        url=value
    else:
        if value.startswith('//'):raise ValueError('相对素材地址不可切换主机')
        url=urljoin(base.rstrip('/')+'/',value.lstrip('/'))
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.fragment:
        raise ValueError('素材地址必须为HTTP(S)完整地址或基于统一前缀的路径')
    return url


def xlsx_rows(path,sheet=None):
    """Read simple cell values from XLSX without executing formulas/macros."""
    ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    relns='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    with zipfile.ZipFile(path) as z:
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(si.itertext()) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns)]
        workbook=ET.fromstring(z.read('xl/workbook.xml'))
        sheets=workbook.findall('m:sheets/m:sheet',ns)
        item=next((s for s in sheets if s.get('name')==sheet),None) if sheet else (sheets[0] if sheets else None)
        if item is None:raise ValueError('XLSX工作表不存在')
        relationships={r.get('Id'):r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        target=relationships[item.get(relns)]
        member=target.lstrip('/') if target.startswith('/') else 'xl/'+target
        if '..' in Path(member).parts:raise ValueError('XLSX工作表路径无效')
        rows=[]
        for row in ET.fromstring(z.read(member)).findall('m:sheetData/m:row',ns):
            cells={}
            for cell in row.findall('m:c',ns):
                ref=cell.get('r','');letters=re.match(r'[A-Z]+',ref)
                if not letters:continue
                col=0
                for ch in letters[0]:col=col*26+ord(ch)-64
                if cell.find('m:f',ns) is not None:raise ValueError('总表不能含公式单元格，请先另存为值')
                value=cell.findtext('m:v','',ns)
                if cell.get('t')=='s':value=strings[int(value)]
                elif cell.get('t')=='inlineStr':value=''.join(cell.find('m:is',ns).itertext())
                cells[col-1]=value
            if cells:rows.append([cells.get(i,'') for i in range(max(cells)+1)])
        return rows


def read_table(path,sheet=None):
    path=Path(path)
    if path.suffix.lower()=='.xlsx':rows=xlsx_rows(path,sheet)
    elif path.suffix.lower() in ('.csv','.tsv'):
        data=path.read_bytes()
        try:text=data.decode('utf-8-sig')
        except UnicodeDecodeError:text=data.decode('gb18030')
        delimiter='\t' if path.suffix.lower()=='.tsv' else ','
        rows=list(csv.reader(io.StringIO(text),delimiter=delimiter))
    else:raise ValueError('总表支持CSV、TSV或XLSX')
    rows=[row for row in rows if any(str(x).strip() for x in row)]
    if len(rows)<2:raise ValueError('总表缺少表头或数据行')
    aliases={normalize(alias):key for key,names in ALIASES.items() for alias in names}
    columns={}
    for i,title in enumerate(rows[0]):
        key=aliases.get(normalize(title))
        if key:
            if key in columns:raise ValueError('重复映射总表字段：'+key)
            columns[key]=i
    for key in ('album_id','album_name','track_id','audio_url','album_cover_url'):
        if key not in columns:raise ValueError('总表缺少列：'+key)
    return [{key:(row[i] if i<len(row) else '') for key,i in columns.items()} for row in rows[1:]]


def import_table(path,output,media_base=None,sheet=None):
    path=Path(path).resolve();output=Path(output).resolve()
    base=media_base or config.AUDIO_CDN_BASE
    if not media_url('probe',base):raise ValueError('统一素材前缀无效')
    items=[];ids=set();albums={}
    for index,row in enumerate(read_table(path,sheet),2):
        album=identifier(row['album_id'] or (items[-1]['album_id'] if items else ''),'album_id');track=identifier(row['track_id'],'track_id')
        inherited=albums.get(album,('',None))
        name=str(row['album_name'] or inherited[0]).strip()
        if not name:raise ValueError(f'第{index}行专辑名称为空')
        if track in ids:raise ValueError('总表声音ID重复，无法精确定位：'+track)
        audio=media_url(row['audio_url'],base);cover=media_url(row.get('album_cover_url'),base) or inherited[1]
        if not audio:raise ValueError(f'第{index}行缺少声音地址')
        signature=(name,cover)
        if album in albums and albums[album]!=signature:raise ValueError('同专辑名称或封面地址冲突：'+album)
        albums[album]=signature;ids.add(track)
        item={'album_id':album,'album_name':name,'track_id':track,'track_name':str(row.get('track_name') or track).strip(),
              'audio_url':audio,'album_cover_url':cover,'transcript':str(row.get('transcript') or '').strip()}
        items.append(item)
    result={'schema':'knowledge-master-catalog/v1','source_name':path.name,'source_sha256':file_hash(path),'media_base':base,'sheet':sheet,'rows':items}
    with file_lock(output.with_suffix('.lock')):
        previous=read_json(output,{})
        if previous and previous!=result:atomic_json(output.parent/'catalog-history'/(digest(previous)+'.json'),previous)
        atomic_json(output,result)
    return {'status':'catalog_imported','catalog':str(output),'albums':len(albums),'tracks':len(items),'downloads_started':False}


def lookup(path,track_id):
    value=read_json(Path(path));track=identifier(track_id,'track_id')
    if value.get('schema')!='knowledge-master-catalog/v1':raise ValueError('不是有效的标准总表')
    found=[row for row in value['rows'] if row['track_id']==track]
    if len(found)!=1:raise ValueError('声音ID未找到或不唯一：'+track)
    return found[0]


def selected_row(job,cfg):
    binding=cfg.get('master_catalog',{})
    row=binding.get('selected_row')
    if row is None:row=lookup(binding['path'],binding['track_id'])
    elif digest(row)!=binding.get('row_sha256'):raise ValueError('总表选中行快照损坏')
    if row['album_id']!=str(cfg['album_id']) or row['track_id']!=str(binding['track_id']):raise ValueError('总表声音/专辑身份不匹配')
    return row


def configure(job,cfg,path,track_id):
    row=lookup(path,track_id);job=Path(job).resolve()
    if cfg.get('album_id') and str(cfg['album_id'])!=row['album_id']:raise ValueError('不可把已存在job绑定到另一个专辑')
    plan=read_json(job/'cut_plan.json',{})
    if plan.get('track_id') and str(plan['track_id'])!=row['track_id']:raise ValueError('不可替换已有选段的声音ID')
    value={**cfg,'album_id':row['album_id'],'album_name':row['album_name'],'requested_track_id':row['track_id'],
           'master_catalog':{'path':str(Path(path).resolve()),'track_id':row['track_id'],'row_sha256':digest(row),'selected_row':row}}
    atomic_json(job/'config.json',value);return value


def prepare_cover(job,cfg):
    row=selected_row(job,cfg);url=row.get('album_cover_url')
    if not url:return cfg
    import shared_album,generate_images
    folder=shared_album.album_root(cfg)/'catalog_media/covers';key=digest(url)
    receipt=folder/(key+'.json')
    with file_lock(folder/(key+'.lock')):
        previous=read_json(receipt,{})
        suffix=previous.get('suffix','.png')
        if suffix not in ('.png','.jpg','.webp','.gif'):raise ValueError('封面缓存格式无效')
        image=folder/(key+suffix)
        if not image.is_file() or file_hash(image)!=previous.get('sha256'):
            folder.mkdir(parents=True,exist_ok=True)
            with requests.get(url,stream=True,timeout=(15,120),allow_redirects=False) as response:
                if response.is_redirect:raise ValueError('专辑封面地址发生重定向，请更新总表为实际地址')
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(dir=folder,delete=False) as stream:
                    temporary=Path(stream.name)
                    try:
                        for chunk in response.iter_content(262144):
                            if chunk:stream.write(chunk)
                        stream.flush();generate_images.verify_image(temporary)
                        with generate_images.Image.open(temporary) as decoded:
                            suffix={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp','GIF':'.gif'}.get(decoded.format)
                        if not suffix:raise ValueError('封面需为PNG/JPEG/WEBP/GIF')
                        image=folder/(key+suffix);temporary.replace(image)
                    finally:temporary.unlink(missing_ok=True)
            atomic_json(receipt,{'url_sha256':key,'sha256':file_hash(image),'suffix':suffix})
        else:generate_images.verify_image(image)
    value={**cfg,'album_visuals':{**cfg.get('album_visuals',{}),'reference_mode':'user_image','reference_image':str(image)}}
    atomic_json(Path(job)/'config.json',value);return value


def input_row(job,cfg):
    row=selected_row(job,cfg)
    return {**row,'track_play_path':row['audio_url']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    imp=sub.add_parser('import');imp.add_argument('--input',type=Path,required=True);imp.add_argument('--output',type=Path,default=config.SHARED_ROOT/'master-catalog.json');imp.add_argument('--media-base');imp.add_argument('--sheet')
    sel=sub.add_parser('select');sel.add_argument('--catalog',type=Path,required=True);sel.add_argument('--track-id',required=True);sel.add_argument('--job-dir',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=='import':result=import_table(args.input,args.output,args.media_base,args.sheet)
        else:
            args.job_dir.mkdir(parents=True,exist_ok=True);cfg=configure(args.job_dir,read_json(args.job_dir/'config.json',{}),args.catalog,args.track_id)
            row=selected_row(args.job_dir,cfg);result={'status':'track_selected','album_id':row['album_id'],'track_id':row['track_id'],'job':str(args.job_dir)}
        print(json.dumps(result,ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError,ET.ParseError,zipfile.BadZipFile) as exc:
        print(json.dumps({'status':'needs_attention','error_type':type(exc).__name__,'message':str(exc)[:300]},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
