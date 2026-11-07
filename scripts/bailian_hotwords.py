"""Small, explicit name selection from album analysis; no automatic name guessing."""
import shared_album

LIMIT = 8


def validate(dictionary):
    selection = dictionary.get('asr_names')
    if not isinstance(selection, dict) or not all(isinstance(selection.get(k), str) and selection[k].strip()
            for k in ('reviewer', 'evidence')):
        raise ValueError('选剧情时先整理人物热词：词典需asr_names.names/reviewer/evidence；无明确姓名可显式填空数组')
    names = selection.get('names')
    if not isinstance(names, list) or len(names) > LIMIT or any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError('主要人名热词建议3–5个，上限8个，必须为非空姓名字符串')
    if len(set(names)) != len(names): raise ValueError('主要人名热词重复')
    entries = {e['canonical']: e for e in dictionary.get('entries', [])}
    for name in names:
        if name not in entries or not entries[name].get('evidence') or len(name) > 15:
            raise ValueError('热词必须引用有文稿依据且不超过15字的词典canonical')
    return [{'text': name, 'weight': 4, 'lang': 'zh'} for name in names]


def enabled(cfg):
    value=cfg.get('asr',{}).get('use_hotwords',False)
    if not isinstance(value,bool):raise ValueError('asr.use_hotwords须为布尔值true/false')
    return value


def vocabulary(cfg):
    if not enabled(cfg):return []
    return validate(shared_album.dictionary(cfg))


def prompt(cfg):
    return ' '.join(row['text'] for row in vocabulary(cfg))
