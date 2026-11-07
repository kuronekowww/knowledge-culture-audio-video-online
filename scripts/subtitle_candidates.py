"""Document-assisted candidates and explicit delta reviews; no implicit approvals."""
from __future__ import annotations

import copy
import difflib
import unicodedata

from artifacts import atomic_json, digest, read_json


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text) if c.isalnum())


def document_map(units, context):
    blocks = [b for r in context.get('document_ranges', []) for b in r['blocks']]
    if not blocks and context.get('transcript'):
        blocks = [{'id': 'plain', 'text': context['transcript']}]
    doc, owners, ends = '', [], set()
    for index, block in enumerate(blocks):
        for char in block['text']:
            value = normalized(char)
            doc += value
            owners.extend([index] * len(value))
            if char in '，,。！？!?；;：:':
                ends.add(len(doc))
    raw = ''.join(normalized(u['text']) for u in units)
    matcher = difflib.SequenceMatcher(None, doc, raw, autojunk=False)
    mapped = {}
    for match in matcher.get_matching_blocks():
        mapped.update({match.a + i: match.b + i for i in range(match.size)})
    # Require matching characters on BOTH sides; don't create a break inside a mismatch.
    boundaries = {mapped[p] for p in ends if p and p < len(doc) and p - 1 in mapped
                  and p in mapped and mapped[p] == mapped[p - 1] + 1}
    raw_owners = {value: owners[key] for key, value in mapped.items()}
    differences = []
    # Exclude leading/trailing document material outside the final clip.
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag != 'equal' and (c > 0 or d > 0) and (c < len(raw) or d < len(raw)):
            if tag == 'delete' and (c == 0 or c == len(raw)):
                continue
            differences.append({'kind': tag, 'document_text': doc[a:b], 'asr_text': raw[c:d],
                                'asr_char_start': c, 'asr_char_end': d})
    return raw, blocks, boundaries, raw_owners, differences


def prepare_candidates(units, context, fallback, policy, phrases):
    raw, blocks, boundaries, owners, differences = document_map(units, context)
    protected = set()
    for phrase in phrases:
        term = normalized(phrase)
        if not term:
            continue
        at = raw.find(term)
        while at >= 0:
            protected.update(range(at + 1, at + len(term)))
            at = raw.find(term, at + 1)
    # Fallback boundaries remain real ASR punctuation/pause boundaries.
    fallback_ids = {c['unit_ids'][-1] for c in fallback}
    groups, group, offset, since = [], [], 0, 0
    for i, unit in enumerate(units):
        group.append(unit)
        offset += len(normalized(unit['text']))
        is_doc = offset in boundaries
        next_pause = i + 1 < len(units) and units[i + 1]['start_us'] - unit['end_us'] > policy['pause_candidate_ms'] * 1000
        duration = unit['end_us'] - group[0]['start_us']
        # Avoid fragments from document commas; long/unmatched text stays for actual review.
        if offset not in protected and ((is_doc and offset - since >= 3 and duration >= 500_000)
                                        or unit['id'] in fallback_ids or next_pause):
            groups.append((group, since, offset, 'local_document' if is_doc else 'asr_boundary'))
            group, since = [], offset
    if group:
        groups.append((group, since, offset, 'asr_boundary'))
    candidates = []
    for number, (group, start, end, source) in enumerate(groups, 1):
        text = ''.join(u['text'] for u in group).strip()
        while text and (unicodedata.category(text[-1]).startswith('P') or text[-1].isspace()):
            text = text[:-1]
        indexes = {owners[p] for p in range(start, end) if p in owners}
        # Context around the matched paragraphs; hash content, not paragraph ordinal IDs.
        adjacent = sorted({j for i in indexes for j in (i - 1, i, i + 1) if 0 <= j < len(blocks)})
        reference = [blocks[i]['text'] for i in adjacent] if indexes else [context.get('transcript', '')]
        candidates.append({'candidate_id': f'C{number:04d}', 'unit_ids': [u['id'] for u in group],
            'corrected_text': ''.join(u['text'] for u in group).strip(), 'text': text, 'action': 'keep',
            'reason': '', 'evidence': '', 'rule_ids': [], 'needs_listen': False,
            'boundary_hint': source, 'document_block_ids': [blocks[i]['id'] for i in adjacent],
            'document_reference_hash': digest(reference)})
    return candidates, differences


