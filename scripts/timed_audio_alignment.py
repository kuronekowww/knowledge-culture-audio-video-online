"""Acquire independent acoustic evidence on the final edit clock, using bound caches or short windows."""
from array import array
from pathlib import Path
import subprocess
import tempfile
import wave

import asr_backend
import bailian_asr
import bailian_hotwords
import shared_album
import subtitle_review
import transcription_cache
from artifacts import atomic_json, digest, file_hash, file_lock, read_json, verified_file

RATE = 16000
VERSION = 'packed-pcm16k-v1'


class AlignmentPending(RuntimeError):
    """The existing window request must be resumed or inspected before switching to a full paid run."""


def _word_units(path, duration):
    units = subtitle_review.units_from_asr(read_json(path), duration)
    if not units or any(u['timing_source'] not in ('word','timestamp') for u in units):
        return None
    return [{'text':u['text'], 'start_us':u['start_us'], 'end_us':u['end_us'], 'timing_source':'word'} for u in units]


def cached_full(job, cfg, *, require_current=False):
    """Read native word timing tied to these exact audio bytes. Never initiate recognition."""
    job = Path(job)
    audio = subtitle_review.audio_path(job)
    sha = file_hash(audio)
    candidates = []
    archive = job/'timed-native-asr'
    folders = [job,archive]
    if require_current:
        folders.extend(sorted((archive/'history').glob('*')))
    ident = asr_backend.identity(cfg) if require_current else None
    prompt = asr_backend.prompt(cfg,shared_album.dictionary(cfg)) if require_current else None
    for folder in folders:
        path = folder/'final_transcript.json'
        receipt = folder/'final_transcript.meta.json'
        meta = read_json(receipt,{})
        identity = meta.get('backend_identity') or {}
        if require_current and (identity != ident or meta.get('asr_settings_hash') != digest(cfg.get('asr',{}))
                                or meta.get('prompt_hash') != digest(prompt)):
            continue
        if (meta.get('audio_sha256') == sha and identity.get('backend') in ('funasr','bailian_paraformer','whisper')
                and verified_file(path, meta.get('transcript_sha256'))):
            candidates.append((path,receipt,meta['transcript_sha256'],identity))
    if not candidates:
        kind = asr_backend.backend(cfg)
        ident = ident or asr_backend.identity(cfg)
        signature = {'audio_sha256':sha,'backend_identity':ident,'settings':cfg.get('asr',{})}
        if kind == 'bailian_paraformer':
            signature['hotwords'] = bailian_hotwords.vocabulary(cfg)
        else:
            signature.update(schema=2,prompt=prompt if prompt is not None else
                             asr_backend.prompt(cfg,shared_album.dictionary(cfg)))
        key = digest(signature)
        folder = shared_album.album_root(cfg)/'asr'/key
        path, receipt = folder/'transcript.json', folder/'record.json'
        record = read_json(receipt,{})
        if (record.get('key') == key and verified_file(path,record.get('sha256')) and
                (kind != 'bailian_paraformer' or
                 (record.get('raw_path') and verified_file(Path(record['raw_path']),record.get('raw_sha256'))))):
            candidates.append((path,receipt,record['sha256'],ident))
    if candidates:
        duration = subtitle_review.duration_us(audio)
        for path,receipt,asr_sha,ident in candidates:
            words = _word_units(path,duration)
            if words:
                return {'source':'bound_word_cache','words':words,'audio_sha256':sha,
                        'transcript_sha256':asr_sha,'backend_identity':ident,
                        'transcript_path':str(path),'record_path':str(receipt)}
    return None


def pending_full(job,cfg):
    if asr_backend.backend(cfg) != 'bailian_paraformer':
        return False
    try:
        credential = bailian_asr.key()
    except ValueError:
        return False
    entry = bailian_asr.remote_entry(cfg,file_hash(subtitle_review.audio_path(Path(job))),bailian_hotwords.vocabulary(cfg),credential)
    receipt = read_json(entry/'task.json',{})
    # The provider may have completed a prior task while local normalization was interrupted.
    return bool(receipt.get('task_id') or receipt.get('status')=='submitting')


