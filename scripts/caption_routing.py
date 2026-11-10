"""Select, bind and preserve the caption time source for the actual final narration."""
from pathlib import Path
import math

from artifacts import atomic_copy, atomic_json, digest, file_hash, read_json

TIMED = 'verified_timed_document'
REPORT = 'caption_source_report.json'


def mode(cfg):
    choice = cfg.get('subtitle_source', 'auto')
    if choice not in ('auto', 'asr'):
        raise ValueError('subtitle_source 只支持 auto 或 asr')
    return choice


def word_ranges(cfg, duration):
    import presentation
    raw = cfg.get('subtitle_word_ranges', [])
    if not isinstance(raw, list):
        raise ValueError('subtitle_word_ranges 须为最终音轨秒数区间数组')
    ranges = []
    if presentation.selected(cfg)['mode'] == 'hyperframes':
        ranges.append((0., min(15., duration)))
    for pair in raw:
        if not isinstance(pair, (tuple,list)) or len(pair) != 2:
            raise ValueError('字幕词级区间需包含开始与结束秒数')
        a,b = pair
        if (not all(type(n) in (float,int) and math.isfinite(n) for n in pair)
                or not 0 <= a < b <= duration):
            raise ValueError('字幕词级区间超出最终音轨')
        ranges.append((a,b))
    return ranges


def recipe(job, cfg, document):
    import asr_backend, presentation, shared_album
    cuts = read_json(job/'cut_result.json')
    probe = {**cfg, 'asr':cfg['caption_probe_asr']} if isinstance(cfg.get('caption_probe_asr'),dict) else cfg
    dictionary=shared_album.dictionary(cfg)
    return digest({'schema':1, 'document':document['blocks_hash'], 'invalid':document.get('invalid_blocks'),
                   'track':document['track_id'], 'clips':cuts['segments'][0]['clips'],
                   'speed':cuts.get('speed',1),'presentation':presentation.selected(cfg)['mode'],
                   'word_ranges':cfg.get('subtitle_word_ranges',[]),'asr':cfg.get('asr',{}),
                   'probe_settings':cfg.get('caption_probe_asr'), 'backend':asr_backend.identity(cfg),
                   'recognition_prompt':asr_backend.prompt(cfg,dictionary),
                   'probe_prompt':asr_backend.prompt(probe,dictionary),
                   'code':[file_hash(Path(__file__).with_name(name)) for name in
                           ('caption_routing.py','timed_audio_alignment.py','timed_caption_route.py','document_word_alignment.py')]})


def current_report(job, cfg, meta):
    import document_context
    if mode(cfg) != 'auto':
        raise ValueError('当前要求全音轨 ASR，不能复用句级时码')
    doc = document_context.caption_document(job, cfg, fetch=False)
    report = read_json(job/REPORT, {})
    if (not doc or meta.get('route_report_hash') != digest(report)
            or report.get('recipe') != recipe(job,cfg,doc)):
        raise ValueError('文稿、剪点、倍速或字幕来源配置变化，需恢复字幕阶段')
    return report


def mapped_reference(context, report, doc):
    """Give review/segmentation the source paragraphs selected after correcting the audio clock."""
    blocks = {b['id']:b for b in doc['blocks']}
    ranges = []
    for index, ids in enumerate(report['reference_groups']):
        refs = [blocks[key] for key in ids if key in blocks]
        if refs:
            ranges.append({'clip_index':index+1,'blocks':refs,'neighbors':[]})
    result = dict(context, document_ranges=ranges)
    if ranges and not result.get('supplied_document'):
        result['transcript'] = '\n'.join(b['text'] for r in ranges for b in r['blocks'])
    return result


def reference_groups(plan, doc):
    rows = plan['direct'] + plan['align']
    groups = {}
    for row in rows:
        ids = row.get('block_ids') or ([row['block_id']] if row.get('block_id') else [])
        groups.setdefault(row.get('clip_index',1), set()).update(ids)
    return [[b['id'] for b in doc['blocks'] if b['id'] in groups[i]] for i in sorted(groups)]


