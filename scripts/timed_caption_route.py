"""Choose subtitle timing from actual document blocks and independent speech anchors.

Document times are milliseconds on its own source clock. All returned start/end values
are seconds on the edited body; integer *_us fields are provided for cutting audio.
No word times are manufactured from text length.
"""
from __future__ import annotations

from statistics import median

from content_selection import seconds
from subtitle_segmentation import POLICY, plain

VERSION = 1
MICRO = 1_000_000
ANCHOR_TOLERANCE = .8       # source seconds; robustness to ASR/AI block onset conventions
NO_SHIFT = .40             # leave the original clock intact for sub-second jitter
TINY_OVERLAP_US = 150_000
UNVERIFIED_GAP_US = 1_200_000


def _clips(items, duration):
    cursor = 0.0
    result = []
    for i, raw in enumerate(items, 1):
        a, b, speed = seconds(raw['start']), seconds(raw['end']), seconds(raw.get('speed', 1))
        if a < 0 or b <= a or speed <= 0:
            raise ValueError('选段时间或倍速无效')
        start = seconds(raw.get('output_start', cursor))
        end = seconds(raw.get('output_end', start+seconds(raw.get('out_duration', (b-a)/speed))))
        if abs(start-cursor) > .12 or end <= start or abs((end-start)-(b-a)/speed) > .13:
            raise ValueError('选段的源/成片映射不一致')
        result.append(dict(index=i, a=a, b=b, speed=speed, start=cursor, end=end))
        cursor = end
    if result and duration is not None:
        duration = seconds(duration)
        if abs(duration-cursor) > .15 or duration <= result[-1]['start']:
            raise ValueError('成片音轨时长与剪辑映射不一致')
        result[-1]['end'] = duration
    return result


def _blocks(rows):
    result = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or not isinstance(r.get('text'), str):
            raise ValueError('AI 文稿记录结构无效')
        t = r['text'].strip()
        if not t or t.startswith(('文稿版本：','文稿版本:')):
            continue
        # Both the original RPC and the persistent normalized document are supported.
        a = seconds(r['start_ms'] if 'start_ms' in r else r['startTime'])/1000
        b = seconds(r['end_ms'] if 'end_ms' in r else r['endTime'])/1000
        if a < 0 or b <= a:
            raise ValueError('AI 文稿时间范围无效')
        result.append(dict(id=r.get('id', f'D{i+1:04d}'), a=a,b=b,text=t,
                           flags=r.get('flags',[]), canonical=plain(t)))
    return sorted(result, key=lambda r:r['a'])


def prefer_full_pass(blocks, clips):
    """Avoid three probes when long blocks already occupy >=80% of the edited audio.

    This selects a recognition strategy, never validates a document clock. Count
    only complete, non-ambiguous long blocks. Gaps and clipped edges contribute
    nothing. Also require long blocks to dominate the source document at the
    selected rate, so an uncalibrated cut near a change of granularity is probed.
    """
    if not blocks or not clips:
        return False
    try:
        rows = _blocks(blocks)
        cuts = _clips(clips, None)
    except (TypeError, ValueError, KeyError):
        return False
    if not rows or any('out_of_order' in row['flags'] for row in rows):
        return False
    prev_end = 0.0
    for row in rows:
        if prev_end-row['a'] > TINY_OVERLAP_US/MICRO:
            return False
        prev_end = max(prev_end, row['b'])

    def covered_long(source, speed):
        covered = 0.0
        previous = 0.0
        for row in source:
            span = row['b']-row['a']
            if span/speed > POLICY['max_seconds'] or len(row['canonical']) > POLICY['max_characters']:
                covered += max(0.0,row['b']-max(previous,row['a']))
            previous = max(previous,row['b'])
        return covered

    # Including gaps in both denominators keeps sparse documents out of this shortcut.
    doc_span = max(row['b'] for row in rows)-rows[0]['a']
    if any(covered_long(rows, speed) < .8*doc_span for speed in {c['speed'] for c in cuts}):
        return False
    long_seconds = 0.0
    for c in cuts:
        complete = (r for r in rows if c['a']<=r['a'] and r['b']<=c['b'])
        long_seconds += covered_long(complete,c['speed'])/c['speed']
    return long_seconds >= .8*sum(c['end']-c['start'] for c in cuts)


def _window_ranges(blocks, clip):
    """Three non-overlapping windows, near document starts but spread over the clip."""
    start,end = clip['start'],clip['end']
    span = end-start
    lengths = [b['b']-b['a'] for b in blocks]
    width = min(span/4, min(36, max(14, (median(lengths)/clip['speed'] if lengths else 0)+10)))
    positions = [start+(b['a']-clip['a'])/clip['speed'] for b in blocks]
    ranges = []
    for fraction in (.10,.50,.90):
        target = start+span*fraction
        nearby = [v for v in positions if abs(v-target) <= min(span*.035,width*.35)]
        # Put the expected sentence onset inside recognition, allowing speech on both sides.
        center = min(nearby,key=lambda t:abs(t-target)) if nearby else target
        a = max(start,min(end-width,center-width*.4))
        ranges.append((round(a*MICRO),round((a+width)*MICRO)))
    return ranges


