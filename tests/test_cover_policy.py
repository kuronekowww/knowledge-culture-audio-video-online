import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from artifacts import atomic_json, read_json
import cover_policy as cover
import creative_options
import team_visuals
import title_review


class CoverPolicyTests(unittest.TestCase):
    def setUp(self):
        self.copy={'episode_title':'他为何归来？','cover_title_lines':['他为何','归来？'],
                   'title_line1':'他为何','title_line2':'归来？'}
        self.cfg={'album_id':'1','production_profile':'team-v1','cover_policy_version':2,
                  'creative_options':{'album_tone':'纸本','style_id':'s',
                  'style_candidates':[{'id':'s','name':'古典','reason':'已选择','prompt':'手绘黑金'}]}}
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job=Path(self.temp.name).resolve()
        self.cfg['shared_root']=str(self.job/'shared')
        atomic_json(self.job/'config.json',self.cfg)
        atomic_json(self.job/'copy.json',self.copy)
        atomic_json(self.job/'cut_plan.json',{'track_id':'1','highlight_id':'K1','summary':'原声','clips':[{'start':0,'end':301}]})
        atomic_json(self.job/'visual_bible.json',{'style_prompt':'黄铜质感','cover_scene':'中心陈列打开的木盒'})
        self.layout={'cover_policy':cover.binding(), 'subject_bounds_px':[[80,330,1000,1470]],
                     'focus_point_px':[523,710], 'title_bounds_px':[[160,1140,926,1230],[166,1250,920,1360]],
                     'dominant_core_subject':True,'face_or_core_detail_clear':True}

    def approve(self):
        ticket=title_review.pending(self.job,self.cfg)
        title_review.decide([self.job],[ticket['title_version']],'test fixture')

    def test_one_two_lines_four_and_hidden_newline(self):
        self.assertEqual(cover.title({**self.copy,'cover_title_lines':['他为何归来？']}),'他为何归来？')
        self.assertEqual(cover.title(self.copy),'他为何归来？')
        for lines in [['他','为何','归来？'],['他为\n何','归来？'],['他为何','归来']]:
            with self.assertRaises(ValueError):cover.title({**self.copy,'cover_title_lines':lines})

    def test_optional_label_center_focus_and_optional_accent(self):
        self.assertTrue(cover.validate_review(self.layout,self.copy['cover_title_lines'],self.copy))
        for side,rect in [('left',[80,391,145,583]),('right',[841,391,905,583]),('top',[436,380,650,441])]:
            opt={**self.copy,'album_core_label':'武则天','cover_label_placement':side}
            layout={**self.layout,'core_label':'武则天','core_label_exact':True,'core_label_placement':side,
                    'core_label_bounds_px':[rect],'core_label_max_glyph_height_px':45}
            self.assertTrue(cover.validate_review(layout,opt['cover_title_lines'],opt))
            with self.assertRaises(ValueError):cover.validate_review(layout,self.copy['cover_title_lines'],self.copy)
        for layout in [{**self.layout,'focus_point_px':[96,396]},
                       {**self.layout,'subject_bounds_px':[[100,210,1010,1650]]},
                       {**self.layout,'title_bounds_px':[[90,1100,990,1360],[90,1340,990,1410]]},
                       {**self.layout,'face_or_core_detail_clear':False}]:
            with self.assertRaises(ValueError):cover.validate_review(layout,self.copy['cover_title_lines'],self.copy)
        monochrome={**self.layout,'title_line_colors':['#523c24','#564029']}
        self.assertTrue(cover.validate_review(monochrome,expected_copy={**self.copy,'cover_accent_line':0}))
        with self.assertRaises(ValueError):cover.validate_review(monochrome,expected_copy={**self.copy,'cover_accent_line':1})
        accent={**self.layout,'title_line_colors':['#ff5633','#fff1b0'],'accent_line':1}
        self.assertTrue(cover.validate_review(accent,expected_copy={**self.copy,'cover_accent_line':1}))

    def test_provider_coordinates_and_label_omission(self):
        p,_=cover.settings()
        self.assertEqual(cover.source_box([0,0,1080,1920]),[0,0,1024,1536])
        self.assertEqual(cover.source_box(p['cover_center_crop_rect_px']),[0,86,1024,1450])
        self.assertEqual(cover.source_box(p['cover_safe_rect_px']),[52,86,972,1450])
        prompt=cover.prompt(self.copy,design={'core_label':'旧名','composition':'旧构图','visual_tone':'墨青'})
        self.assertIn('省略专辑简称',prompt);self.assertNotIn('旧名',prompt)
        self.assertIn('墨青',prompt);self.assertNotIn('旧构图',prompt);self.assertNotIn('1080×1920',prompt)

    def test_raised_block_single_or_double_checks_position_and_focus(self):
        p,c=cover.settings()
        rect=cover.title_box(p,c)
        self.assertEqual([1060-rect[1],1650-rect[3]],[288,288])
        self.assertEqual(c['episode_title']['group_center_y_px'],1203)
        self.assertIn('998px',cover.prompt(self.copy))
        for row in ([[120,1150,960,1256]], [[120,1100,960,1190],[120,1214,960,1306]]):
            with self.subTest(row=row):
                self.assertTrue(cover.validate_review({**self.layout,'title_bounds_px':row}))
        with self.assertRaises(ValueError):
            cover.validate_review({**self.layout,'title_bounds_px':[[120,800,960,920]]})
        with self.assertRaisesRegex(ValueError,'遮挡'):
            cover.validate_review({**self.layout,'title_bounds_px':[[120,1100,960,1190],[120,1214,960,1306]],
                                   'focus_point_px':[540,1130]})
        with self.assertRaises(ValueError):
            cover.validate_review({**self.layout,'title_bounds_px':[[80,1385,995,1480],[90,1500,993,1597]]})

    def test_r24_image_receipt_pins_request_but_unsubmitted_recipe_moves_up(self):
        import generate_images
        name=self.job/'image_manifest.json'
        prefix='【统一画风（用户选择）】手绘黑金\n'
        suffix=cover.prompt(self.copy,previous=True)
        row={'id':'cover_portrait','filename':'cover.png','size':'1024x1536','cover_fit':'full_width_9x16',
             'prompt':prefix+'中心陈列打开的木盒'+suffix}
        old={'output_dir':'images/generated','quality':'low','images':[row], 'style_prefix':prefix,
             'style_selection_hash':creative_options.style_hash(self.cfg),'cover_title_suffix':suffix,
             'cover_options':cover.options(self.copy),'cover_title_hash':cover.title_hash(self.copy),
             'cover_core_label':'','cover_policy':cover.R24_BINDING}
        atomic_json(name,old)
        creative_options.bind_prompts(self.job,self.cfg)
        fresh=read_json(name)
        self.assertEqual(fresh['cover_policy'],cover.binding())
        self.assertIn('998px',fresh['images'][0]['prompt'])
        atomic_json(name,old)
        key=generate_images.image_fingerprint(generate_images.DEFAULT_API_BASE,row,'low')
        atomic_json(self.job/'images/generated/.receipts/cover_portrait.json',{'fingerprint':key})
        creative_options.bind_prompts(self.job,self.cfg)
        pinned=read_json(name)
        self.assertEqual(pinned['cover_policy'],cover.R24_BINDING)
        self.assertEqual(pinned['images'][0],row)
        creative_options.check_bound(self.job,self.cfg)
        review={**self.layout,'cover_policy':cover.R24_BINDING,
                'title_bounds_px':[[80,1385,995,1480],[90,1500,993,1597]]}
        self.assertTrue(cover.validate_review(review,expected_copy=self.copy,previous=True))

    def test_r24_web_recipe_resumes_uncertain_original_request(self):
        import generate_images
        self.approve()
        path=self.job/'web-cover-manifest.json'
        row={'id':'cover_portrait','filename':'cover_portrait.png','size':'1024x1536','cover_fit':'full_width_9x16',
             'prompt':'【统一画风】黄铜质感\n中心陈列打开的木盒'+cover.prompt(self.copy,previous=True)}
        old={'compiled_by':'team-v1','approved_title_version':title_review.approved_version(self.job,self.cfg),
             'cover_policy':cover.R24_BINDING,'output_dir':'images/generated','quality':'low','images':[row]}
        atomic_json(path,old)
        key=generate_images.image_fingerprint(generate_images.DEFAULT_API_BASE,row,'low')
        atomic_json(self.job/'images/generated/.cache'/key/'request.json',{'status':'generation_uncertain','fingerprint':key})
        team_visuals.compile_web_cover(self.job,self.cfg)
        self.assertEqual(read_json(path)['images'][0],row)
        self.assertEqual(read_json(path)['cover_policy'],cover.R24_BINDING)
        atomic_json(self.job/'visual_bible.json',{'style_prompt':'黄铜质感','cover_scene':'书房里在桌前阅读的人'})
        team_visuals.compile_web_cover(self.job,self.cfg)
        self.assertEqual(read_json(path)['cover_policy'],cover.binding())

    def test_manifest_compilation_is_idempotent(self):
        path=self.job/'image_manifest.json'
        atomic_json(path,{'images':[{'id':'cover_portrait','prompt':'木盒主像'},
                                    {'id':'SH001','prompt':'日常情境'}]})
        creative_options.bind_prompts(self.job,self.cfg)
        raw=read_json(path);first=raw['images'][0]
        self.assertEqual((first['size'],first['cover_fit']),('1024x1536','full_width_9x16'))
        before=path.read_bytes()
        creative_options.bind_prompts(self.job,self.cfg);creative_options.check_bound(self.job,self.cfg)
        self.assertEqual(before,path.read_bytes())
        self.assertNotIn('本集封面',raw['images'][1]['prompt'])
        atomic_json(self.job/'copy.json',{**self.copy,'album_core_label':'新名'})
        with self.assertRaises(ValueError):creative_options.check_bound(self.job,self.cfg)

    def test_existing_three_line_recipe_only_keeps_original_title(self):
        cfg={**self.cfg};cfg.pop('cover_policy_version')
        text={**self.copy,'cover_title_lines':['他','为何','归来？']}
        atomic_json(self.job/'copy.json',text)
        raw={'cover_policy':cover.PREVIOUS_BINDING,'cover_title_suffix':'历史构图',
             'cover_title_hash':cover.title_hash(text),'cover_core_label':'旧名',
             'images':[{'id':'cover_portrait','size':'1080x1920','prompt':'主体历史构图'}]}
        atomic_json(self.job/'image_manifest.json',raw)
        self.assertTrue(cover.legacy(self.job,cfg));self.assertFalse(cover.legacy(self.job,self.cfg))
        creative_options.bind_prompts(self.job,cfg)
        saved=read_json(self.job/'image_manifest.json')
        self.assertEqual(saved['cover_policy'],cover.PREVIOUS_BINDING)
        self.assertEqual(saved['images'][0]['size'],'1080x1920')
        self.assertNotIn('cover_fit',saved['images'][0])
        atomic_json(self.job/'copy.json',{**text,'episode_title':'改字'})
        self.assertFalse(cover.legacy(self.job,cfg))

    def test_web_and_image_paths_bind_receipt_to_current_cover(self):
        with self.assertRaises(ValueError):team_visuals.compile_web_cover(self.job,self.cfg)
        with self.assertRaises(ValueError):team_visuals.compile_job(self.job,self.cfg)
        self.approve()
        compiled=team_visuals.compile_web_cover(self.job,self.cfg)
        row=read_json(Path(compiled['manifest']))['images'][0]
        self.assertEqual((row['size'],row['cover_fit']),('1024x1536','full_width_9x16'))
        image=self.job/'cover.png'
        Image.new('RGB',(1080,1920),'navy').save(image)
        request=cover.web_review_request(self.job,self.cfg,image)
        preview=cover.crop_preview(self.job,image,request['basis']['image_sha256'])
        with Image.open(preview) as im:self.assertEqual(im.size,(540,720))
        report={'basis':request['basis'],'title_exact':True,'reviewer':'fixture','evidence':'fixture','layout':self.layout}
        atomic_json(self.job/'web-cover-review.json',report)
        cover.validate_web_cover(self.job,self.cfg,image)
        Image.new('RGB',(1080,1920),'red').save(image)
        with self.assertRaises(ValueError):cover.validate_web_cover(self.job,self.cfg,image)
        atomic_json(self.job/'copy.json',{**self.copy,'cover_title_lines':['他为何归来？']})
        with self.assertRaises(ValueError):team_visuals.compile_web_cover(self.job,self.cfg)


if __name__=='__main__':
    unittest.main()
