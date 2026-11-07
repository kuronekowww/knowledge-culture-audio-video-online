import copy
import json
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cover_policy as cover
import creative_options
from artifacts import atomic_json,read_json


class CoverPolicyTests(unittest.TestCase):
    def setUp(self):
        self.copy={'episode_title':'武则天的父亲请袁天罡看相，他为啥不敢去？',
            'cover_title_lines':['武则天的父亲请袁天罡看相，','他为啥不敢去？'],'album_core_label':'武则天'}

    def review(self):
        return {'cover_policy':cover.binding(),'title_bounds_px':[[94,1150,986,1250],[160,1280,920,1425]],
            'title_line_colors':['#ff5633','#fff1b0'],'core_label_placement':'right',
            'core_label_bounds_px':[[850,380,990,820]],'core_label_max_glyph_height_px':140,
            'dominant_core_subject':True,'face_or_core_detail_clear':True}

    def test_two_lines_preserve_complete_title(self):
        self.assertEqual(cover.title(self.copy),self.copy['episode_title'])
        bad=copy.deepcopy(self.copy);bad['cover_title_lines']=['武则天的父亲','请袁天罡看相，','他为啥不敢去？']
        self.assertEqual(cover.title(bad),self.copy['episode_title'])
        bad['cover_title_lines']=['武则天的父亲','请袁天罡','看相，','他为啥不敢去？']
        with self.assertRaisesRegex(ValueError,'两行或三行'):cover.title(bad)

    def test_hidden_newline_and_dropped_words_rejected(self):
        bad=copy.deepcopy(self.copy);bad['cover_title_lines'][0]='武则天的父亲\n请袁天罡看相，'
        with self.assertRaises(ValueError):cover.title(bad)
        bad=copy.deepcopy(self.copy);bad['cover_title_lines'][0]='父亲请袁天罡看相，'
        with self.assertRaisesRegex(ValueError,'增删字'):cover.title(bad)

    def test_prompt_uses_reference_but_excludes_stale_geometry(self):
        text=cover.prompt(self.copy,'武则天',{'core_subject':'武则天主像','visual_tone':'黑金古典手绘',
            'composition':'旧版标题三行','typography':'旧版专辑超大字'})
        self.assertIn('允许两行或三行',text);self.assertIn('#ff5633',text);self.assertIn('#fff1b0',text)
        self.assertIn('黑金古典手绘',text);self.assertNotIn('旧版标题三行',text);self.assertNotIn('旧版专辑超大字',text)

    def test_review_checks_two_colors_width_and_short_name(self):
        self.assertTrue(cover.validate_review(self.review()))
        three=self.review();three['title_bounds_px']=[[170,1080,910,1180],[170,1200,910,1300],[100,1320,980,1425]]
        three['title_line_colors']=['#ff5633','#fff1b0','#fff1b0']
        self.assertTrue(cover.validate_review(three))
        with self.assertRaises(ValueError):cover.validate_review(three,self.copy['cover_title_lines'])
        for field,value in [('title_line_colors',['#fff1b0','#fff1b0']),
            ('title_bounds_px',[[54,1150,1026,1250],[160,1280,920,1425]]),
            ('core_label_max_glyph_height_px',200),('face_or_core_detail_clear',False)]:
            bad=self.review();bad[field]=value
            with self.assertRaises(ValueError,msg=field):cover.validate_review(bad)

    def test_old_review_binding_rejected(self):
        bad=self.review();bad['cover_policy']={'id':'old','sha256':'old'}
        with self.assertRaisesRegex(ValueError,'当前两到三行双色'):cover.validate_review(bad)

    def test_prompt_binding_is_idempotent_and_updates_only_cover_suffix(self):
        cfg={'creative_options':{'album_tone':'黑金古典','style_id':'s',
            'style_candidates':[{'id':'s','name':'古典','reason':'用户要求','prompt':'手绘黑金'}]}}
        with tempfile.TemporaryDirectory() as d,patch('album_visuals.cover_design_record',return_value={}):
            job=Path(d);atomic_json(job/'copy.json',self.copy)
            atomic_json(job/'image_manifest.json',{'images':[{'id':'cover_portrait','prompt':'人物主像'},
                {'id':'SH001','prompt':'正文故事情境'}]})
            creative_options.bind_prompts(job,cfg);one=(job/'image_manifest.json').read_bytes()
            creative_options.bind_prompts(job,cfg);self.assertEqual(one,(job/'image_manifest.json').read_bytes())
            self.assertEqual(creative_options.check_bound(job,cfg)['style']['id'],'s')
            raw=read_json(job/'image_manifest.json');self.assertNotIn('当前封面规则',raw['images'][1]['prompt'])
            raw['cover_policy']={'id':'old','sha256':'old'};atomic_json(job/'image_manifest.json',raw)
            with self.assertRaises(ValueError):creative_options.check_bound(job,cfg)


if __name__=='__main__':unittest.main()