def stitch(plan, audio_evidence, duration_us):
    """Use document ranges only for accepted whole cues; use acoustic words in all repair windows."""
    from subtitle_review import units_from_asr, join_text
    rows = [{'start':r['start_us']/1e6,'end':r['end_us']/1e6,'text':join_text([' '.join(r['text'].split())]),
             'timing_source':'document_segment','block_ids':[r['block_id']]} for r in plan['direct']]
    stats=[]
    observed=audio_evidence.get('by_window', {})
    ordered = sorted(plan['align'], key=lambda item:item['start_us'])
    for window in ordered:
        a,b = window['start_us'],window['end_us']
        local=observed.get((a,b),audio_evidence)
        words=local.get('words', [])
        silence=local.get('silence_windows', [])
        matched = [w for w in words if a <= (w['start_us']+w['end_us'])/2 < b]
        if not matched:
            if any(left<=a and b<=right for left,right in silence):
                stats.append({'start_us':a,'end_us':b,'status':'decoded_silence'})
                continue
            if not window.get('references') and local.get('source') not in (None, 'unavailable'):
                stats.append({'start_us':a,'end_us':b,'status':'no_words_detected',
                              'note':'文稿未覆盖且语音识别未发现词'})
                continue
            raise ValueError('局部对齐缺少有效词时码或可验证的静音依据')
        # A word can cross two adjacent repair windows. It belongs to one window by
        # midpoint, but keeps its acoustic timestamps. Only trim the neighbouring document
        # cue if its coarse clock overlaps a real word by a small boundary tolerance.
        for direct in rows:
            if direct.get('timing_source')!='document_segment':
                continue
            lo,hi=round(direct['start']*1e6),round(direct['end']*1e6)
            if hi<=a and matched[0]['start_us']<hi:
                if hi-matched[0]['start_us']>120_000 or matched[0]['start_us']<=lo:
                    raise ValueError('实测词与前一文稿句边界不符')
                direct['end']=matched[0]['start_us']/1e6
            if lo>=b and matched[-1]['end_us']>lo:
                if matched[-1]['end_us']-lo>120_000 or matched[-1]['end_us']>=hi:
                    raise ValueError('实测词与后一文稿句边界不符')
                direct['start']=matched[-1]['end_us']/1e6
        from document_word_alignment import align_phrases
        matched, aligned=align_phrases(matched,window.get('references',[]))
        stats.append({**aligned,'start_us':a,'end_us':b})
        timed = [{'word':w['text'],'start':w['start_us']/1e6,
                  'end':w['end_us']/1e6} for w in matched]
        rows.append({'start':timed[0]['start'],'end':timed[-1]['end'],
                     'text':''.join(w['word'] for w in timed),'words':timed,
                     'timing_source':'word','block_ids':window.get('block_ids',[])})
    data={'backend':TIMED,'segments':sorted(rows,key=lambda row:row['start'])}
    units_from_asr(data,duration_us)
    return data,stats


def preserve_native(job, audio_sha):
    """Retain a real word-timed file used as evidence before replacing the active pair."""
    raw,meta=job/'final_transcript.json',job/'final_transcript.meta.json'
    data=read_json(meta,{})
    if ((data.get('backend_identity') or {}).get('backend') not in ('bailian_paraformer','funasr','whisper')
            or data.get('audio_sha256')!=audio_sha or not raw.is_file()
            or data.get('transcript_sha256')!=file_hash(raw)):
        return
    dest=job/'timed-native-asr'
    old=read_json(dest/meta.name,{})
    if old.get('transcript_sha256')!=data['transcript_sha256'] and old.get('transcript_sha256'):
        older=dest/'history'/digest(old)
        if (dest/raw.name).is_file() and file_hash(dest/raw.name)==old['transcript_sha256']:
            atomic_copy(dest/raw.name,older/raw.name)
            atomic_json(older/meta.name,old)
    atomic_copy(raw,dest/raw.name)
    atomic_json(dest/meta.name,data)


def native(job,cfg,reason,plan=None):
    import transcription_cache, subtitle_review
    info={'route':'asr','reason':reason,'audio_sha256':file_hash(subtitle_review.audio_path(job))}
    if plan:
        info.update(anchors=plan.get('anchors',[]),clips=plan.get('clips',[]))
    # A failed full-ASR attempt must not overwrite a still verifiable earlier route report.
    atomic_json(job/'caption_source_attempt.json',info)
    error=transcription_cache.transcribe(job,cfg)
    if not error or not (job/'final_transcript.meta.json').is_file():
        atomic_json(job/REPORT,info)
    return error


