import array
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import timed_audio_alignment as alignment
from artifacts import atomic_json, file_hash, read_json


class TimedAcousticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job=Path(self.tmp.name)/'job'
        self.job.mkdir()
        self.cfg={'album_id':'7','shared_root':str(Path(self.tmp.name)/'shared')}
        self.audio=self.job/'audio.wav'
        self.write_audio(bytes(2*16000)+array.array('h',[120,160,-260,-410]*4000*2).tobytes())
        atomic_json(self.job/'cut_result.json',{'segments':[{'path':str(self.audio)}]})
        self.windows=[(1_000_000,1_500_000),(2_000_000,2_500_000)]

    def write_audio(self,pcm):
        with wave.open(str(self.audio),'wb') as out:
            out.setparams((1,2,16000,0,'NONE',''))
            out.writeframes(pcm)

    @staticmethod
    def record(folder, words, sha=None):
        text=''.join(r[0] for r in words)
        output=folder/'final_transcript.json'
        atomic_json(output,{'backend':'bailian_paraformer','segments':[
                    {'start':words[0][1],'end':words[-1][2],'text':text,
                     'words':[{'word':w,'start':a,'end':b} for w,a,b in words]}]})
        atomic_json(folder/'final_transcript.meta.json',{'audio_sha256':sha or file_hash(alignment.subtitle_review.audio_path(folder)),
                   'transcript_sha256':file_hash(output),'backend_identity':{'backend':'bailian_paraformer'}})

    def test_native_cache_must_belong_to_exact_final_audio(self):
        self.record(self.job,[('开口',1.,1.3),('接缝',1.4,1.6),('末',2.1,2.2)])
        with patch.object(alignment.transcription_cache,'transcribe',side_effect=AssertionError):
            got=alignment.acquire(self.job,self.cfg,self.windows)
        self.assertEqual(got['source'],'bound_word_cache')
        self.assertEqual([u['text'] for u in got['words']],['开口','末'])
        self.assertEqual(got['edge_conflicts'][0]['text'],'接缝')
        self.write_audio(bytes(2*16000*3))
        self.assertIsNone(alignment.cached_full(self.job,self.cfg))

    def test_nonadjacent_windows_submit_once_and_resume_without_key(self):
        def recognize(folder,cfg):
            # 0.5 s first window, one second silence, then second window.
            self.record(folder,[('开',.1,.3),('界',.45,.64),('末',1.61,1.88)])
            return ''
        with patch.object(alignment.bailian_asr,'key', return_value='test'), patch.object(alignment.transcription_cache,'transcribe',side_effect=recognize) as submit:
            got=alignment.acquire(self.job,self.cfg,self.windows)
            self.assertEqual(submit.call_count,1)
        self.assertEqual(got['edge_conflicts'],[{'window':[1000000,1500000],'text':'界','side':'end'}])
        self.assertEqual([(w['text'],w['start_us'],w['end_us']) for w in got['words']],
                         [('开',1100000,1300000),('末',2110000,2380000)])
        with patch.object(alignment.bailian_asr,'key',side_effect=AssertionError),patch.object(alignment.transcription_cache,'transcribe',side_effect=AssertionError):
            self.assertEqual(alignment.acquire(self.job,self.cfg,self.windows),got)

    def test_no_submission_for_silence_but_other_missing_evidence_is_not_silence(self):
        with patch.object(alignment.bailian_asr,'key',side_effect=ValueError('缺密钥')),patch.object(alignment.transcription_cache,'transcribe',side_effect=AssertionError):
            silent=alignment.acquire(self.job,self.cfg,[(0,999000)])
            self.assertEqual(silent['silence_windows'],[[0,999000]])
            failed=alignment.acquire(self.job,self.cfg,self.windows)
            self.assertEqual(failed['source'],'unavailable')
            self.assertFalse(failed.get('silence_windows'))

    def test_unresolved_cloud_never_falls_through_to_full_asr(self):
        with patch.object(alignment.bailian_asr,'key',return_value='test'),patch.object(alignment.transcription_cache,'transcribe', return_value='pending result'):
            with self.assertRaises(alignment.AlignmentPending):
                alignment.acquire(self.job,self.cfg,self.windows)
        self.assertTrue(list((Path(self.cfg['shared_root'])/'albums/7/timed-audio-windows').glob('*/config.json')))

    def test_local_recognizer_receives_separate_short_windows(self):
        import resident_asr
        self.write_audio(array.array('h',[90,-130]*8000*56).tobytes())
        windows=[(2_000_000,16_000_000),(38_000_000,52_000_000)]
        received=[]
        def infer(ident,model,settings,clip,path,prompt):
            duration=alignment.subtitle_review.duration_us(clip)
            self.assertLessEqual(duration,13_400_000)
            received.append(duration)
            atomic_json(path,{'text':'好','timestamp':[[1100,1400]]})
        from unittest.mock import Mock
        pool=Mock();pool.transcribe.side_effect=infer
        cfg={**self.cfg,'caption_probe_asr':{'backend':'funasr','model':'test'}}
        with patch.object(resident_asr,'ACTIVE',pool),patch.object(alignment.asr_backend,'identity',return_value={'backend':'funasr','model_sha256':'test'}):
            data=alignment.acquire(self.job,cfg,windows)
            self.assertEqual(len(received),4)
            self.assertEqual(data['source'],'local_funasr')
            self.assertEqual([x['start_us'] for x in data['words']],[2400000,14400000,38400000,50400000])
            self.assertEqual(alignment.acquire(self.job,cfg,windows)['words'],data['words'])
            self.assertEqual(len(received),4)
        pool.close.assert_not_called()

    def test_rejects_out_of_audio_window(self):
        with self.assertRaises(ValueError):
            alignment.acquire(self.job,self.cfg,[(-10000,130000)])

if __name__ == '__main__':
    unittest.main()
