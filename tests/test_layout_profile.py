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

    def test_reference_mac_glyph_metrics(self):
        self.assertEqual(layout.load()['font_files']['subtitle']['ttc_face_index'], 20)
        self.assertAlmostEqual(layout.measure('有声小说武则天从3岁到82岁', 'subtitle'), 721.44)
        self.assertEqual(layout.fit('武则天：从三岁到八十二岁', 'album')[0], '武则天：从三岁到八十二岁')

    def test_long_story_title_rejected_without_reducing_font(self):
        with self.assertRaisesRegex(ValueError,'改写文案'):
            layout.fit('武则天的父亲为什么会请袁天罡到家中来看相','title_line1')

    def test_approved_title_fits_including_stroke(self):
        text='武则天的父亲请袁天罡看相';fitted,m=layout.fit(text,'title_line1')
        self.assertEqual(fitted,text);self.assertLessEqual(m['paint_width_px'],900)
        self.assertEqual(layout.resolved_style('title_line1',text)[1]['pixel_style']['font_size_px'],78)

    def test_native_sizes_do_not_get_cancelled_by_enlarged_scale(self):
        from copy import deepcopy
        p=layout.load()
        for role,size in {'title_line1':13,'title_line2':13,'subtitle':10,'album':10}.items():
            with self.subTest(role=role):
                native=layout.resolved_style(role,'测试文字',p=p)[1]
                legacy=deepcopy(p)
                legacy['texts'][role].pop('jianying_font_size')
                previous=layout.resolved_style(role,'测试文字',p=legacy)[1]
                self.assertEqual(native['font_size'],size)
                for key in ('scale_x','scale_y','x','y','font_sha256'):
                    self.assertEqual(native[key],previous[key])

    def test_final_saved_draft_has_native_size_in_both_text_fields(self):
        import jianying_profile
        p=layout.load();requests=[];materials=[];tracks=[]
        roles={'title_line1':13,'title_line2':13,'subtitle':10,'album':10}
        texts={'title_line1':'第一行标题','title_line2':'第二行标题','subtitle':'测试字幕','album':'专辑名称'}
        for role in roles:
            text,style=layout.resolved_style(role,texts[role])
            cue={'text':text,'start':1000000,'end':3000000}
            requests.append({'body':{**style,'captions':json.dumps([cue]),
                'transform_x':style['x'],'transform_y':style['y']},'verification':{}})
            materials.append({'id':role,'font_size':99,'content':json.dumps({
                'text':text,'styles':[{'size':99}]})})
            tracks.append({'type':'text','segments':[{'material_id':role,
                'target_timerange':{'start':1000000,'duration':2000000},'clip':{}}]})
        line={'layout_profile':layout.binding(),'canvas':p['canvas'],
            'caption_api':{'requests':requests},'frames':[],
            'album_background':{'asset_id':'background','start_us':0,'end_us':3000000}}
        data={'materials':{'texts':materials,'videos':[]},'tracks':tracks}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('draft_content.json','draft_info.json'):
                (root/name).write_text(json.dumps(data))
            jianying_profile.finalize(root,line,{'background':{'sha256':'fixture'}})
            for name in ('draft_content.json','draft_info.json'):
                saved=read_json(root/name)
                for mat in saved['materials']['texts']:
                    expected=roles[mat['id']]
                    self.assertEqual(mat['font_size'],expected)
                    self.assertTrue(all(s['size']==expected for s in json.loads(mat['content'])['styles']))
                    self.assertAlmostEqual(mat['line_max_width']*p['canvas']['width'],950 if mat['id']=='subtitle' else 900)
                    seg=next(t['segments'][0] for t in saved['tracks'] if t['segments'][0]['material_id']==mat['id'])
                    self.assertEqual(seg['clip']['scale'],{'x':1.,'y':1.})
                    self.assertEqual(seg['uniform_scale']['value'],1.)
                    if mat['id']!='album':self.assertTrue(mat['force_apply_line_max_width'])

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
            record={'path':str(bg),'sha256':file_hash(bg),'revision':1,'mode':'episode_solid','color':'#222222','reference_mode':'user_image','reference_path':str(cover),'reference_sha256':file_hash(cover)}
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
                    disc=next(r for r in line['branding_elements'] if r['role']=='playback')
                    self.assertEqual(disc['kind'],'image')
                    self.assertEqual(disc['box_px'],[177,1339,347,1509])
                    import assembly_api
                    published={k:{**v,'url':'https://fixture.invalid/'+k} for k,v in assets.items()}
                    sequence=assembly_api.compile_timeline(line,published)
                    self.assertNotIn('add_sticker',[step['endpoint'] for step in sequence['steps']])
                    with Image.open(assets[disc['asset_id']]['path']) as disk:
                        self.assertEqual((disk.mode,disk.n_frames),('RGBA',1))
                    source=next(r for r in line['branding_elements'] if r['role']=='source_label')
                    self.assertEqual(source['start_us'],1000000)
                    self.assertEqual(source['end_us'],line['body_duration_us'])
                self.assertEqual(lines[0],lines[1])


if __name__=='__main__':unittest.main()
