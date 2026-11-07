"""Observable segmentation checks; semantic boundaries remain the AI's job."""
import re
import unicodedata
from difflib import SequenceMatcher

POLICY = {'version': 1, 'target_seconds': [1, 3], 'max_seconds': 4,
          'min_display_ms': 500, 'max_characters': 22, 'max_characters_per_second': 14,
          'pause_candidate_ms': 500, 'internal_pause_ms': 800}
# A small conservative guard list, supplemented by this job's names and phrases.
COMMON_PHRASES = ('使用', '工作', '同志', '如何', '多少', '效率', '保持', '沉默',
                  '组织', '没有', '知道', '因为', '所以', '但是', '否则', '不得不',
                  '一个', '这番话', '这一番话', '告诉', '回答', '物资', '阴险')
SHORT_UTTERANCES = set('好是对嗯啊哦喂不行别走来哎唉咦谁啥滚停')


def plain(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text) if c.isalnum())


def protected_phrases(context, response):
    extra = response.get('protected_phrases', [])
    if not isinstance(extra, list) or any(not isinstance(x, str) or len(plain(x)) < 2 for x in extra):
        raise ValueError('protected_phrases须为至少两字的专名/词语数组，不要填写整句')
    names = []
    for entry in context.get('dictionary', {}).get('entries', []):
        names.extend([entry['canonical'], *entry.get('aliases', [])])
    return sorted({plain(x) for x in (*COMMON_PHRASES, *names, *extra) if len(plain(x)) >= 2})


def _issue(cue, code, detail, kind='segmentation'):
    return {'cue_id': cue['id'], 'start_us': cue['start_us'], 'end_us': cue['end_us'],
            'kind': kind, 'code': code, 'issues': [detail], 'text': cue['text']}


def has_note(row, key):
    value = row.get(key)
    return isinstance(value, str) and bool(value.strip())


def check(cues, rows, unit_map, phrases):
    issues = []
    for cue, row in zip(cues, rows):
        if cue['action'] == 'omit':
            continue
        units = [unit_map[x] for x in cue['unit_ids']]
        seconds = (cue['end_us'] - cue['start_us']) / 1_000_000
        text = plain(cue['text'])
        short_ok = text in SHORT_UTTERANCES and has_note(row, 'short_utterance_reason')
        if seconds < POLICY['min_display_ms'] / 1000 and not short_ok:
            issues.append(_issue(cue, 'flash_cue', '不足0.5秒，重新选择语义边界；独立短答须说明依据'))
        if len(text) == 1 and not short_ok:
            issues.append(_issue(cue, 'orphan_character', '孤立单字，检查是否拆开词语或句尾助词'))
        if seconds > POLICY['max_seconds']:
            kind = 'alignment' if len(units) == 1 else 'segmentation'
            issues.append(_issue(cue, 'long_cue', '超过4秒，按语义及已有边界拆分；无内部边界时重新对齐', kind))
        if len(text) > POLICY['max_characters']:
            issues.append(_issue(cue, 'long_line', '单行超过22字，按短语拆分并预览实际字体行宽'))
        if seconds > 0 and len(text) / seconds > POLICY['max_characters_per_second'] and not short_ok:
            issues.append(_issue(cue, 'reading_speed', '超过14字/秒，检查拥挤或错位，不得自行拉长时码'))
        if not has_note(row, 'boundary_reason'):
            issues.append(_issue(cue, 'missing_boundary_reason', '填写本句语义结束/分句处或独立语气的断句依据'))
        gaps = [b['start_us'] - a['end_us'] for a, b in zip(units, units[1:])]
        if any(g > POLICY['internal_pause_ms'] * 1000 for g in gaps) and not has_note(row, 'pause_reason'):
            issues.append(_issue(cue, 'internal_pause', '跨越0.8秒以上停顿，检查句间合并或文字错位；自然句内停顿需说明'))
        raw, corrected = plain(cue['raw_text']), plain(cue['corrected_text'])
        if raw != corrected:
            review = row.get('alignment_review', {})
            if not isinstance(review, dict):
                raise ValueError('alignment_review须为对象')
            mode, evidence = review.get('mode'), review.get('evidence', '')
            same_span = (mode == 'same_span' and len(raw) == len(corrected)
                         and SequenceMatcher(None, raw, corrected, autojunk=False).ratio() >= 0.5)
            checked = mode == 'audio_checked'
            if not (isinstance(evidence, str) and evidence.strip() and (same_span or checked)):
                issues.append(_issue(cue, 'text_audio_alignment',
                    '校正文改变原识别内容：等长局部同音替换需same_span依据；增删/大改需真实音频核验或重新对齐，不能仅引用文稿字符位置', 'alignment'))

    # Find protected words crossing any number of nearby cues, in both layers.
    groups, group = [], []
    for cue in cues:
        if cue['action'] == 'omit' or (group and cue['start_us'] - group[-1]['end_us'] > 1_000_000):
            if group:
                groups.append(group)
            group = []
        if cue['action'] != 'omit':
            group.append(cue)
    if group:
        groups.append(group)
    for group in groups:
        for field in ('corrected_text', 'text'):
            joined, ends = '', []
            for cue in group:
                joined += plain(cue[field])
                ends.append(len(joined))
            for phrase in phrases:
                for match in re.finditer('(?=' + re.escape(phrase) + ')', joined):
                    start, end = match.start(), match.start() + len(phrase)
                    if any(start < edge < end for edge in ends[:-1]):
                        ids = [cue['id'] for cue, left, right in zip(group, [0, *ends[:-1]], ends)
                               if left < end and right > start]
                        issues.append({'cue_ids': ids, 'kind': 'segmentation', 'code': 'split_phrase',
                                       'issues': [f'{field}跨条拆开词语/专名：{phrase}'], 'phrase': phrase})
    return issues
