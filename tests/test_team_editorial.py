import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))

from artifacts import atomic_json, read_json
import creative_options
import generate_images
import image_finish
import team_visuals
import title_review


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = Path(self.tmp.name)
        self.cfg = {'album_id':'7','production_profile':'team-v1','shared_root':str(self.job/'shared'),
                    'creative_options':{'style_candidates':[{'id':'S','name':'纸本','reason':'本集选择',
                    'prompt':'纸本质感，暖金色'}],'album_tone':'文化解读','style_id':'S'}}
        self.titles = {'episode_title':'他为何归来？','cover_title_lines':['他为何','归来？'],
                       'title_line1':'他为何','title_line2':'归来？','album_core_label':'归来'}
        self.shot = {'id':'SH01','start':0,'end':7,'source_unit_ids':['U1'],'description':'人物将木盒放回案上',
                     'beat':'归还木盒','subjects':['A']}
        for name, data in {
            'config':self.cfg, 'copy':self.titles,
            'cut_plan':{'highlight_id':'K01','track_id':'123','clips':[{'start':0,'end':301}],'content':{'evidence':[]}},
            'storyboard':{'shots':[self.shot]},
            'subtitle_review_request':{'units':[{'id':'U1','start_us':100,'end_us':500000}]},
            'visual_bible':{'cover_scene':'人物手持木盒','characters':{'A':'灰袍人物'},'setting':'木制案前'},
        }.items():
            atomic_json(self.job/(name+'.json'), data)

    def approve(self):
        request = title_review.pending(self.job, self.cfg)
        self.assertEqual(request['status'],'awaiting_user_title')
        title_review.decide([self.job], [request['title_version']], 'fixture approval')
        self.assertIsNone(title_review.pending(self.job,self.cfg))
        return request['title_version']

    def test_changed_onscreen_title_and_selection_invalidate_only_their_receipt(self):
        first = self.approve()
        atomic_json(self.job/'copy.json',{**self.titles,'album_name':'书名'})
        self.assertIsNone(title_review.pending(self.job,self.cfg))
        atomic_json(self.job/'copy.json',{**self.titles,'title_line1':'他为什么'})
        with self.assertRaises(ValueError):
            title_review.decide([self.job],[first],'stale approval')
        self.approve()
        atomic_json(self.job/'cut_plan.json',{'track_id':'456','highlight_id':'K02'})
        self.assertIsNotNone(title_review.pending(self.job,self.cfg))

    def test_wait_precedes_paid_and_both_assembly_paths(self):
        import run, prepare_web_video, assemble_video
        with patch.object(generate_images,'run_batch',side_effect=AssertionError):
            with self.assertRaises(ValueError):
                run.stage_generate_images(self.job,self.cfg)
        atomic_json(self.job/'config.json',{**self.cfg, 'presentation':{'mode':'hyperframes','choice_evidence':'测试'}})
        with self.assertRaisesRegex(ValueError,'标题'):
            prepare_web_video.prepare(self.job, self.job/'web')
        atomic_json(self.job/'config.json',self.cfg)
        with patch.object(sys,'argv',['assemble_video','--job-dir',str(self.job)]), self.assertRaisesRegex(ValueError,'标题'):
            assemble_video.main()

    def test_compile_deterministic_revisions_and_reject_unrelated_references(self):
        self.approve()
        team_visuals.compile_job(self.job,self.cfg)
        path = self.job/'image_manifest.json'
        raw = read_json(path)
        self.assertEqual(raw['quality'],'low')
        self.assertIn('他为何归来？',raw['images'][0]['prompt'])
        body = raw['images'][1]['prompt']
        self.assertIn('灰袍人物',body)
        self.assertIn('纸本质感',body)
        before = path.stat().st_mtime_ns
        team_visuals.compile_job(self.job,self.cfg)
        self.assertEqual(path.stat().st_mtime_ns,before)
        raw['images'][1]['revision']=2
        atomic_json(path,raw)
        team_visuals.compile_job(self.job,self.cfg)
        self.assertEqual(read_json(path)['images'][1]['revision'],2)
        atomic_json(self.job/'storyboard.json',{'shots':[{**self.shot,'start':8,'end':12}]})
        with self.assertRaisesRegex(ValueError,'不重合'):
            team_visuals.compile_job(self.job,self.cfg)
        self.assertEqual(read_json(path)['images'][1]['prompt'],body)

    def test_web_compiles_only_selected_inserts(self):
        self.approve()
        project = self.job/'web'
        atomic_json(project/'web-animation-context.json',{'job_dir':str(self.job)})
        atomic_json(project/'web-storyboard.json',{'shots':[{'type':'template'}]})
        self.assertEqual(team_visuals.compile_web(self.job,self.cfg,project),{'count':0})
        atomic_json(self.job/'visual_bible.json',{'characters':{'A':'灰袍人物'},'style_prompt':'蓝色水彩'})
        atomic_json(project/'web-storyboard.json',{'shots':[{'type':'template'},{**self.shot,'type':'image','asset_id':'BOX'}]})
        row=team_visuals.compile_web(self.job,self.cfg,project)
        manifest=read_json(Path(row['manifest']))
        self.assertEqual([image['id'] for image in manifest['images']],['BOX'])
        self.assertIn('蓝色水彩',manifest['images'][0]['prompt'])

    def test_actual_cover_body_and_web_requests_share_clean_finish(self):
        # A real album's palette, lighting and props survive inherited paper/film effects.
        self.cfg['creative_options']['style_candidates'][0]['prompt'] = '暖金与朱红、柔和侧光、细腻纸面颗粒及金箔'
        self.titles.update(episode_title='胶片颗粒从哪来？', cover_title_lines=['胶片颗粒','从哪来？'])
        atomic_json(self.job/'copy.json',self.titles)
        self.approve()
        bible = {'cover_scene':'粗糙纸本上的木匣','style_prompt':'warm gold, distressed paper texture',
                 'characters':{'A':'青灰衣冠'},'era_region':'古代'}
        atomic_json(self.job/'visual_bible.json',bible)
        shot = {**self.shot,'description':'人物放回木盒，细碎的噪点，film grain；案头有一小盘稻谷'}
        atomic_json(self.job/'storyboard.json',{'shots':[shot]})
        team_visuals.compile_job(self.job,self.cfg)
        pure = read_json(self.job/'image_manifest.json')['images']
        manifest = team_visuals.compile_web_cover(self.job,self.cfg)['manifest']
        web_cover = read_json(Path(manifest))['images'][0]
        project=self.job/'web'
        atomic_json(project/'web-animation-context.json',{'job_dir':str(self.job)})
        atomic_json(project/'web-storyboard.json',{'shots':[{**shot,'type':'image','asset_id':'box'}]})
        web=team_visuals.compile_web(self.job,self.cfg,project)
        web_row=read_json(Path(web['manifest']))['images'][0]
        self.assertIn('完整标题“胶片颗粒从哪来？”',web_cover['prompt'])
        self.assertIn('第1行逐字：“胶片颗粒”',pure[0]['prompt'])
        self.assertIn('暖金与朱红',pure[1]['prompt'])
        self.assertIn('青灰衣冠',web_row['prompt'])
        self.assertIn('观察细小的颗粒物',image_finish.finish_prompt('观察细小的颗粒物，胶片噪点质感'))
        nested='完整标题“关于“光”的胶片颗粒”'
        self.assertIn(nested,image_finish.finish_prompt(nested+'在下方。'))
        with patch('generate_images.requests.post') as send:
            send.return_value.status_code=200
            send.return_value.json.return_value={'data':[{'url':'https://example.test/image.png'}]}
            for row in [*pure,web_row,web_cover]:
                generate_images.request_image('https://example.test','fixture',row,'low',240)
                sent=send.call_args.kwargs['json']['prompt']
                self.assertEqual(sent,row['prompt'])
                self.assertEqual(sent.count(image_finish.RULE),1)
                for conflict in ('细腻纸面颗粒','粗糙纸本','film grain','distressed paper','细碎的噪点'):
                    self.assertNotIn(conflict,sent)
        self.assertIn('细腻纸面颗粒',self.cfg['creative_options']['style_candidates'][0]['prompt'])

    def test_preexisting_bound_recipes_keep_their_identity(self):
        self.approve()
        team_visuals.compile_job(self.job,self.cfg)
        path=self.job/'image_manifest.json'
        historical=read_json(path)
        historical.pop('image_finish')
        for item in historical['images']:
            item['prompt']=item['prompt'].removesuffix('\n'+image_finish.RULE)
        atomic_json(path,historical)
        team_visuals.compile_job(self.job,self.cfg)
        self.assertEqual(read_json(path), historical)

    def test_legacy_job_does_not_gain_new_checkpoint(self):
        self.assertIsNone(title_review.pending(self.job, {'album_id':'7'}))
