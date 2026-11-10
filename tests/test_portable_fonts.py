import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from portable_prepare import prepare
from portable_validate import validate
from portable_fonts import FontBindingError
import team_fonts


class PortableFontsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / '解压后的草稿'
        font_dir = self.src / 'assets' / 'fonts'
        font_dir.mkdir(parents=True)
        self.font = font_dir / 'display.otf'
        self.font.write_bytes(b'fixture-draft-font')
        sha = hashlib.sha256(self.font.read_bytes()).hexdigest()
        self.old_font = r'C:\Users\producer\draft\assets\fonts\display.otf'
        self.layout = {'layout_profile': {'id': 'fixture'}, 'fonts': {sha: self.old_font}}
        self.write('knowledge-layout.json', self.layout)
        image = self.src / 'assets' / 'images' / '北京.jpg'
        image.parent.mkdir()
        image.write_bytes(b'test-image')
        content = {'text':'标题', 'styles':[{'size':13,'font':{'path': self.old_font}}] * 2}
        self.data = {
            'canvas_config':{'width':1080,'height':1920},
            'materials':{'videos':[{'path':r'C:\资料\draft\assets\images\北京.jpg'}],
                         'texts':[{'id':'text','font_path':self.old_font,'font_size':13,
                                   'content':json.dumps(content)}]},
            'tracks':[{'type':'text','segments':[{'material_id':'text','clip':{'scale':{'x':1,'y':1},'transform':{'x':0,'y':0.6}}}]}],
        }
        for filename in ('draft_content.json','draft_info.json'):
            self.write(filename, self.data)

    def write(self, name, data):
        (self.src/name).write_text(json.dumps(data),encoding='utf-8')

    def test_relocates_both_files_each_run_and_windows_media(self):
        result = prepare(self.src, self.root, '接收机')
        target = Path(result['target_dir'])
        self.assertTrue(validate(target)['ok'])
        for filename in ('draft_content.json','draft_info.json'):
            moved = json.loads((target/filename).read_text(encoding='utf-8'))
            mat = moved['materials']['texts'][0]
            self.assertEqual(moved['tracks'], self.data['tracks'])
            self.assertEqual(mat['font_size'], 13)
            self.assertEqual(Path(mat['font_path']), target/'assets/fonts/display.otf')
            self.assertEqual(Path(moved['materials']['videos'][0]['path']), target/'assets/images/北京.jpg')
            styles = json.loads(mat['content'])['styles']
            self.assertEqual([s['font']['path'] for s in styles], [mat['font_path']] * 2)
        styles[1]['font']['path'] = self.old_font
        mat['content'] = json.dumps({'text':'标题', 'styles':styles})
        for filename in ('draft_content.json','draft_info.json'):
            (target/filename).write_text(json.dumps(moved),encoding='utf-8')
        self.assertFalse(validate(target)['ok'])

    def test_private_fonts_are_resolved_on_receiver_and_not_copied(self):
        installed = self.root/'本机字体.ttf'
        installed.write_bytes(self.font.read_bytes())
        roles = {next(iter(self.layout['fonts'])): 'display'}
        with patch('portable_prepare.private_roles', return_value=roles), \
             patch('portable_fonts.private_roles', return_value=roles), \
             patch('team_fonts.resolve_all', return_value={'display':installed}):
            result = prepare(self.src, self.root, '本机原字体')
            target = Path(result['target_dir'])
            self.assertTrue(validate(target)['ok'])
            mat = json.loads((target/'draft_content.json').read_text())['materials']['texts'][0]
            self.assertEqual(mat['font_path'], str(installed))
            self.assertFalse((target/'assets/fonts/display.otf').exists())

    def test_missing_team_fonts_report_both_at_once(self):
        with patch.dict('os.environ', {
            'KNOWLEDGE_DISPLAY_FONT': str(self.root/'bad display.ttf'),
            'KNOWLEDGE_SUBTITLE_FONT': str(self.root/'bad subtitle.ttc'),
            'KNOWLEDGE_FONT_SETTINGS': str(self.root/'settings.json'),
        }), self.assertRaisesRegex(ValueError, '江西拙楷 Regular.*苹方简 Medium'):
            team_fonts.resolve_all()

    def test_unknown_font_does_not_substitute_or_publish_incomplete_draft(self):
        self.font.write_bytes(b'other font with same name')
        with self.assertRaisesRegex(FontBindingError, '字体版本'):
            prepare(self.src,self.root,'新副本')
        self.assertFalse((self.root/'新副本').exists())
        self.assertTrue((self.src/'draft_content.json').exists())

    def test_project_with_missing_active_material_font_is_rejected(self):
        del self.data['materials']['texts'][0]['font_path']
        for name in ('draft_content.json','draft_info.json'):
            self.write(name, self.data)
        with self.assertRaisesRegex(FontBindingError, '缺少材料字体'):
            prepare(self.src,self.root,'新副本')
