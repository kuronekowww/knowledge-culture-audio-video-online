import json
from contextlib import ExitStack
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import layout_profile as layout
import config
from artifacts import read_json


class LayoutTests(unittest.TestCase):
    def test_latest_user_positions_and_size_are_shared(self):
        p=layout.load();spec=config.load_spec()
        self.assertEqual(p['boxes_px']['album_cover'],[94,1339,264,1509])
        self.assertEqual(p['boxes_px']['playback'],[177,1339,347,1509])
        self.assertEqual(p['texts']['album']['left_top_px'][0],357)
        for role in ('album_cover','playback','source_label','outro'):
            self.assertEqual(spec['portrait']['safe_area']['slots_px'][role],p['boxes_px'][role])

    def test_spec_has_no_second_set_of_layout_numbers(self):
        p=read_json(layout.ROOT/'spec.json')
        self.assertNotIn('portrait',p);self.assertEqual(set(p['branding']),{'enabled'})
        self.assertNotIn('outro_seconds',p['timing'])

    def test_long_story_title_rejected_without_reducing_font(self):
        with self.assertRaisesRegex(ValueError,'改写文案'):
            layout.fit('武则天的父亲为什么会请袁天罡到家中来看相','title_line1')

    def test_approved_title_fits_including_stroke(self):
        text='武则天的父亲请袁天罡看相';fitted,m=layout.fit(text,'title_line1')
        self.assertEqual(fitted,text);self.assertLessEqual(m['paint_width_px'],900)
        self.assertEqual(layout.resolved_style('title_line1',text)[1]['pixel_style']['font_size_px'],78)

    def test_missing_glyph_is_an_error(self):
        with self.assertRaisesRegex(ValueError,'缺字'):layout.fit('未收录\U00031350','title_line1')

    def test_album_wrap_does_not_erase_characters(self):
        title='武则天：从三岁到八十二岁的传奇故事'
        text,m=layout.fit(title,'album')
        self.assertEqual(text.replace('\n',''),title);self.assertEqual(text.count('\n'),1)
        self.assertLessEqual(m['paint_width_px'],layout.load()['texts']['album']['max_width_px'])

    def test_album_more_than_two_lines_requires_short_name(self):
        with self.assertRaisesRegex(ValueError,'album_display_name'):layout.fit('武则天传奇故事'*15,'album')

    def test_caption_wrap_preserves_all_words(self):
        text='上面写着久闻大名，恳请莅临之类的客套话';shown,_=layout.fit(text,'subtitle')
        self.assertEqual(shown.replace('\n',''),text);self.assertEqual(shown.count('\n'),1)

    def test_palette_depends_on_cover_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'a.png';b=Path(d)/'b.png'
            Image.new('RGB',(40,40),'#e7bb10').save(a);Image.new('RGB',(40,40),'#173bea').save(b)
            one=layout.album_color(a);self.assertEqual(one,layout.album_color(a))
            self.assertNotEqual(one['hex'],layout.album_color(b)['hex'])
            self.assertNotEqual(one['cover_sha256'],layout.album_color(b)['cover_sha256'])

    def test_original_media_not_recomposed_for_jianying(self):
        canvas=layout.load()['canvas'];assets={'s':{'width':1536,'height':1024}}
        actual=layout.media_transform({'asset_id':'s','box_px':[0,600,1080,1320]},assets,canvas)
        self.assertEqual(actual['scale_x'],1);self.assertEqual(actual['transform_x'],0);self.assertEqual(actual['transform_y'],0)

    def test_api_integer_rounding_is_separate_from_precise_position(self):
        assets={'s':{'width':568,'height':72}};row={'asset_id':'s','box_px':layout.load()['boxes_px']['source_label']}
        exact=layout.media_transform(row,assets,layout.load()['canvas']);api=layout.api_media_transform(row,assets,layout.load()['canvas'])
        self.assertIsInstance(api['transform_y'],int)
        self.assertNotEqual(api['transform_y'],exact['transform_y'])

    def test_full_preparer_uses_raw_assets_for_both_backends(self):
        import handoff
        from artifacts import atomic_json,file_hash
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);cover=root/'cover.png';raw=root/'raw.png';bg=root/'bg.png';audio=root/'audio.m4a'
            Image.new('RGB',(1080,1920),'#202020').save(cover)
            Image.new('RGB',(1536,1024),'#402020').save(raw)
            Image.new('RGB',(1024,1536),'#222222').save(bg);audio.write_bytes(b'unchanged source audio')
            record={'path':str(bg),'sha256':file_hash(bg),'revision':1,'reference_mode':'user_image','reference_path':str(cover),'reference_sha256':file_hash(cover)}
            images={'cover_portrait':{'path':str(cover),'sha256':file_hash(cover)},'SH001':{'path':str(raw),'sha256':file_hash(raw)}}
            identity={'highlight_id':'K01','album_id':'1','track_id':'2','summary':'fixture'}
            lines=[]
            with ExitStack() as stack:
                for target,value in [('handoff.gates',identity),('handoff.source_fingerprint','fixture'),('draft_naming.requested','测试专辑_2_测试标题'),
                    ('album_visuals.current',record),('creative_options.check_bound',{}),('config.effective_spec',config.load_spec()),
                    ('subtitle_review.audio_path',audio),('subtitle_review.duration_us',310000000),('content_selection.validate_job',None),
                    ('content_selection.duration_ok',None),('job_versions.image_assets',images),
                    ('run._read_subtitles',[{'start_us':1000000,'end_us':2500000,'text':'原声内容'}])]:
                    stack.enter_context(patch(target,return_value=value))
                for mode in ('openfilm','capcut'):
                    job=root/mode;job.mkdir()
                    atomic_json(job/'config.json',{})
                    atomic_json(job/'copy.json',{'album_name':'测试专辑','episode_title':'测试标题','title_line1':'武则天为何不敢去','title_line2':'他到底在怕什么'})
                    atomic_json(job/'presentation-assets.json',{'album_cover':{'path':str(cover),'sha256':file_hash(cover)}})
                    atomic_json(job/'storyboard.json',{'shots':[{'id':'SH001','start':0,'end':310}]})
                    atomic_json(job/'cut_plan.json',{'content':{}})
                    result=handoff.prepare(job,{'output_backend':mode})
                    assets={x['asset_id']:x for x in result['assets']}
                    line=read_json(job/'handoff_plan.json')['timelines'][0];lines.append(line)
                    self.assertEqual(assets['portrait__SH001']['path'],str(raw.resolve()))
                    self.assertEqual(line['frames'][1]['box_px'],[0,600,1080,1320])
                    self.assertEqual(line['outro']['end_us']-line['outro']['start_us'],3349000)
                    self.assertEqual(line['album_background']['end_us'],line['total_duration_us'])
                    source=next(r for r in line['branding_elements'] if r['role']=='source_label')
                    self.assertEqual(source['start_us'],1000000)
                    self.assertEqual(source['end_us'],line['body_duration_us'])
                self.assertEqual(lines[0],lines[1])


if __name__=='__main__':unittest.main()
