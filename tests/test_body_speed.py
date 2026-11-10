"""Probe actual tempo, pitch, and the final audio clock for all public speed choices."""
from array import array
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from artifacts import atomic_json, file_hash, read_json
import content_selection
import entry_preflight
import flow
import run
import subtitle_review
import title_review


def source(path):
    """420 s voice-range tone with identifying 1 kHz markers at source 70 and 350."""
    rate=8000
    tone = lambda hz: array('h',(round(11000*math.sin(math.tau*hz*i/rate)) for i in range(rate))).tobytes()
    low,high=tone(440),tone(1000)
    with wave.open(str(path),'wb') as fp:
        fp.setparams((1,2,rate,0,'NONE',''))
        for second in range(420):
            fp.writeframesraw(high if second in (70,350,419) else low)


def measured_hz(path, at, seconds=.2):
    data=subprocess.check_output(['ffmpeg','-v','error','-ss',f'{at:.5f}','-i',str(path),'-t',str(seconds),
                                  '-ar','8000','-ac','1','-f','s16le','-'])
    samples=array('h');samples.frombytes(data)
    rises=sum(a<0<=b for a,b in zip(samples,samples[1:]))
    return rises*8000/len(samples)


def plan(audio, *, selection='full_track'):
    content={'is_complete':True,'is_intro_only':False,'complete_reason':'正文有完整论证',
             'problem':'问题','demand_reason':'听众需求','delivered_value':'解释','conclusion':'结论',
             'opening_hook':'问题','return_hook_omission_reason':'全文自然结尾',
             'evidence':[{'start':1,'end':2,'text':'本段内容'}]}
    return {'track_id':'5','highlight_id':'K1','summary':'本集','selection_mode':selection,
            'clips':[{'start':0,'end':420}],'source_audio':str(audio),'content':content}


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs local FFmpeg')
class SpeedTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.job=Path(t.name).resolve()
        self.audio=self.job/'source.wav';source(self.audio)

    def test_all_tempos_keep_pitch_and_markers_on_final_clock(self):
        original=file_hash(self.audio)
        hashes=[]
        for speed,expected in [(1,420),(1.2,350),(1.4,300)]:
            with self.subTest(speed=speed):
                row={**plan(self.audio),'speed':speed}
                atomic_json(self.job/'cut_plan.json',row)
                report=entry_preflight.inspect(row,self.job)
                self.assertTrue(report['ok'],report)
                self.assertEqual(report['expected_body_seconds'],expected)
                self.assertEqual(run.stage_cut_speed(self.job, {}),'')
                audio=subtitle_review.audio_path(self.job)
                result=read_json(self.job/'cut_result.json')
                self.assertEqual(result['speed'],speed)
                self.assertAlmostEqual(subtitle_review.duration_us(audio)/1e6,expected,delta=.002)
                self.assertAlmostEqual(measured_hz(audio,10),440,delta=6)
                for at in (70,350,419):
                    self.assertAlmostEqual(measured_hz(audio,at/speed+.08),1000,delta=6)
                self.assertEqual(audio==self.audio,speed==1)
                content_selection.require_current_speed(self.job,{})
                if speed != 1:
                    # A transcript from the unaccelerated source must not pass the provenance check.
                    transcript=self.job/'final_transcript.json'
                    atomic_json(transcript,{'segments':[]})
                    atomic_json(self.job/'final_transcript.meta.json',{'audio_sha256':original,
                                'transcript_sha256':file_hash(transcript)})
                    with self.assertRaisesRegex(ValueError,'ASR来源未绑定当前音频'):
                        subtitle_review.current_basis(self.job,{})
                hashes.append(file_hash(audio))
        self.assertEqual(file_hash(self.audio),original)
        self.assertEqual(len(set(hashes)),3)
        atomic_json(self.job/'cut_plan.json',{**plan(self.audio),'speed':1})
        with self.assertRaisesRegex(ValueError,'倍速已变更'):
            content_selection.require_current_speed(self.job,{})

    def test_pitched_splice_uses_source_cuts_and_final_boundaries(self):
        # Remove one sourced interval. A six-minute body can run at up to 1.2.
        row=plan(self.audio,selection='excerpt')
        row.update(speed=1.2,clips=[{'start':0,'end':180},{'start':240,'end':420}])
        row['content'].update(splice_reason='连续的两个相关论点',
            return_hook={'question':'延伸问题','evidence':'源文稿','track_id':'5','start':200,'end':202})
        atomic_json(self.job/'cut_plan.json',row)
        self.assertEqual(run.stage_cut_speed(self.job, {}),'')
        meta=read_json(self.job/'cut_result.json')['segments'][0]
        self.assertEqual([(c['output_start'],c['output_end']) for c in meta['clips']],[(0,150),(150,300)])
        self.assertAlmostEqual(subtitle_review.duration_us(subtitle_review.audio_path(self.job))/1e6,300,delta=.002)
        second_marker=150+(350-240)/1.2
        self.assertAlmostEqual(measured_hz(subtitle_review.audio_path(self.job),second_marker+.07),1000,delta=6)

    def test_selection_default_and_item_override_are_visible_before_production(self):
        cfg={'speed':1.2,'shared_root':str(self.job/'shared'),
             'presentation':{'mode':'hyperframes','choice_evidence':'测试'}}
        first=plan(self.audio);second={**first,'highlight_id':'K2','speed':1.4}
        data={'album_id':'8','config':cfg,'segments':[first,second]}
        input_path=self.job/'selection.json';manifest=self.job/'batch.json';atomic_json(input_path,data)
        flow.create_selection(input_path,manifest)
        for job,expected in zip(flow.jobs_in(manifest),(1.2,1.4)):
            cfg=read_json(job/'config.json')
            self.assertEqual(read_json(job/'cut_plan.json')['speed'],expected)
            atomic_json(job/'copy.json',{'episode_title':'为何归来？','cover_title_lines':['为何','归来？'],
                                         'title_line1':'为何','title_line2':'归来？'})
            proposal=title_review.pending(job,cfg)
            self.assertEqual(proposal['speed'],expected)
            self.assertEqual([o['speed'] for o in proposal['available_speeds']],list(content_selection.SPEEDS))
            self.assertFalse((job/'title_approval.json').exists())

    def test_request_outside_budget_reports_only_available_choices(self):
        row=plan(self.audio,selection='excerpt')
        row.update(clips=[{'start':0,'end':355}],speed=1.2)
        report=entry_preflight.inspect(row,self.job)
        self.assertFalse(report['ok']);self.assertEqual(report['available_speeds'],[{'speed':1,'body_seconds':355}])
        self.assertIn('可选档为1倍',str(report['errors']))
        for invalid in (False,True,0,-1,1.1,1.6,2,float('nan'),float('inf'),None):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):content_selection.speed({'speed':invalid})
