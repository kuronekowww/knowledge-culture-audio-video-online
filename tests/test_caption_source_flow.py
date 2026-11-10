import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from artifacts import atomic_json, digest, file_hash, read_json
import asr_backend, caption_routing, job_versions, run, shared_album, subtitle_review
import timed_audio_alignment as audio
import transcription_cache


class CaptionFlowTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.job=Path(temp.name)
        self.wav=self.job/'voice.wav'
        with wave.open(str(self.wav),'wb') as f:
            f.setparams((1,1,1000,0,'NONE',''))
            f.writeframes(bytes([128])*300000)
        self.cfg={'album_id':'47','shared_root':str(self.job/'cache'),'ai_doc_path':'source.json'}
        self.clips=[{'start':0.,'end':300.,'speed':1.}]
        self.content={'is_complete':True,'is_intro_only':False,'complete_reason':'完整论述',
            'problem':'标题','demand_reason':'依据','delivered_value':'解释','conclusion':'方法',
            'opening_hook':'起因','evidence':[{'start':1,'end':3,'text':'示例'}],
            'return_hook_omission_reason':'完整单集'}
        atomic_json(self.job/'config.json',self.cfg)
        atomic_json(self.job/'cut_plan.json',{'source_audio':str(self.wav),'selection_mode':'full_track',
                    'track_id':'119','highlight_id':'X','clips':self.clips,'content':self.content})
        atomic_json(self.job/'cut_result.json',{'segments':[{'path':str(self.wav),'clips':self.clips}]})
        self.blocks=[]
        self.words=[]
        for i in range(100):
            letter=chr(0x5200+i)
            line=letter+'是本课的起点。'
            self.blocks.append({'startTime':3000*i,'endTime':3000*i+2800,'text':line})
            for j,char in enumerate(line[:-1]):
                start=3_000_000*i+j*400_000
                self.words.append({'text':char,'start_us':start,'end_us':start+350000,'timing_source':'word'})
        atomic_json(self.job/'source.json',{'body':self.blocks,'methodName':'queryAiDocByTrackId'})

    def acquire(self,job,cfg,windows):
        return {'source':'unit-test-acoustic-observations','windows':windows,
                'words':[w for w in self.words if any(a<=w['start_us'] and w['end_us']<=b for a,b in windows)]}

    def test_direct_and_cached_resume_use_the_subtitle_review_stage(self):
        with patch.object(audio,'acquire',side_effect=self.acquire) as observe, \
             patch.object(transcription_cache,'transcribe',side_effect=AssertionError('全轨ASR不应触发')):
            result=run.stage_subtitles(self.job,self.cfg)
            self.assertTrue(result.startswith('AWAITING_REVIEW:'),result)
            self.assertEqual(observe.call_count,1)
            request=read_json(self.job/'subtitle_review_request.json')
            self.assertEqual(request['timing_source']['route'],'document')
            self.assertEqual(len(request['suggested_cues']),len(self.blocks))
            self.assertEqual({u['timing_source'] for u in request['units']},{'document_segment'})
            self.assertTrue(all(u.get('document_block_ids') for u in request['units']))
            decision={'request_hash':request['request_hash'],'reviewer':'fixture full reviewer',
                      'checks':{key:True for key in subtitle_review.CHECKS},
                      'accept':[row['candidate_id'] for row in request['suggested_cues']],'patches':[]}
            atomic_json(self.job/'subtitle_review_response.json',decision)
            reviewed=subtitle_review.apply_review(self.job,self.cfg)
            self.assertEqual(reviewed['status'],'passed',reviewed.get('pending'))
            self.assertIn('00:00:00,000 --> 00:00:02,800',(self.job/'subtitles.srt').read_text(encoding='utf-8-sig'))
            srt=self.job/'subtitles.srt'
            approved=srt.read_bytes()
            state={'stages':{'subtitles':'done'}}
            job_versions.stamp(state,'subtitles',self.job,self.cfg)
            srt.unlink()
            self.assertFalse(subtitle_review.ready(self.job,self.cfg))
            self.assertNotEqual(job_versions.signature('subtitles',self.job,self.cfg),state['fingerprints']['subtitles'])
            self.assertEqual(job_versions.invalidate_changed(state,self.job,self.cfg),[])
            self.assertEqual(srt.read_bytes(),approved)
            srt.write_bytes(b'interrupted')
            with patch.object(run,'stage_transcribe',side_effect=AssertionError('修复导出无需再识别')):
                self.assertEqual(run.stage_subtitles(self.job,self.cfg),'')
            self.assertEqual(srt.read_bytes(),approved)
            self.assertEqual(run.stage_subtitles(self.job,self.cfg),'')
            self.assertEqual(run.stage_transcribe(self.job,self.cfg),'')
            self.assertEqual(observe.call_count,1)
            # User-selected mode bypasses document-based reuse; a native transcript must be obtained.
            with patch.object(transcription_cache,'transcribe',return_value='full-route') as full:
                self.assertEqual(run.stage_transcribe(self.job,{**self.cfg,'subtitle_source':'asr'}),'full-route')
                full.assert_called_once()

    def test_padded_repairs_covering_a_probe_gap_use_one_word_clock(self):
        # The same trailing word falls on different sides of a seam in two ASR passes.
        def word(text,start,end):
            return {'text':text,'start_us':round(start*1e6),'end_us':round(end*1e6)}
        areas=[(0,110_000_000),(110_000_000,111_000_000),(111_000_000,300_000_000)]
        plan={'route':'hybrid','direct':[],'clips':[], 'align':[
            {'start_us':a,'end_us':b,'references':[],'block_ids':[]} for a,b in areas]}
        probes={'source':'test-probes','windows':[(100_000_000,125_000_000)],
                'words':[word('呢',109.8,110.4),word('好',110.6,110.8)]}
        repairs={'source':'test-repairs','windows':[(0,300_000_000)],
                 'words':[word('呢',109.6,110.2),word('好',110.6,110.8),word('继续',111.2,111.8)]}
        with patch.object(audio,'acquire',side_effect=[probes,repairs]), \
             patch('timed_caption_route.plan',return_value=plan), \
             patch.object(transcription_cache,'transcribe',side_effect=AssertionError('不应整轨重识别')):
            self.assertEqual(run.stage_transcribe(self.job,self.cfg),'')
        data=read_json(self.job/'final_transcript.json')
        units=subtitle_review.units_from_asr(data,300_000_000)
        self.assertEqual([u['text'] for u in units],['呢','好','继续'])
        self.assertEqual(units[0]['end_us'],110_200_000)

    def test_paragraphs_need_only_one_full_recognition_even_for_anchor_checks(self):
        paragraphs=[]
        for first in range(0,100,5):
            paragraphs.append({'startTime':3000*first,'endTime':3000*(first+5),
                               'text':''.join(row['text'] for row in self.blocks[first:first+5])})
        atomic_json(self.job/'source.json',{'body':paragraphs})
        def recognize(job,cfg):
            import asr_backend,shared_album
            from artifacts import digest
            transcript=job/'final_transcript.json'
            rows=[{'start':w['start_us']/1e6,'end':w['end_us']/1e6,'text':w['text'],
                   'words':[{'start':w['start_us']/1e6,'end':w['end_us']/1e6,'word':w['text']}]}
                  for w in self.words]
            atomic_json(transcript,{'segments':rows})
            atomic_json(job/'final_transcript.meta.json',{'audio_sha256':file_hash(self.wav),
                'transcript_sha256':file_hash(transcript),'backend_identity':asr_backend.identity(cfg),
                'asr_settings_hash':digest(cfg.get('asr',{})),
                'prompt_hash':digest(asr_backend.prompt(cfg,shared_album.dictionary(cfg)))})
            return ''
        with patch.object(audio,'acquire',side_effect=AssertionError('长段无需额外抽样')), \
             patch.object(transcription_cache,'transcribe',side_effect=recognize) as recognize_full:
            self.assertEqual(run.stage_transcribe(self.job,self.cfg),'')
            self.assertEqual(run.stage_transcribe(self.job,self.cfg),'')
            recognize_full.assert_called_once()
        report=read_json(self.job/caption_routing.REPORT)
        self.assertTrue(report['single_full_pass'])
        self.assertFalse(report['additional_window_requests_us'])
        self.assertTrue(Path(report['acoustic_evidence']['probes']['record_path']).is_file())

    def test_opening_words_and_remaining_sentences_have_different_provenance(self):
        cfg={**self.cfg,'presentation':{'mode':'hyperframes','choice_evidence':'网页'}}
        with patch.object(audio,'acquire',side_effect=self.acquire), \
             patch.object(transcription_cache,'transcribe',side_effect=AssertionError('全轨ASR不应触发')):
            self.assertEqual(run.stage_transcribe(self.job,cfg),'')
            request=subtitle_review.prepare(self.job,cfg)
            self.assertEqual(request['timing_source']['route'],'hybrid')
            self.assertTrue(all(u['timing_source']=='word' for u in request['units'] if u['start_us']<15_000_000))
            self.assertEqual(request['units'][-1]['timing_source'],'document_segment')
            changed={**self.cfg,'subtitle_word_ranges':[[33,40]]}
            with self.assertRaisesRegex(ValueError,'配置变化'):
                subtitle_review.current_basis(self.job,changed)
            self.blocks[20]['text']='另一版已修改句子。'
            atomic_json(self.job/'source.json',{'body':self.blocks})
            with self.assertRaisesRegex(ValueError,'文稿'):
                subtitle_review.current_basis(self.job,cfg)

    def test_accept_remaining_edits_only_named_cues_and_keeps_review_checks(self):
        with patch.object(audio,'acquire',side_effect=self.acquire):
            self.assertEqual(run.stage_transcribe(self.job,self.cfg),'')
        request=subtitle_review.prepare(self.job,self.cfg)
        candidates=request['suggested_cues']
        selected=candidates[1]
        new_text=selected['text'].replace('起点','重点')
        change={'candidate_ids':[selected['candidate_id']], 'cues':[{**selected,'text':new_text,
                'corrected_text':new_text,'reason':'纠正用字','evidence':'测试原文','boundary_reason':'句意在此结束',
                'alignment_review':{'mode':'audio_checked','evidence':'测试用的对轴回执'}}]}
        response={'request_hash':request['request_hash'],'reviewer':'fixture full reviewer',
                  'checks':{k:True for k in subtitle_review.CHECKS},'accept':'remaining'}
        path=self.job/'subtitle_review_response.json'
        for invalid in ([change,change],[{**change,'candidate_ids':['unknown']}]):
            atomic_json(path,{**response,'patches':invalid})
            with self.assertRaisesRegex(ValueError,'编号'):
                subtitle_review.apply_review(self.job,self.cfg)
        atomic_json(path,{**response,'patches':[change]})
        reviewed=subtitle_review.apply_review(self.job,self.cfg)
        self.assertEqual(reviewed['status'],'passed',reviewed['pending'])
        self.assertEqual(reviewed['decision_summary']['accepted_candidates'],len(candidates)-1)
        self.assertEqual([r['unit_ids'] for r in reviewed['cues']],[r['unit_ids'] for r in candidates])
        self.assertEqual(reviewed['cues'][1]['text'],subtitle_review.trim_text(new_text))
        change['cues'][0]['needs_listen']=True
        atomic_json(path,{**response,'patches':[change]})
        self.assertEqual(subtitle_review.apply_review(self.job,self.cfg)['status'],'needs_review')

    def test_forced_asr_restores_matching_native_history_and_auto_reuses_it(self):
        with patch.object(audio,'acquire',side_effect=self.acquire):
            self.assertEqual(run.stage_transcribe(self.job,self.cfg),'')
        archive=self.job/'timed-native-asr'
        raw={'backend':'bailian_paraformer','segments':[{'text':'样本','start':1.,'end':2.,
             'words':[{'word':'样本','start':1.,'end':2.}]}]}
        atomic_json(archive/'final_transcript.json',raw)
        receipt={'audio_sha256':file_hash(self.wav),'transcript_sha256':file_hash(archive/'final_transcript.json'),
                 'backend_identity':asr_backend.identity(self.cfg),'asr_settings_hash':digest({}),
                 'prompt_hash':digest(asr_backend.prompt(self.cfg,shared_album.dictionary(self.cfg)))}
        atomic_json(archive/'final_transcript.meta.json',{**receipt,'prompt_hash':'earlier'})
        history=archive/'history'/'matched'
        atomic_json(history/'final_transcript.json',raw)
        atomic_json(history/'final_transcript.meta.json',receipt)
        with patch.object(audio,'acquire',side_effect=AssertionError('无需重新抽查')), \
             patch.object(transcription_cache,'transcribe',side_effect=AssertionError('无需重复整轨识别')):
            for mode in ('asr','auto'):
                cfg={**self.cfg,'subtitle_source':mode}
                self.assertEqual(run.stage_transcribe(self.job,cfg),'')
                self.assertEqual(subtitle_review.current_basis(self.job,cfg)['asr_sha256'],receipt['transcript_sha256'])
                self.assertEqual(read_json(self.job/'final_transcript.json'),raw)
                self.assertEqual(read_json(self.job/caption_routing.REPORT)['route'],'asr_cache')

    def test_pending_acoustic_task_never_triggers_new_full_asr(self):
        with patch.object(audio,'acquire',side_effect=audio.AlignmentPending('原请求待确认')), \
             patch.object(transcription_cache,'transcribe',side_effect=AssertionError('不要进入全轨ASR')):
            with self.assertRaises(audio.AlignmentPending):
                run.stage_transcribe(self.job,self.cfg)
        self.assertFalse((self.job/'final_transcript.json').exists())

    def test_obsolete_native_observations_remain_archived_when_new_document_result_is_written(self):
        transcript=self.job/'final_transcript.json'
        native={'backend':'bailian_paraformer','segments':[{'text':'样本','start':1.,'end':2.,
                'words':[{'word':'样本','start':1.,'end':2.}]}]}
        atomic_json(transcript,native)
        old={'audio_sha256':file_hash(self.wav),'transcript_sha256':file_hash(transcript),
             'backend_identity':{'backend':'bailian_paraformer','adapter_sha256':'earlier'}}
        atomic_json(self.job/'final_transcript.meta.json',old)
        with patch.object(audio,'acquire',side_effect=self.acquire),patch.object(audio,'pending_full',return_value=False):
            self.assertEqual(run.stage_transcribe(self.job,self.cfg),'')
        self.assertEqual(read_json(self.job/'timed-native-asr/final_transcript.json'),native)
        self.assertEqual(read_json(self.job/'timed-native-asr/final_transcript.meta.json'),old)
        self.assertEqual(read_json(transcript)['backend'],caption_routing.TIMED)

    def test_pending_full_task_is_resumed_before_paid_window_probes(self):
        with patch.object(audio,'acquire',side_effect=AssertionError('不得提交局部任务')), \
             patch.object(audio,'pending_full',return_value=True), \
             patch.object(transcription_cache,'transcribe',return_value='resuming'):
            self.assertEqual(run.stage_transcribe(self.job,self.cfg),'resuming')
            self.assertIn('已有',read_json(self.job/'caption_source_attempt.json')['reason'])

    def test_invalid_document_time_falls_back_without_starting_probes(self):
        self.blocks[40].pop('endTime')
        atomic_json(self.job/'source.json',{'body':self.blocks})
        with patch.object(audio,'acquire',side_effect=AssertionError('不应探测')), \
             patch.object(transcription_cache,'transcribe',return_value='existing_asr'):
            self.assertEqual(run.stage_transcribe(self.job,self.cfg),'existing_asr')
            self.assertEqual(read_json(self.job/caption_routing.REPORT)['route'],'asr')