def restore_cached(job,cfg,saved):
    """Materialize only an already verified word transcript, without entering a paid adapter."""
    import asr_backend, shared_album, subtitle_review
    transcript=Path(saved['transcript_path'])
    if file_hash(transcript)!=saved['transcript_sha256']:
        raise ValueError('读取时识别缓存字节发生变化')
    source=Path(saved['record_path'])
    evidence=read_json(source, {})
    raw=evidence.get('raw_path')
    if raw and evidence.get('raw_sha256'):
        if not Path(raw).is_file() or file_hash(Path(raw))!=evidence['raw_sha256']:
            raise ValueError('识别缓存原始响应校验失败')
        atomic_copy(Path(raw),job/'bailian_asr_raw.json')
    target=job/'final_transcript.json'
    if target.resolve()!=transcript.resolve():
        atomic_copy(transcript,target)
    sha=file_hash(subtitle_review.audio_path(job))
    if sha!=saved['audio_sha256'] or file_hash(target)!=saved['transcript_sha256']:
        raise ValueError('恢复识别缓存时音频或结果改变')
    meta={'schema':1,'request_hash':evidence.get('key') or evidence.get('request_hash'),
          'audio_sha256':sha,'transcript_sha256':saved['transcript_sha256'],
          'asr_settings_hash':digest(cfg.get('asr',{})),'backend_identity':saved['backend_identity'],
          'prompt_hash':digest(asr_backend.prompt(cfg,shared_album.dictionary(cfg)))}
    atomic_json(job/'final_transcript.meta.json',meta)
    atomic_json(job/REPORT,{'route':'asr_cache','audio_sha256':sha,'source_record':str(source),
                            'reason':'复用与最终音轨绑定的完整识别，无新提交'})
    return ''


def acoustic(job,cfg,windows):
    import timed_audio_alignment as audio
    try:
        return audio.acquire(job,cfg,windows)
    except audio.AlignmentPending as exc:
        status={'route':'window_task_pending','windows_us':windows,'message':str(exc),
                'action':'继续原有窗口任务；核实远端回执后恢复，不重发整轨或另开识别'}
        atomic_json(job/'caption_source_attempt.json',status)
        raise