def probe_windows(blocks, clips, duration=None):
    parsed=_blocks(blocks)
    if not parsed:
        return []
    return [window for clip in _clips(clips,duration) for window in _window_ranges(parsed,clip)]


def _evidence(probes):
    if isinstance(probes, dict):
        if probes.get('source')=='unavailable':
            return []
        windows = probes.get('windows',[])
        return [(w['start_us'],w['end_us'],plain(w['text'])) for w in probes.get('words',[])
                if w.get('timing_source','word')=='word' and w['end_us']>w['start_us']
                and any(a<=w['start_us'] and w['end_us']<=b for a,b in windows)]
    # Alternate input for an already acquired recognizer result, cut on the final audio.
    words=[]
    for p in probes or []:
        for row in p.get('segments',[]):
            for w in row.get('words',[]):
                if p['start']<=w['start']<w['end']<=p['end']:
                    words.append((round(w['start']*MICRO),round(w['end']*MICRO),
                                  plain(w.get('word',w.get('text','')))))
    return words


def _phrases(blocks):
    # Match only the onset of a real block, never an invented position within a paragraph.
    # Join with a marker to avoid an apparent match spanning unrelated blocks.
    text='|'.join(block['canonical'] for block in blocks)
    # Count substrings at ALL unique positions, including repeated text within one block.
    candidates=[]
    for b in blocks:
        phrases=[]
        for n in (12,9,7,5):
            q=b['canonical'][:n]
            if len(q)==n and text.find(q)==text.rfind(q):
                phrases.append(q)
        if phrases:
            candidates.append((b,phrases))
    return candidates


def _anchor(window, clip, words, phrases):
    lo,hi=window
    pieces=[];starts={};n=0
    for a,b,t in words:
        if t and lo<=a and b<=hi:
            starts[n]=a/MICRO
            pieces.append(t); n+=len(t)
    heard=''.join(pieces)
    matches=[]
    for block,candidates in phrases:
        for q in candidates:
            at=heard.find(q)
            if at not in starts or at!=heard.rfind(q):
                continue
            audio_time=starts[at]
            source_time=clip['a']+(audio_time-clip['start'])*clip['speed']
            matches.append({'block_id':block['id'],'text':q,'audio_time':audio_time,
                            'offset':round(source_time-block['a'],6)})
            break
    report={'clip_index':clip['index'],'start_us':lo,'end_us':hi,'matched':len(matches),
            'source_offset_seconds':None}
    if not matches:
        return report
    # A one-off unrelated phrase should not pull the offset to another scene/version.
    offsets=[m['offset'] for m in matches]
    center=median(offsets)
    usable=[m for m in matches if abs(m['offset']-center)<=ANCHOR_TOLERANCE]
    if len(usable)*2<=len(matches):
        return report
    report.update(source_offset_seconds=round(median(m['offset'] for m in usable),6),
                  matches=usable)
    return report


def _trusted_zones(anchors,clip):
    """A pair of adjacent agreeing windows brackets a trusted clock.

    If the clock jumps, ASR fills the unbracketed part; no constant offset is
    extrapolated across a disagreement. Leading/trailing source gaps are handled later.
    """
    zones=[]
    for i in range(2):
        first,second=anchors[i:i+2]
        a,b=first['source_offset_seconds'],second['source_offset_seconds']
        if a is None or b is None or abs(a-b)>ANCHOR_TOLERANCE:
            continue
        if not {m['block_id'] for m in first['matches']}.isdisjoint(m['block_id'] for m in second['matches']):
            continue
        # Windows agree. Each end may extend to the cut edge only if its outer probe passed.
        lo=round(clip['start']*MICRO) if i==0 else first['start_us']
        hi=round(clip['end']*MICRO) if i==1 else second['end_us']
        offsets=[a,b]
        if zones:
            # Local pairwise closeness alone must never accept monotonically drifting clocks.
            if max(anchors[j]['source_offset_seconds'] for j in range(3))-min(
                    anchors[j]['source_offset_seconds'] for j in range(3))>ANCHOR_TOLERANCE:
                return []
            lo=zones.pop()[0]
            offsets=[r['source_offset_seconds'] for r in anchors]
        offset=median(offsets)
        zones.append((lo,hi,0.0 if abs(offset)<=NO_SHIFT else round(offset,6)))
    return zones


def _span(a,b):
    return {'start':a/MICRO,'end':b/MICRO,'start_us':a,'end_us':b}


def _region(a,b,clip_index,reasons,refs=()):
    return {**_span(a,b),'clip_index':clip_index,'reasons':list(reasons),'references':list(refs)}


def _word_ranges(ranges):
    for r in ranges:
        if isinstance(r,dict):
            if 'start_us' in r: yield int(r['start_us']),int(r['end_us'])
            else: yield round(seconds(r['start'])*MICRO),round(seconds(r['end'])*MICRO)
        else:
            yield round(seconds(r[0])*MICRO),round(seconds(r[1])*MICRO)


