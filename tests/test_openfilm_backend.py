"""Offline guardrails: preserve source media and edits, reject stale handoffs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from artifacts import atomic_json, file_hash, read_json
import openfilm_backend as backend
from assemble_video import selected


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name);self.job=self.root/'job';self.job.mkdir()
        self.project=self.root/'film'
        self.font=self.root/'font.otf';self.font.write_bytes(b'fixture-font-copy-only')
        self.options={}
        assets=[]
        for aid,suffix in [('cover','.png'),('story','.png'),('audio','.m4a')]:
            path=self.root/(aid+suffix);path.write_bytes(('original '+aid).encode())
            assets.append({'asset_id':aid,'path':str(path),'sha256':file_hash(path),'kind':'audio' if aid=='audio' else 'image'})
        self.line={'canvas':{'width':1080,'height':1920},'safe_area':{'rect_px':[54,250,1026,1440],
          'slots_px':{'subtitle':[72,1206,1008,1278]}},'body_duration_us':5_000_000,'total_duration_us':5_000_000,
          'frames':[{'asset_id':'cover','start_us':0,'end_us':1_000_000,'role':'cover'},
                    {'asset_id':'story','start_us':1_000_000,'end_us':5_000_000,'role':'story','shot_id':'K001'}],
          'audio':{'asset_id':'audio','start_us':0,'end_us':5_000_000},
          'subtitles':[{'start_us':1_000_000,'end_us':2_540_000,'text':'人物<&事件'}],
          'texts':[{'role':role,'text':'旧标题','start_us':1000000,'end_us':5000000,'style_reference':{}} for role in ('title_line1','title_line2')],
          'branding_enabled':False,'text_defaults':{},'transform_convention':{'origin':'center','x_positive':'right','y_positive':'up','scale':'full_canvas'},'subtitle_style_reference':{'font_size':12,'scale_x':.85,'scale_y':.85},
          'branding_elements':[],'draft_name':'测试标题'}
        self.plan={'source_version':'fixture','timelines':[self.line]}
        self.request={'source_version':'fixture','assets':assets}
        from PIL import Image
        Image.new('RGB',(1080,1920),'blue').save(self.root/'cover.png')
        self.request['assets'][0]['sha256']=file_hash(self.root/'cover.png')
        atomic_json(self.job/'copy.json',{'album_name':'测试专辑','title_line1':'武则天为何不敢去','title_line2':'他到底在怕什么','episode_title':'他到底在怕什么'})
        atomic_json(self.job/'presentation-assets.json',{'album_cover':{'path':str(self.root/'cover.png'),'sha256':file_hash(self.root/'cover.png')}})
        self.layered_fixture()

    def tearDown(self):self.temp.cleanup()

    def save_inputs(self):
        atomic_json(self.job/'handoff_plan.json',self.plan)
        self.request['plan_sha256']=file_hash(self.job/'handoff_plan.json')
        atomic_json(self.job/'upload_request.json',self.request)

    def test_default_backend_and_explicit_selection(self):
        self.assertEqual(selected({}),'capcut')
        self.assertEqual(selected({},'openfilm'),'openfilm')
        with self.assertRaises(ValueError):selected({'output_backend':'unknown'})

    def test_build_keeps_source_bytes_and_original_clock(self):
        result=backend.build(self.job,self.project,self.options)
        for item in self.request['assets']:
            copied=self.project/result['source_assets'][item['asset_id']]['project_path']
            self.assertEqual(file_hash(copied),item['sha256'])
            self.assertEqual(file_hash(Path(item['path'])),item['sha256'])
        html=(self.project/'film.html').read_text()
        self.assertIn('src="assets/audio.m4a#t=0,5" at="0"',html)
        self.assertIn('width:1080px;height:1920px',html)
        vtt=(self.project/'captions.vtt').read_text()
        self.assertIn('00:00:01.000 --> 00:00:02.540',vtt)
        self.assertIn('人物&lt;&amp;事件',vtt)
        self.assertEqual(result['playback']['mode'],'disabled')

    def test_build_idempotence_and_manual_edit_preservation(self):
        backend.build(self.job,self.project,self.options)
        self.assertEqual(backend.build(self.job,self.project,self.options)['status'],'project_created')
        path=self.project/'film.html';edited=path.read_text()+'<!-- user edit -->';path.write_text(edited)
        self.assertEqual(backend.build(self.job,self.project,self.options)['status'],'project_edited')
        self.assertEqual(path.read_text(),edited)

    def test_changed_plan_requires_new_project(self):
        backend.build(self.job,self.project,self.options)
        self.line['draft_name']='新标题';self.save_inputs()
        with self.assertRaisesRegex(ValueError,'输入或模板版本改变'):backend.build(self.job,self.project,self.options)

    def test_unmanaged_page_is_not_overwritten(self):
        self.project.mkdir();path=self.project/'film.html';path.write_text('<p>User page</p>')
        with self.assertRaisesRegex(ValueError,'目标存在'):backend.build(self.job,self.project,self.options)
        self.assertEqual(path.read_text(),'<p>User page</p>')

    def test_asset_corruption_is_rejected(self):
        Path(self.request['assets'][0]['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'哈希变化'):backend.checked_inputs(self.job)

    def test_stale_plan_and_source_version_are_rejected(self):
        atomic_json(self.job/'handoff_plan.json',{**self.plan,'extra':'change'})
        with self.assertRaisesRegex(ValueError,'交接计划已改变'):backend.checked_inputs(self.job)
        self.save_inputs();self.request['source_version']='other';atomic_json(self.job/'upload_request.json',self.request)
        with self.assertRaisesRegex(ValueError,'同一版本'):backend.checked_inputs(self.job)

    def test_legacy_geometry_is_upgraded_from_unique_profile(self):
        self.line['safe_area']['rect_px']=[50,200,1030,1500];self.save_inputs()
        backend.build(self.job,self.project,self.options)
        import layout_profile
        line=read_json(self.project/'knowledge-timeline.json')
        self.assertEqual(line['layout_profile'],layout_profile.binding())
        self.assertEqual(line['safe_area']['rect_px'],layout_profile.load()['cover_safe_rect_px'])

    def test_gap_and_audio_shift_are_rejected(self):
        self.line['frames'][1]['start_us']=1_100_000;self.save_inputs()
        with self.assertRaisesRegex(ValueError,'不连续'):backend.checked_inputs(self.job)
        self.line['frames'][1]['start_us']=1_000_000;self.line['audio']['start_us']=1_000_000;self.save_inputs()
        with self.assertRaisesRegex(ValueError,'原声音轨'):backend.checked_inputs(self.job)

    def test_first_second_caption_is_rejected(self):
        self.line['subtitles'][0]['start_us']=110_000;self.save_inputs()
        with self.assertRaisesRegex(ValueError,'首秒抑制'):backend.checked_inputs(self.job)

    def test_original_animation_timing_is_preserved(self):
        from PIL import Image
        gif=self.root/'original.gif';sheet=self.root/'sheet.png'
        Image.new('RGBA',(10,10),'red').save(gif,save_all=True,append_images=[Image.new('RGBA',(10,10),'blue')],duration=[100,200],loop=0)
        info=backend.sprite(gif,sheet)
        self.assertEqual(info['durations_ms'],[100,200])
        with Image.open(sheet) as image:self.assertEqual(image.size,(20,10))

    def layered_fixture(self):
        from PIL import Image
        from image_layers import MODE
        raw=self.root/'raw.png';bg=self.root/'background.png'
        Image.new('RGB',(1536,1024),'red').save(raw)
        Image.new('RGB',(1024,1536),'blue').save(bg)
        item=next(x for x in self.request['assets'] if x['asset_id']=='story')
        item.update(path=str(raw),sha256=file_hash(raw),source_sha256=file_hash(raw))
        self.request['assets']=[a for a in self.request['assets'] if a['asset_id']!='background']
        self.request['assets'].append({'asset_id':'background','path':str(bg),'sha256':file_hash(bg),'kind':'image'})
        self.line.update(frame_fit=MODE,album_background={'asset_id':'background','sha256':file_hash(bg)})
        self.save_inputs()
        return raw,bg

    def test_raw_story_and_background_remain_separate_and_byte_identical(self):
        from PIL import Image
        raw,bg=self.layered_fixture()
        result=backend.build(self.job,self.project,{'font_file':str(self.font)})
        line=read_json(self.project/'knowledge-timeline.json')
        self.assertEqual(line['frames'][1]['box_px'],[0,600,1080,1320])
        for aid,source in [('story',raw),('background',bg)]:
            target=self.project/result['source_assets'][aid]['project_path']
            self.assertEqual(file_hash(target),file_hash(source))
            with Image.open(target) as img:self.assertEqual(img.size,(1536,1024) if aid=='story' else (1024,1536))
        film=(self.project/'film.html').read_text()
        self.assertLess(film.index('id="track-pictures"'),film.index('id="track-background"'))
        self.assertEqual(line['album_background']['start_us'],1_000_000)
        self.assertEqual(line['album_background']['end_us'],5_000_000)

    def test_centering_preserves_different_landscape_ratios(self):
        from PIL import Image
        from image_layers import centered_box
        raw=self.root/'wide.png';Image.new('RGB',(1600,900),'red').save(raw)
        box,size=centered_box(raw,self.line['canvas'])
        self.assertEqual(box,[0,656.25,1080,1263.75])
        self.assertEqual(size,[1600,900])
        Image.new('RGB',(900,1600),'red').save(raw)
        with self.assertRaisesRegex(ValueError,'必须为横图'):centered_box(raw,self.line['canvas'])

    def test_legacy_handoff_recovers_only_hash_bound_originals(self):
        from PIL import Image
        from unittest.mock import patch
        raw=self.root/'original.png';bg=self.root/'background.png'
        Image.new('RGB',(1536,1024),'red').save(raw)
        Image.new('RGB',(1024,1536),'blue').save(bg)
        story=next(x for x in self.request['assets'] if x['asset_id']=='story')
        story['source_sha256']=file_hash(raw)
        self.line['album_background']={'sha256':file_hash(bg)};self.line['frame_fit']='legacy_precomposed';self.save_inputs()
        images={'K001':{'path':str(raw),'sha256':file_hash(raw)}}
        with patch('job_versions.image_assets',return_value=images),patch('album_visuals.current',return_value={'path':str(bg),'sha256':file_hash(bg)}):
            result=backend.build(self.job,self.project,{'font_file':str(self.font)})
            self.assertEqual(result['source_assets']['story']['sha256'],file_hash(raw))
            self.assertIn('portrait__album_background',result['source_assets'])
            story['source_sha256']='wrong';self.save_inputs()
            with self.assertRaisesRegex(ValueError,'原图与已核验交接不匹配'):
                backend.build(self.job,self.root/'other-project',{'font_file':str(self.font)})

    def test_layered_background_hash_must_match_handoff(self):
        self.layered_fixture()
        self.line['album_background']['sha256']='wrong';self.save_inputs()
        with self.assertRaisesRegex(ValueError,'底图素材缺失或哈希不匹配'):
            backend.build(self.job,self.project,{'font_file':str(self.font)})


if __name__=='__main__':unittest.main()