def _intervals(windows,duration):
    merged = []
    for a,b in sorted(windows):
        if type(a)!=int or type(b)!=int or not 0<=a<b<=duration:
            raise ValueError('音频窗口须为最终音频范围内的整数微秒')
        if merged and a<=merged[-1][1]:
            merged[-1][1]=max(b,merged[-1][1])
        else:
            merged.append([a,b])
    return merged


def _pack(audio, windows, folder):
    """Keep disjoint trims on one cloud request; separators never receive subtitle times."""
    manifest=folder/'packed.json'
    dest=folder/'windows.flac'
    saved=read_json(manifest,{})
    if saved.get('windows')==windows and verified_file(dest,saved.get('sha256')):
        return dest,saved['map'],saved['silence_windows']
    mapping=[];silence=[];frames=0
    with tempfile.TemporaryDirectory(dir=folder) as scratch:
        wav=Path(scratch)/'windows.wav'
        with wave.open(str(wav),'wb') as target:
            target.setparams((1,2,RATE,0,'NONE',''))
            for i,(a,b) in enumerate(windows):
                if i:
                    target.writeframesraw(bytes(2*RATE))
                    frames+=RATE
                cmd=['ffmpeg','-v','error','-nostdin','-ss',f'{a/1e6:.6f}','-i',str(audio),'-t',f'{(b-a)/1e6:.6f}',
                     '-map','0:a:0','-vn','-ar',str(RATE),'-ac','1','-f','s16le','-c:a','pcm_s16le','pipe:1']
                result=subprocess.run(cmd,capture_output=True,check=True,timeout=max(60,round((b-a)/1e6)))
                pcm=result.stdout
                count=len(pcm)//2
                if not count or len(pcm)%2 or abs(count/RATE - (b-a)/1e6)>.08:
                    raise ValueError('片段实际解码采样数与选定窗口不符')
                samples=array('h', pcm)
                if min(samples)>=-1 and max(samples)<=1:
                    # A stereo downmix can cancel audible antiphase signals. Verify all source channels.
                    stream_cmd = cmd.copy()
                    channel_arg = stream_cmd.index('-ac')
                    del stream_cmd[channel_arg:channel_arg+2]
                    original = subprocess.run(stream_cmd,capture_output=True,check=True,timeout=max(60,round((b-a)/1e6)))
                    all_channels = array('h',original.stdout)
                    if all_channels and min(all_channels)>=-1 and max(all_channels)<=1:
                        silence.append([a,b])
                target.writeframesraw(pcm)
                at=round(frames*1e6/RATE)
                frames+=count
                mapping.append([a,b,at,round(frames*1e6/RATE)])
        flac=Path(scratch)/'windows.flac'
        subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(wav),'-map_metadata','-1','-c:a','flac',str(flac)],
                       check=True,capture_output=True,timeout=180)
        flac.replace(dest)
    atomic_json(manifest,{'windows':windows,'map':mapping,'silence_windows':silence,'sha256':file_hash(dest)})
    return dest,mapping,silence


def _unpack(words,mapping):
    output=[];edge_conflicts=[]
    n=0
    for a,b,left,right in mapping:
        while n<len(words) and words[n]['end_us']<=left:
            n+=1
        while n<len(words) and words[n]['start_us']<right:
            w=words[n];n+=1
            if w['start_us']<left or w['end_us']>right:
                edge_conflicts.append({'window':[a,b],'text':w['text'],'side':'start' if w['start_us']<left else 'end'})
                continue
            start=a+w['start_us']-left
            end=min(b,a+w['end_us']-left)
            if start<end:
                output.append({**w,'start_us':start,'end_us':end})
    return output,edge_conflicts


def _selection(words,windows):
    return [w for w in words if any(a<=w['start_us']<w['end_us']<=b for a,b in windows)]


