import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import presentation
import prepare_web_video
import run
import storyboard_prepare

def config(mode='hyperframes',**kw):
    return {'presentation':{'mode':mode,'choice_evidence':'测试用明确选择记录','opening_style':'flat-vector',**kw}}

class PresentationTests(unittest.TestCase):
    def test_legacy_images_remain_supported(self):
        self.assertEqual(presentation.selected({})['mode'],'images')

    def test_missing_evidence_rejected(self):
        with self.assertRaises(ValueError):presentation.selected({'presentation':{'mode':'hyperframes'}})

    def test_delivery_backend_is_not_a_presentation_mode(self):
        with self.assertRaises(ValueError):presentation.selected(config('openfilm'))

    def test_actual_other_styles_allowed(self):
        self.assertEqual(presentation.selected(config(opening_style='one-line'))['opening_style'],'one-line')

    def test_unknown_or_path_style_rejected(self):
        for s in ('unknown-style','../../SKILL','/tmp/style'):
            with self.subTest(style=s),self.assertRaises(ValueError):presentation.selected(config(opening_style=s))

    def test_storyboard_does_not_enter_image_work(self):
        with tempfile.TemporaryDirectory() as t, patch('creative_options.selected',side_effect=AssertionError('Image branch was reached')):
            result=run.stage_storyboard(Path(t),config())
            self.assertTrue(result.startswith('AWAITING_REVIEW:'))
            req=json.loads((Path(t)/'web_animation_request.json').read_text())
            self.assertFalse(req['automatic_story_image_batch'])

    def test_paid_generation_entry_is_routed_before_image_validation(self):
        with tempfile.TemporaryDirectory() as t, patch('creative_options.check_bound',side_effect=AssertionError('Image branch was reached')):
            self.assertTrue(run.stage_generate_images(Path(t),config()).startswith('AWAITING_REVIEW:'))

    def test_images_mode_does_not_emit_web_request(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(presentation.image_pipeline_guard(Path(t),config('images')),'')
            self.assertFalse((Path(t)/'web_animation_request.json').exists())

    def test_prepare_collects_missing_inputs_and_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as t:
            job=Path(t)/'job';job.mkdir();(job/'config.json').write_text(json.dumps(config()))
            project=Path(t)/'web'
            with self.assertRaises(ValueError) as caught:prepare_web_video.prepare(job,project)
            for name in ('subtitles.csv','subtitle_review.json','final_transcript.json'):
                self.assertIn(name,str(caught.exception))
            self.assertFalse(project.exists())
            project.mkdir();marker=project/'keep.txt';marker.write_text('manual edits')
            with self.assertRaisesRegex(ValueError,'已存在'):prepare_web_video.prepare(job,project)
            self.assertEqual(marker.read_text(),'manual edits')

    def test_preferred_styles_are_present(self):
        for style in presentation.OPENING_STYLES:
            self.assertTrue((ROOT/'vendor/motion-showreel/references/styles'/f'{style}.md').is_file())

    def test_web_preparation_does_not_reuse_image_quota_or_command(self):
        with tempfile.TemporaryDirectory() as t, patch('subtitle_review.duration_us',return_value=600_000_000):
            cfg=config();cfg['storyboard_prepare_command']=['old-image-only-agent']
            subtitle={'request_hash':'test','audio_path':'audio.m4a','context':{},'units':[], 'basis':{'audio_sha256':'test'}}
            request=storyboard_prepare.request_for(Path(t),cfg,subtitle)
            self.assertNotIn('image_pacing',request)
            self.assertIn('scene_budget',request)
            self.assertIsNone(request['command'])

    def test_legacy_preparation_keeps_existing_command(self):
        with tempfile.TemporaryDirectory() as t, patch('subtitle_review.duration_us',return_value=600_000_000):
            cfg={'storyboard_prepare_command':['existing-agent']}
            subtitle={'request_hash':'test','audio_path':'audio.m4a','context':{},'units':[], 'basis':{'audio_sha256':'test'}}
            request=storyboard_prepare.request_for(Path(t),cfg,subtitle)
            self.assertIn('image_pacing',request)
            self.assertEqual(request['command'],['existing-agent'])

if __name__=='__main__':unittest.main()
