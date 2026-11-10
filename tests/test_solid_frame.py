from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageColor
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from artifacts import file_hash
import layout_profile
import solid_frame


class FrameTests(unittest.TestCase):
    def test_one_stable_color_contrasts_with_story_and_type(self):
        policy=layout_profile.load()['body_frame']
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            pictures=[]
            for idx in range(20):
                p=folder/f'{idx}.png'
                Image.new('RGB',(60,40),(210+idx,145,82)).save(p)
                pictures.append({'id':str(idx),'path':p})
            frame=solid_frame.materialize(folder,pictures)
            self.assertEqual(len(frame['sampled_scenes']),7)
            self.assertEqual([frame['sampled_scenes'][i]['id'] for i in (0,-1)],['0','19'])
            with Image.open(frame['path']) as im:
                self.assertEqual(im.size,(1080,1920))
                self.assertEqual(im.getcolors(1),[(1080*1920,ImageColor.getrgb(frame['color']))])
            for fg in policy['foreground_colors']:
                self.assertGreaterEqual(solid_frame.contrast(ImageColor.getrgb(fg),ImageColor.getrgb(frame['color'])),4.5)
            stamp=Path(frame['path']).stat().st_mtime_ns
            self.assertEqual(frame,solid_frame.materialize(folder,pictures))
            self.assertEqual(stamp,Path(frame['path']).stat().st_mtime_ns)
            Image.new('RGB',(60,40),'#000000').save(pictures[0]['path'])
            self.assertNotEqual(frame['key'],solid_frame.materialize(folder,pictures)['key'])

    def test_dark_scene_uses_a_different_surround(self):
        self.assertNotEqual(solid_frame.choose_color([(8,8,8)]),solid_frame.choose_color([(218,210,196)]))

    def test_static_record_is_single_visible_rgba_frame(self):
        profile=layout_profile.load()
        p=ROOT/profile['playback']['asset']
        self.assertEqual(file_hash(p),profile['asset_sha256'][profile['playback']['asset']])
        with Image.open(p) as im:
            self.assertEqual(im.n_frames,1)
            self.assertEqual(im.getchannel('A').getextrema(),(0,255))