def _report(words,source,windows,**evidence):
    observed = [] if source=='unavailable' else windows
    empty = [r for r in observed if not any(r[0]<=w['start_us']<w['end_us']<=r[1] for w in words)]
    return {'source':source,'words':words,'windows':observed,'no_word_windows':empty,'edge_conflicts':[],**evidence}


def _settings(cfg):
    """A team may opt in to its installed local recognizer, independently of the full ASR route."""
    selected=cfg.get('caption_probe_asr')
    return {**cfg,'asr':selected} if isinstance(selected,dict) else cfg


def _local_windows(audio, windows, cfg, ident, dictionary, audio_sha, duration):
    """Short, separate model inputs. A non-VAD Paraformer cannot time disjoint concatenated speech."""
    import resident_asr
    settings=cfg.get('asr',{})
    prompt=' '.join(entry['canonical'] for entry in dictionary.get('entries',[]))
    namespace=shared_album.album_root(cfg)/'timed-local-windows'/digest(
        ['local-cpu-window-v2', audio_sha,ident,settings,prompt])
    batch_pool=resident_asr.ACTIVE
    pool=batch_pool or resident_asr.Pool()
    words=[];conflicts=[];silent=[];receipts=[]
    try:
        for a,b in windows:
            quiet=True
            for start in range(a,b,12_000_000):
                end=min(b,start+12_000_000)
                # Recognize some context on either side of the selected core; never cut a token to fit it.
                left=max(0,start-700_000);right=min(duration,end+700_000)
                folder=namespace/f'{left}-{right}'
                with file_lock(folder/'window.lock',timeout=1000):
                    flac,mapping,near_zero=_pack(audio,[[left,right]],folder)
                    if near_zero:
                        continue
                    quiet=False
                    raw=folder/'acoustic.json';rec=folder/'acoustic.meta.json'
                    meta=read_json(rec,{})
                    win_sha=file_hash(flac)
                    if (meta.get('audio_sha256')!=win_sha or
                            not verified_file(raw,meta.get('transcript_sha256'))):
                        with file_lock(shared_album.album_root(cfg).parent.parent/'asr-device.lock',timeout=3600):
                            pool.transcribe(ident,asr_backend.model_path(cfg),settings,flac,raw,prompt)
                        atomic_json(rec,{'audio_sha256':win_sha,'transcript_sha256':file_hash(raw)})
                    units=_word_units(raw,mapping[0][3])
                    if units is None:
                        raise ValueError('本地片段无可验证词级结果')
                    for unit in units:
                        u={**unit,'start_us':left+unit['start_us'],'end_us':left+unit['end_us']}
                        if u['start_us'] < a < u['end_us'] or u['start_us'] < b < u['end_us']:
                            conflicts.append({'window':[a,b],'text':u['text'],'side':'start' if u['start_us']<a else 'end'})
                            continue
                        # Overlapping contexts have one owner, selected by real source-time midpoint.
                        if not start <= (u['start_us']+u['end_us'])/2 < end:
                            continue
                        if words and u['start_us'] < words[-1]['end_us']:
                            if words[-1]['text'] != u['text']:
                                conflicts.append({'window':[a,b],'text':u['text'],'side':'chunk_join',
                                                  'start_us':u['start_us'],'end_us':u['end_us']})
                            continue
                        words.append(u)
                    receipts.append(str(rec))
            if quiet:
                silent.append([a,b])
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        return _report([],'unavailable',windows,reason=str(exc))
    finally:
        if batch_pool is None:
            pool.close()
    if file_hash(audio)!=audio_sha:
        raise ValueError('本地取证期间最终音轨已变化')
    return _report(words,'local_funasr',windows,audio_sha256=audio_sha,silence_windows=silent,
                   edge_conflicts=conflicts,record_paths=receipts)


