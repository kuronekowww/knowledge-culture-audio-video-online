"""Recover manuscript phrases on real ASR word boundaries, without inventing word times."""
import re
import unicodedata


def clean(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text) if c.isalnum())


def _render_tokens(phrase, tokens):
    """Assign literal source characters/punctuation only to existing, whole audio tokens."""
    characters = []
    for char in phrase:
        if clean(char):
            characters.append([char])
        elif characters:
            characters[-1].append(char)
    if not characters or any(len(clean(c[0])) != 1 for c in characters):
        return None
    index = 0
    result = []
    for token in tokens:
        n = len(clean(token['text']))
        text = ''.join(''.join(row) for row in characters[index:index+n])
        if not n or clean(text) != clean(token['text']):
            return None
        result.append(text)
        index += n
    return result if index==len(characters) else None


def align_phrases(words, references):
    """Match unique contiguous spoken phrases inside their acoustically calibrated source block."""
    output = [dict(w) for w in words]
    positions = {}
    parts = []
    pos = 0
    for i, word in enumerate(words):
        part = clean(word['text'])
        if not part:
            continue
        positions[pos] = i
        parts.append(part)
        pos += len(part)
    positions[pos] = len(words)
    heard = ''.join(parts)
    next_word = 0
    matched_words = 0
    matches = []
    for ref in references:
        for fragment in re.findall(r'[^，,。！？!?；;：:\n]+[，,。！？!?；;：:]*', ref['text']):
            query = clean(fragment)
            if len(query) < 3:
                continue
            loc = heard.find(query)
            if loc < 0 or loc != heard.rfind(query):
                continue
            a, b = positions.get(loc), positions.get(loc+len(query))
            if a is None or b is None or a < next_word or b <= a:
                continue
            # A repeated passage outside the calibrated block must not borrow its wording.
            if words[a]['start_us'] < ref['start_us']-900000 or words[b-1]['end_us'] > ref['end_us']+900000:
                continue
            tokens = words[a:b]
            literal = _render_tokens(fragment.strip(), tokens)
            if literal is None:
                continue
            for i,text in enumerate(literal, a):
                output[i]['text'] = text
            matched_words += len(tokens)
            matches.append({'block_id':ref['block_id'],'start_us':words[a]['start_us'],
                            'end_us':words[b-1]['end_us'],'text':''.join(literal)})
            next_word = b
    return output, {'matched_phrases':len(matches),'matched_words':matched_words,'matches':matches}
