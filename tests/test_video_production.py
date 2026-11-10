"""Behavioural regressions for source, handoff, preview and incremental review."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from artifacts import atomic_json, file_hash, read_json
import content_selection, delivery, entry_preflight, flow, layout_profile, opening_review, runtime_doctor, shared_album, subtitle_candidates, web_quality


def content():
    return dict(is_complete=True,is_intro_only=False,complete_reason='测试：解释和应用均在本集',problem='问题',demand_reason='需要',delivered_value='方法',conclusion='结论',opening_hook='开头',evidence=[dict(start=1,end=2,text='证据')],return_hook_omission_reason='本集结束')

def wave_audio(folder,name,length):
    p=folder/name
    with wave.open(str(p),'wb') as f:
        f.setparams((1,1,1000,0,'NONE',''));f.writeframes(bytes([128])*round(length*1000))
    return p

def project_inputs(p):
    (p/'assets').mkdir();(p/'assets/brand.svg').write_text('<svg/>')
    for name in ('opening.js','content.js','runtime.js','project-data.js','layout.css','index.html'):(p/name).write_text(name)
    atomic_json(p/'runtime-binding.json',{'node':'node','cli':'hf'})
    atomic_json(p/'project-data.json',{'duration':413,'bodyDuration':413})
    atomic_json(p/'caption-display.json',[{'id':'S1','start':3,'end':4,'text':'一','display_text':'一'}])
    atomic_json(p/'opening-plan.json',{'start':1,'end':10,'actions':[]})

class ProductionTests(unittest.TestCase):
    def test_candidate_batch_probes_all_lengths_without_creating_jobs(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);rows=[]
            for i,n in enumerate((236,280,413.429,320)):
                c=content()
                if i==3:c.update(is_complete=False,is_intro_only=True,complete_reason='只有节目预告')
                rows.append({'track_id':str(i+1),'source_audio':str(wave_audio(p,str(i)+'.wav',n)),'content':c})
            report=entry_preflight.selection({'candidates':rows},p)
            self.assertEqual([t['ok'] for t in report['tracks']],[False,False,True,False])
            self.assertEqual([t['actual_seconds'] for t in report['tracks']],[236.,280.,413.429,320.])
            self.assertIn('只有前言',''.join(report['tracks'][-1]['errors']))
            self.assertFalse((p/'jobs').exists())

    def test_no_short_full_track_exception_or_partial_disguised_as_full(self):
        plan={'selection_mode':'full_track','clips':[{'start':0,'end':300}],'content':content()}
        with self.assertRaises(ValueError):content_selection.validate_source(plan,299.99)
        with self.assertRaises(ValueError):content_selection.validate_source(plan,413)
        with self.assertRaises(ValueError):content_selection.validate({**plan,'speed':.99})

    def test_actual_body_threshold_applies_even_to_encoding_roundoff(self):
        content_selection.duration_ok(300.0)
        with self.assertRaises(ValueError):content_selection.duration_ok(299.999999)
        plan={'selection_mode':'excerpt','clips':[{'start':0,'end':301.03}]}
        with self.assertRaises(ValueError):content_selection.validate_source(plan,301.0)

    def test_full_track_hook_omission_does_not_weaken_excerpt(self):
        plan={'track_id':'1','selection_mode':'full_track','clips':[{'start':0,'end':413}],'content':content()}
        self.assertEqual(content_selection.validate(plan)['duration_seconds'],413)
        for patch_ in ({'selection_mode':'excerpt'},{'content':{**content(),'is_intro_only':True}},
                       {'content':{**content(),'return_hook_omission_reason':''}},
                       {'content':{**content(),'return_hook':dict(track_id='1',start=1,end=2,question='?',evidence='已播放')}}):
            with self.subTest(patch_=patch_),self.assertRaises(ValueError):content_selection.validate({**plan,**patch_})

    def test_default_project_delivery_and_recorded_override(self):
        self.assertEqual(delivery.requested({})['target'],'project')
        self.assertEqual(delivery.requested({'delivery':{'target':'project'}})['target'],'project')
        self.assertEqual(delivery.requested({'delivery':{'target':'mp4','choice_evidence':'用户要求渲染MP4'}})['target'],'mp4')
        self.assertEqual(delivery.requested({'delivery':{'target':'preview','choice_evidence':'只看预览'}})['target'],'preview')
        with self.assertRaises(ValueError):delivery.requested({'delivery':{'target':'mp4'}})

    def test_default_project_never_starts_companion_renderer(self):
        import assemble_video
        with tempfile.TemporaryDirectory() as td:
            job=Path(td)
            atomic_json(job/'config.json',{'openfilm':{'project_dir':'configured-renderer'}})
            with patch.object(sys,'argv',['assemble_video','--job-dir',str(job)]), patch.object(assemble_video.subprocess,'run') as run:
                run.return_value.returncode=0
                self.assertEqual(assemble_video.main(),0)
                self.assertEqual(run.call_count,1)
                self.assertTrue(run.call_args.args[0][1].endswith('assemble_draft.py'))

    def test_native_export_stale_or_missing_mp4_never_completes_a_draft(self):
        import verify_native_export as native
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);draft={'draft_dir':str(p/'editable')}
            for n in ('draft_result.json','handoff_plan.json','upload_request.json','review.json'):atomic_json(p/n,{})
            mp4=p/'out.mp4';mp4.write_bytes(b'binding fixture')
            r={'status':'render_verified','draft_dir':draft['draft_dir'],'inputs':native.inputs(p),'output':str(mp4),
               'output_sha256':file_hash(mp4),'review':str(p/'review.json'),'review_sha256':file_hash(p/'review.json')}
            atomic_json(p/'native_export_result.json',r)
            self.assertEqual(native.current(p,draft),r)
            atomic_json(p/'handoff_plan.json',{'version':2});self.assertIsNone(native.current(p,draft))
            atomic_json(p/'handoff_plan.json',{});mp4.unlink();self.assertIsNone(native.current(p,draft))

    def test_full_track_through_selection_without_image_style(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);audio=wave_audio(p,'source.wav',300.25)
            plan={'selection_mode':'full_track','source_audio':'source.wav','track_id':'7','highlight_id':'whole','summary':'单集','content':content()}
            data={'config':{'shared_root':str(p/'shared'),'presentation':{'mode':'hyperframes','choice_evidence':'网页'}},'album_id':'4','segments':[plan]}
            atomic_json(p/'selection.json',data);flow.create_selection(p/'selection.json',p/'batch.json')
            job=flow.jobs_in(p/'batch.json')[0];stored=read_json(job/'cut_plan.json')
            self.assertEqual(stored['clips'],[{'start':0,'end':300.25}])
            import run
            self.assertEqual(run.stage_cut_speed(job,read_json(job/'config.json')),'')
            result=read_json(job/'cut_result.json');self.assertTrue(Path(result['segments'][0]['path']).samefile(audio))
            self.assertEqual(result['processing'],'original_bytes')

    def test_user_manuscript_is_used_for_candidate_differences_and_versioned(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);doc=p/'ref.txt';doc.write_text('钥匙');cfg={'album_id':'3','shared_root':str(p),'user_transcript_path':str(doc)};atomic_json(p/'cut_plan.json',{'track_id':'1'})
            ctx=shared_album.selected_context(p,cfg);old=ctx['supplied_document']['sha256']
            _,blocks,_,_,diff=subtitle_candidates.document_map([{'text':'铁锤'}],ctx)
            self.assertEqual(blocks[0]['text'],'钥匙');self.assertTrue(diff)
            doc.write_text('纸门');self.assertNotEqual(shared_album.selected_context(p,cfg)['supplied_document']['sha256'],old)

    def test_layout_cli_uses_short_display_name_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'copy.json';copy={'album_name':'墨菲定律丨案例','album_display_name':'墨菲定律','title_line1':'怎样打开','title_line2':'一把锁'};atomic_json(p,copy)
            r=json.loads(subprocess.check_output([sys.executable,str(ROOT/'scripts/layout_profile.py'),'--check','--copy',str(p)]))
            self.assertEqual(r['text_fit']['album'][0],'墨菲定律');self.assertEqual(read_json(p),copy)
            with self.assertRaises(ValueError):layout_profile.fit('整段话\n字','subtitle')

    def test_doctor_rejects_broken_executable_even_if_link_exists(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);broken=p/'ffprobe';broken.symlink_to(p/'no-binary')
            with self.assertRaises(ValueError):runtime_doctor.command([str(broken),'-version'])

    def test_changed_caption_checks_old_and_new_times_then_brand_expands_scope(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);project_inputs(p);atomic_json(p/'qa/last-inputs.json',web_quality.fingerprint(p))
            atomic_json(p/'caption-display.json',[{'id':'S1','start':4,'end':5,'text':'二','display_text':'二'}])
            planned=web_quality.plan(p);self.assertEqual(planned['scope'],'subtitles');self.assertIn('3.5',planned['args'][1]);self.assertIn('4.5',planned['args'][1])
            (p/'assets/brand.svg').write_text('<svg width="2"/>');self.assertEqual(web_quality.plan(p)['scope'],'full')

    def test_opening_change_scopes_to_opening_and_seam(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);project_inputs(p);atomic_json(p/'qa/last-inputs.json',web_quality.fingerprint(p))
            (p/'opening.js').write_text('updated opening')
            planned=web_quality.plan(p);self.assertEqual(planned['scope'],'opening');self.assertIn('0.9,10.1',planned['args'])
            self.assertEqual(web_quality.plan(p,'full')['scope'],'full')

    def test_word_anchors_and_visual_review_are_separate_gates(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);project_inputs(p);job=p/'job';job.mkdir()
            atom={'id':'U1','text':'开','start_us':2000000,'end_us':2200000,'timing_source':'word'}
            atomic_json(job/'subtitle_review_request.json',{'units':[atom]})
            action={k:'纸层' for k in ('id','subject','initial_state','contact','operation','result_state','constraints')}
            action['anchor']={'unit_ids':['U1'],'text':'开','start_us':2000000,'end_us':2200000}
            plan={'start':1,'end':3,'message':'揭开','style':'paper-cut','style_signals':['纸层','折页'],'transition_to_body':'纸面展开','actions':[action]}
            atomic_json(p/'opening-plan.json',plan);r=opening_review.validate(p,job);self.assertEqual(r['status'],'needs_visual_review')
            action['anchor']['start_us']+=1;atomic_json(p/'opening-plan.json',plan);self.assertEqual(opening_review.validate(p,job)['status'],'invalid')

if __name__=='__main__':unittest.main()