def acquire(job,cfg,windows):
    job=Path(job)
    audio=subtitle_review.audio_path(job)
    windows=_intervals(windows,subtitle_review.duration_us(audio))
    if not windows:
        return _report([],'unavailable',[],reason='没有请求对齐窗口')
    previous=cached_full(job,cfg)
    if previous:
        edge_conflicts = [{'window':[a,b],'text':w['text'],'side':side}
                          for a,b in windows for w in previous['words'] for side,edge in [('start',a),('end',b)]
                          if w['start_us']<edge<w['end_us']]
        return _report(_selection(previous['words'],windows),'bound_word_cache',windows,
                       audio_sha256=previous['audio_sha256'],transcript_sha256=previous['transcript_sha256'],edge_conflicts=edge_conflicts)
    selected=_settings(cfg)
    kind=asr_backend.backend(selected)
    if kind not in ('bailian_paraformer','funasr'):
        return _report([],'unavailable',windows,reason='所选窗口识别后端没有可验证的词级时码')
    try:
        ident=asr_backend.identity(selected)
    except (ValueError,OSError,subprocess.SubprocessError) as exc:
        return _report([],'unavailable',windows,reason=str(exc))
    if kind=='funasr' and not ident.get('model_sha256'):
        return _report([],'unavailable',windows,reason='所选本地词级模型未安装')
    sha=file_hash(audio)
    words_hint=bailian_hotwords.vocabulary(selected) if kind=='bailian_paraformer' else shared_album.dictionary(selected)
    if kind=='funasr':
        return _local_windows(audio,windows,selected,ident,words_hint,sha,subtitle_review.duration_us(audio))
    key=digest([VERSION,sha,windows,ident,selected.get('asr',{}),words_hint])
    folder=shared_album.album_root(selected)/'timed-audio-windows'/key
    folder.mkdir(parents=True,exist_ok=True)
    source='bailian_windows'
    with file_lock(folder/'.windows.lock',timeout=1000):
        flac,mapping,silence=_pack(audio,windows,folder)
        if file_hash(audio)!=sha:
            raise ValueError('抽取窗口期间成片音轨已变化，请重新读取当前版本')
        if len(silence)==len(windows):
            return _report([],'decoded_silence',windows,silence_windows=silence,audio_sha256=sha)
        atomic_json(folder/'cut_result.json',{'segments':[{'path':str(flac.resolve())}]})
        native=cached_full(folder,selected)
        if not native:
            try:
                credential=bailian_asr.key()
            except ValueError as exc:
                return _report([],'unavailable',windows,reason=str(exc))
            # Only the fields required by configure_bailian.py --recover-task belong in a recovery job.
            recovery = {k:v for k,v in selected.get('asr',{}).items()
                        if k in ('backend','model','api_base','use_hotwords')}
            atomic_json(folder/'config.json', {'album_id':selected['album_id'], 'asr':recovery,
                        'shared_root':str(shared_album.album_root(selected).parent.parent)})
            err=transcription_cache.transcribe(folder,selected)
            if err:
                entry=bailian_asr.remote_entry(selected,file_hash(flac),words_hint,credential)
                receipt=read_json(entry/'task.json',{})
                raw=entry/'asr_raw.json'
                if receipt.get('status')=='SUCCEEDED' and verified_file(raw,receipt.get('raw_sha256')):
                    channels=[r for r in read_json(raw,{}).get('transcripts',[]) if r.get('channel_id')==0]
                    if len(channels)==1 and channels[0].get('sentences')==[] and not channels[0].get('text'):
                        return _report([],source,windows,audio_sha256=sha,silence_windows=silence,record_path=str(raw))
                raise AlignmentPending(f'{err}；当前窗口任务目录：{folder}')
            native=cached_full(folder,selected)
        if not native:
            raise AlignmentPending(f'已处理窗口但缺少可验证词级回执：{folder}')
        if file_hash(audio)!=sha:
            raise ValueError('窗口识别期间成片音轨已变化，回执保存在原音轨目录')
        words,edge_conflicts=_unpack(native['words'],mapping)
        return _report(words,source,windows,audio_sha256=sha,silence_windows=silence,edge_conflicts=edge_conflicts,
                       transcript_sha256=native['transcript_sha256'],record_path=native['record_path'])