def cache_key(request, cue):
    basis = request['basis']
    # Whole-final-audio identity is intentional: no reuse across cuts/tempo changes.
    namespace = {k: v for k, v in basis.items() if k not in ('context_hash', 'asr_sha256')}
    ids = set(cue['unit_ids'])
    return digest({'basis': namespace, 'units': [u for u in request['units'] if u['id'] in ids],
                   'track': request['context']['track_id'], 'dictionary': request['context']['dictionary'],
                   'reference': cue['document_reference_hash'], 'supplied_transcript':request['context'].get('supplied_transcript',''),
                   'neighbors': [[b['text'] for b in r['neighbors']] for r in request['context'].get('document_ranges', [])]})


def reuse(request, root):
    hits = 0
    for cue in request['suggested_cues']:
        key = cache_key(request, cue)
        path = root / 'subtitle-paragraphs' / f'{key}.json'
        saved = read_json(path, {})
        payload = saved.get('payload', {})
        if saved.get('sha256') != digest(payload) or payload.get('key') != key:
            continue
        row = payload.get('cue', {})
        if row.get('unit_ids') != cue['unit_ids']:
            continue
        cue.update(copy.deepcopy(row))
        cue['verified_cache'] = {'key': key, 'reviewer': payload['reviewer'],
                                 'request_hash': payload['request_hash'],
                                 'protected_phrases': payload.get('protected_phrases', [])}
        hits += 1
    return hits


def save_verified(request, response, root):
    rows = {tuple(row['unit_ids']): row for row in response['cues']}
    for cue in request['suggested_cues']:
        row = rows.get(tuple(cue['unit_ids']))
        # Re-segmented rows are covered by full-review cache; never misapply their ranges.
        if row is None:
            continue
        key = cache_key(request, cue)
        fields = ('unit_ids', 'corrected_text', 'text', 'action', 'reason', 'evidence', 'rule_ids',
                  'needs_listen', 'boundary_reason', 'short_utterance_reason', 'pause_reason', 'alignment_review')
        payload = {'key': key, 'cue': {k: row[k] for k in fields if k in row},
                   'reviewer': response['reviewer'], 'request_hash': request['request_hash'],
                   'protected_phrases': response.get('protected_phrases', [])}
        atomic_json(root / 'subtitle-paragraphs' / f'{key}.json', {'payload': payload, 'sha256': digest(payload)})


def request_hash(request):
    return digest({k: request[k] for k in ('basis', 'units', 'suggested_cues')})


def expand(request, response):
    """Require explicit exhaustive selection. Patches may merge/split adjacent candidates."""
    if 'cues' in response:
        if 'accept' in response or 'patches' in response:
            raise ValueError('cues不能与accept/patches混用')
        return response
    candidates = request['suggested_cues']
    ids = [c['candidate_id'] for c in candidates]
    accept, patches = response.get('accept', []), response.get('patches', [])
    if not isinstance(accept, list) or not isinstance(patches, list):
        raise ValueError('accept和patches必须是数组')
    covered, replacements = set(), {}
    for identifier in accept:
        if not isinstance(identifier, str) or identifier not in ids or identifier in covered:
            raise ValueError('accept包含未知或重复候选编号')
        covered.add(identifier)
        cue = copy.deepcopy(candidates[ids.index(identifier)])
        cue.setdefault('boundary_reason', '审核者已确认该候选语义完整且分句合适')
        replacements[identifier] = [cue]
    for patch in patches:
        if not isinstance(patch, dict):
            raise ValueError('patch必须为对象')
        selected, rows = patch.get('candidate_ids'), patch.get('cues')
        if not isinstance(selected, list) or not selected or any(not isinstance(i, str) or i not in ids or i in covered for i in selected):
            raise ValueError('patch候选编号未知、重复或已确认')
        first = ids.index(selected[0])
        if selected != ids[first:first + len(selected)]:
            raise ValueError('patch必须引用顺序连续的候选')
        if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) for r in rows):
            raise ValueError('patch.cues必须为非空字幕数组')
        expected = [u for c in candidates[first:first + len(selected)] for u in c['unit_ids']]
        if any(not isinstance(r.get('unit_ids'), list) for r in rows) or [u for r in rows for u in r['unit_ids']] != expected:
            raise ValueError('patch必须恰好覆盖所引用候选的全部unit')
        covered.update(selected)
        replacements[selected[0]] = rows
    if covered != set(ids):
        raise ValueError('差异审核必须显式确认或修正所有候选，不允许遗漏即通过')
    result = copy.deepcopy(response)
    result.pop('accept', None)
    result.pop('patches', None)
    result['cues'] = [row for identifier in ids for row in replacements.get(identifier, [])]
    # Previously protected phrases remain protected when a reviewed row is reused.
    protected = set(result.get('protected_phrases', []))
    for identifier in accept:
        protected.update(candidates[ids.index(identifier)].get('verified_cache', {}).get('protected_phrases', []))
    result['protected_phrases'] = sorted(protected)
    return result