def _process_zone(blocks,clip,zone,forced,words):
    lo,hi,offset=zone
    direct=[];repair=[];mapped=[]
    for b in blocks:
        def project(t):
            return round((clip['start']+(t+offset-clip['a'])/clip['speed'])*MICRO)
        a,bound=project(b['a']),project(b['b'])
        x,y=max(lo,a),min(hi,bound)
        if y<=x: continue
        reasons=set()
        if x!=a or y!=bound: reasons.add('crossed_boundary')
        if (y-x>POLICY['max_seconds']*MICRO or len(b['canonical'])>POLICY['max_characters'] or
                (y-x < POLICY['min_display_ms']*1000) or len(b['canonical'])*MICRO/(y-x)>POLICY['max_characters_per_second']):
            reasons.add('requires_word_alignment')
        if 'out_of_order' in b['flags']:
            reasons.add('out_of_order')
        if any(x<v and u<y for u,v in forced): reasons.add('word_timing_requested')
        mapped.append({'a':x,'b':y,'text':b['text'],'id':b['id'],'reasons':reasons,
                       'partial':x!=a or y!=bound})
    active=[]
    for item in mapped:
        active=[p for p in active if p['b']>item['a']]
        for prev in active:
            overlap=prev['b']-item['a']
            if overlap<=TINY_OVERLAP_US and item['a']-prev['a']>=POLICY['min_display_ms']*1000:
                prev['b']=item['a']
            else:
                item['reasons'].add('substantial_overlap');prev['reasons'].add('substantial_overlap')
        active.append(item)
    cursor=lo
    for item in mapped:
        x,y=item['a'],item['b']
        if x>cursor:
            repair.extend(_gap(cursor,x,clip['index'],forced,words))
        cursor=max(cursor,y)
        ref={**_span(x,y),'text':item['text'],'block_id':item['id'],'partial':item['partial']}
        if item['reasons']:
            repair.append(_region(x,y,clip['index'],sorted(item['reasons']),[ref]))
        else:
            direct.append({**_span(x,y),'text':item['text'],'block_id':item['id'],
                           'clip_index':clip['index'],'timing_source':'document_segment'})
    if cursor<hi:
        repair.extend(_gap(cursor,hi,clip['index'],forced,words))
    return direct,repair


def _gap(a,b,index,forced,words):
    # Large uncovered holes may be alternate intros or inserted speech, not silence.
    if (b-a>=UNVERIFIED_GAP_US or any(a<=x and y<=b for x,y,_ in words)):
        return [_region(a,b,index,['document_gap'])]
    return [_region(max(a,x),min(b,y),index,['word_timing_requested']) for x,y in forced if x<b and a<y]


def _merge(regions):
    merged=[]
    for r in sorted(regions,key=lambda v:(v['clip_index'],v['start_us'])):
        # A hole in the document needs transcription of any audible new words.
        # Don't treat a touching title/intro gap as part of a reference-guided sentence.
        if (merged and merged[-1]['clip_index']==r['clip_index']
                and bool(merged[-1]['references'])==bool(r['references'])
                and r['start_us']<=merged[-1]['end_us']):
            p=merged[-1]
            p.update(_span(p['start_us'],max(p['end_us'],r['end_us'])))
            p['reasons']=sorted(set(p['reasons'])|set(r['reasons']))
            p['references'].extend(r['references'])
        else: merged.append(r)
    for p in merged:
        p['block_ids']=[r['block_id'] for r in p['references']]
        p['reference_text']=[r['text'] for r in p['references']]
    return merged


def plan(blocks, clips, probes, *, word_ranges=(), duration=None):
    """Build direct subtitle rows and disjoint local ASR windows in edited-audio time."""
    source=_blocks(blocks)
    words=sorted(set(_evidence(probes)))
    phrases=_phrases(source)
    force=list(_word_ranges(word_ranges))
    direct=[];align=[];anchors=[];reports=[];verified=False
    for clip in _clips(clips,duration):
        lo,hi=round(clip['start']*MICRO),round(clip['end']*MICRO)
        evidence=[_anchor(w,clip,words,phrases) for w in _window_ranges(source,clip)]
        zones=_trusted_zones(evidence,clip)
        anchors.extend(evidence)
        reports.append({'clip_index':clip['index'],'source_offset_seconds':zones[0][2] if len(zones)==1 else None,
                        'verified_ranges':[{**_span(a,b),'source_offset_seconds':off} for a,b,off in zones]})
        cursor=lo
        for zone in zones:
            a,b,_=zone
            if cursor<a:
                align.append(_region(cursor,a,clip['index'],['unverified_clock']))
            rows,repairs=_process_zone(source,clip,zone,force,words)
            direct.extend(rows);align.extend(repairs)
            cursor=b;verified=True
        if cursor<hi:
            align.append(_region(cursor,hi,clip['index'],['unverified_clock']))
    return {'version':VERSION,'route':'asr' if not verified else ('hybrid' if align else 'document'),
            'direct':direct,'align':_merge(align),'anchors':anchors,'clips':reports}