def select_transcript(job, cfg):
    import asr_backend, document_context, subtitle_review, timed_audio_alignment as audio, timed_caption_route as route
    selected = mode(cfg)
    explicit_doc=bool(cfg.get('ai_doc_path'))
    doc=document_context.caption_document(job,cfg,fetch=False) if explicit_doc else None
    meta=read_json(job/'final_transcript.meta.json', {})
    if meta:
        try:
            subtitle_review.current_basis(job,cfg)
        except (KeyError,TypeError,OSError,ValueError):
            pass
        else:
            if meta.get('source_kind')!=TIMED:
                report = read_json(job/REPORT,{})
                if report.get('audio_sha256')!=meta['audio_sha256'] or report.get('route') not in ('asr','asr_cache'):
                    atomic_json(job/REPORT,{'route':'asr_cache','audio_sha256':meta['audio_sha256'],
                                'reason':'当前最终音轨识别已经有效，复用现有结果'})
            return ''
    saved=audio.cached_full(job,cfg,require_current=True)
    if saved:
        return restore_cached(job,cfg,saved)
    if selected=='asr':
        return native(job,cfg,'已指定全音轨 ASR')
    if audio.pending_full(job,cfg):
        return native(job,cfg,'继续同一最终音轨已有的完整识别请求')
    if not explicit_doc:
        doc=document_context.caption_document(job,cfg)
    if not doc or not doc.get('blocks') or doc.get('invalid_blocks'):
        return native(job,cfg,'所选声音缺少完整有效的文稿时码')
    duration=subtitle_review.duration_us(subtitle_review.audio_path(job))
    secs=duration/1e6
    clips=read_json(job/'cut_result.json')['segments'][0]['clips']
    wanted=word_ranges(cfg, secs)
    inputs=recipe(job,cfg,doc)
    audio_sha=file_hash(subtitle_review.audio_path(job))
    windows=route.probe_windows(doc['blocks'],clips,duration=secs)
    if not windows:
        return native(job,cfg,'所选范围缺少可用的探测窗口')
    # Paragraph manuscripts will require word timing over nearly the whole edit. Obtain
    # one coherent transcript and use it for the anchors and the actual subtitles.
    full_pass=route.prefer_full_pass(doc['blocks'],clips)
    native_ready=full_pass and not cfg.get('caption_probe_asr') and asr_backend.backend(cfg)!='funasr'
    if native_ready:
        error=native(job,cfg,'文稿以长段为主，直接一次识别并复用整轨词时码')
        if error:
            return error
        full=audio.cached_full(job,cfg,require_current=True)
        if not full:
            return ''
        probes={**full,'windows':[(0,duration)]}
        if Path(probes['record_path']).parent==job:
            probes['record_path']=str(job/'timed-native-asr/final_transcript.meta.json')
    else:
        probes=acoustic(job,cfg,[(0,duration)] if full_pass else windows)
    if probes['source']=='unavailable':
        if full_pass:
            raise ValueError('所选对齐方式未得到整轨词稿：'+str(probes.get('reason','')))
        return native(job,cfg,probes.get('reason') or '缺少可靠的语音锚点')
    plan=route.plan(doc['blocks'],clips,probes,word_ranges=wanted,duration=secs)
    if plan['route']=='asr' and not full_pass:
        return native(job,cfg,plan.get('reason') or '开中尾语音与文稿无法稳定匹配',plan)
    if full_pass and any(e.get('side')=='chunk_join' for e in probes.get('edge_conflicts',[])):
        raise ValueError('本地整轨词稿的分窗接缝有冲突，请选择现有词稿或改用整轨识别')
    repairs=[(r['start_us'],r['end_us']) for r in plan['align']]
    # Context lets the recognizer hear words straddling a document/word boundary.
    # Only the logical repair interval is published.
    covered={}
    additional=[]
    for a,b in repairs:
        lo,hi=max(0,a-1_000_000),min(duration,b+1_000_000)
        if any(p<=lo and hi<=q for p,q in probes.get('windows',[])):
            covered[(a,b)]=probes
        else:
            additional.append((lo,hi))
    evidence=acoustic(job,cfg,additional) if additional else {}
    if evidence.get('source')=='unavailable':
        return native(job,cfg,evidence.get('reason') or '局部对齐不可用',plan)
    # Adjacent repairs plus their context may cover an entire probe-only gap. Prefer the
    # same recognition pass on both sides, so a word cannot be claimed twice on two clocks.
    repair_windows=evidence.get('windows',[])
    covered={(a,b):observed for (a,b),observed in covered.items()
             if not any(lo<=max(0,a-1_000_000) and min(duration,b+1_000_000)<=hi
                        for lo,hi in repair_windows)}
    if covered:
        evidence={**evidence,'by_window':covered}
    try:
        if any(row.get('side')=='chunk_join' for row in evidence.get('edge_conflicts',[])):
            raise ValueError('分窗接缝附近的词时码不可靠')
        transcript,stats=stitch(plan,evidence,duration)
    except ValueError as exc:
        if not full_pass:
            return native(job,cfg,str(exc),plan)
        if native_ready:
            return ''  # The complete word transcript and its source report already exist.
        # The single-pass word timing remains usable if document boundaries disagree.
        plan={**plan,'direct':[],'route':'asr','reason':str(exc),
              'align':[{'start_us':0,'end_us':duration,'clip_index':1,
                        'references':[],'block_ids':[],'reasons':['use_complete_word_timing']}]}
        repairs=[(0,duration)]
        transcript,stats=stitch(plan,probes,duration)
    fresh_doc=document_context.caption_document(job,cfg,fetch=False)
    if (file_hash(subtitle_review.audio_path(job))!=audio_sha or not fresh_doc
            or recipe(job,cfg,fresh_doc)!=inputs):
        raise ValueError('字幕制作期间音频或文稿发生变化，需重新恢复')
    evidence_fields=('source','audio_sha256','transcript_sha256','record_path','record_paths','edge_conflicts')
    report={**plan,'schema':1,'recipe':inputs,'audio_sha256':audio_sha,
            'acoustic_evidence':{k:{field:value[field] for field in evidence_fields if field in value}
                                 for k,value in [('probes',probes),('repairs',evidence)]},
            'single_full_pass':full_pass,'document_sha256':doc['blocks_hash'],'reference_groups':reference_groups(plan,doc),
            'probe_source':probes['source'],'probe_windows_us':windows,
            'alignment_source':evidence.get('source') or (probes['source'] if covered else None),
            'reused_probe_windows':len(covered),'additional_window_requests_us':additional,
            'alignment_windows_us':repairs,'phrase_alignment':stats}
    preserve_native(job, audio_sha)
    target=job/'final_transcript.json'
    atomic_json(target,transcript)
    atomic_json(job/REPORT,report)
    atomic_json(job/'final_transcript.meta.json',{'schema':2,'source_kind':TIMED,
                'audio_sha256':audio_sha,'transcript_sha256':file_hash(target),'route_report_hash':digest(report)})
    return ''
