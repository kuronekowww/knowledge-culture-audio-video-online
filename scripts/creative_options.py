"""One user choice per batch; compile the chosen style into every image prompt."""
import copy
import shared_album
from artifacts import atomic_json, digest, read_json, file_lock


def selected(cfg):
    options = cfg.get('creative_options', {})
    if not isinstance(options,dict):raise ValueError('creative_options必须为对象')
    branding=options.get('branding_enabled',True)
    if type(branding) is not bool:
        raise ValueError('branding_enabled必须为布尔值；默认开启，只有用户明确关闭才设false')
    candidates = options.get('style_candidates')
    if not isinstance(candidates, list) or len(candidates) not in (1, 3):
        raise ValueError('请提供三种候选，或用户已明确指定的一种画风')
    ids = set()
    for item in candidates:
        if not isinstance(item,dict):raise ValueError('画风候选必须为对象')
        for field in ('id', 'name', 'reason', 'prompt'):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError('每个画风候选需要id/name/reason/prompt')
        if item['id'] in ids:
            raise ValueError('画风候选ID重复')
        ids.add(item['id'])
    if not isinstance(options.get('album_tone'), str) or not options['album_tone'].strip():
        raise ValueError('需要依据专辑文稿概括album_tone')
    found = next((x for x in candidates if x['id'] == options.get('style_id')), None)
    if found is None:
        raise ValueError('等待用户从三种画风中选择；不要由Agent代选')
    return {'branding_enabled': branding, 'style': found,
            'album_tone': options['album_tone']}


def style_hash(cfg):
    options = cfg.get('creative_options', {})
    found = next((x for x in options.get('style_candidates', [])
                  if x.get('id') == options.get('style_id')), None)
    return digest(found)


def bind_prompts(job, cfg):
    choice = selected(cfg)
    path = job/'image_manifest.json'
    value = read_json(path)
    prefix = '【统一画风（用户选择）】' + choice['style']['prompt'].strip() + '\n'
    old_prefix = value.get('style_prefix', '')
    old_suffix=value.get('cover_title_suffix','')
    import album_visuals
    copy_text=read_json(job/'copy.json')
    full_title=album_visuals.title(copy_text)
    saved=album_visuals.cover_design_record(cfg)
    design=saved.get('design',{})
    core=copy_text.get('album_core_label') or design.get('core_label')
    if not isinstance(core,str) or not core.strip():
        raise ValueError('封面需要copy.album_core_label或专辑设计档案core_label，依据真实专辑确定核心人物/书籍/事件')
    core=core.strip()
    import cover_policy
    suffix=cover_policy.prompt(copy_text,core,design)
    for row in value['images']:
        prompt = row['prompt']
        if old_prefix and prompt.startswith(old_prefix):
            prompt = prompt[len(old_prefix):]
        if not prompt.strip():
            raise ValueError('镜头描述不能为空')
        if old_suffix and prompt.endswith(old_suffix): prompt=prompt[:-len(old_suffix)]
        row['prompt'] = prefix + prompt + (suffix if row['id']=='cover_portrait' else '')
        if row['id']=='cover_portrait':row['size']='1080x1920'
    value.update(style_prefix=prefix, style_selection_hash=style_hash(cfg),cover_title_suffix=suffix,cover_title_hash=digest({'title':full_title,'lines':copy_text['cover_title_lines']}))
    value['cover_policy']=cover_policy.binding()
    value['cover_core_label']=core
    value['cover_design_version']=saved.get('version')
    if value != read_json(path):
        atomic_json(path, value)
    bible = read_json(job/'visual_bible.json', {})
    chosen = {**bible, 'selected_style': choice['style'], 'style_selection_hash': style_hash(cfg)}
    if chosen != bible:
        atomic_json(job/'visual_bible.json', chosen)


def check_bound(job, cfg):
    choice = selected(cfg)
    value = read_json(job/'image_manifest.json', {})
    prefix = '【统一画风（用户选择）】' + choice['style']['prompt'].strip() + '\n'
    import album_visuals
    copy_text=read_json(job/'copy.json')
    title_hash=digest({'title':album_visuals.title(copy_text),'lines':copy_text['cover_title_lines']})
    saved=album_visuals.cover_design_record(cfg)
    core=copy_text.get('album_core_label') or saved.get('design',{}).get('core_label')
    import cover_policy
    if (value.get('cover_policy')!=cover_policy.binding()
            or value.get('cover_title_hash')!=title_hash or value.get('style_selection_hash') != style_hash(cfg)
            or not core or value.get('cover_core_label')!=core
            or value.get('style_prefix') != prefix
            or not value.get('images')
            or any(not row.get('prompt', '').startswith(prefix) for row in value['images'])):
        raise ValueError('画风未绑定当前生图提示词，请resume完成分镜与图片版本更新')
    return choice


def resolve_album(cfg):
    """Reuse confirmed album style; never silently overwrite another confirmed style."""
    cfg = copy.deepcopy(cfg)
    if cfg.get('content_wordlist_mode','contextual') not in {'contextual','strict'}:
        raise ValueError('content_wordlist_mode须为contextual或strict')
    path = shared_album.album_root(cfg)/'visual_style.json'
    with file_lock(path.with_suffix('.lock')):
        saved = read_json(path, {})
        if saved and saved.get('selection_hash') != digest(saved.get('creative_options')):
            raise ValueError('专辑画风档案损坏，请恢复真实确认记录')
        supplied = cfg.get('creative_options', {})
        has_style = isinstance(supplied, dict) and bool(supplied.get('style_id'))
        if not has_style:
            if saved:
                options = copy.deepcopy(saved['creative_options'])
                if type(supplied.get('branding_enabled')) is bool:
                    options['branding_enabled'] = supplied['branding_enabled']
                cfg['creative_options'] = options
                cfg['album_style_version'] = saved['version']
            return cfg
        selected(cfg)
        confirmation = supplied.get('style_confirmation', {})
        same = bool(saved and style_hash(cfg) == style_hash({'creative_options':saved['creative_options']}))
        if not saved or not same:
            if confirmation.get('confirmed_by') != 'user' or not str(confirmation.get('evidence','')).strip():
                raise ValueError('首次画风或更换专辑画风需用户明确选择，记录style_confirmation；不得由Agent代选')
            if saved and supplied.get('replaces_style_version') != saved['version']:
                # Existing jobs keep their pinned, historically confirmed style.
                historical = read_json(path.parent/'visual_style_history'/f"{cfg.get('album_style_version')}.json", {})
                if historical and style_hash(cfg) == style_hash({'creative_options':historical.get('creative_options',{})}):
                    return cfg
                raise ValueError('更换画风须引用当前replaces_style_version，不能静默改动专辑风格')
            version = saved.get('version',0)+1
            if saved: atomic_json(path.parent/'visual_style_history'/f"{saved['version']}.json", saved)
            record = {'schema':1,'album_id':str(cfg['album_id']),'version':version,
                      'creative_options':supplied,'selection_hash':digest(supplied)}
            atomic_json(path, record)
            cfg['album_style_version'] = version
        else:
            cfg['album_style_version'] = saved['version']
    return cfg
