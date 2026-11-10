"""Delivery intent is independent of image/web presentation and editing backend."""
def requested(cfg):
    import config
    from artifacts import read_json
    value = cfg.get('delivery', {})
    if not isinstance(value, dict): raise ValueError('delivery须为对象')
    target = value.get('target', read_json(config.SPEC_PATH)['delivery_policy']['default_target'])
    if target not in ('mp4', 'preview', 'project'):
        raise ValueError('delivery.target须为mp4、preview或project')
    if target in ('mp4', 'preview') and not str(value.get('choice_evidence', '')).strip():
        raise ValueError('渲染MP4或仅预览须有本次明确请求或沿用记录delivery.choice_evidence；默认只交可编辑草稿')
    return {**value, 'target':target, 'editable_project':True,
            'default_applied':'target' not in value}
