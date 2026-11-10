import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import generate_images as images
import solid_frame,team_visuals

class DefaultsTests(unittest.TestCase):
    def manifest(self,root):
        return images.validate_manifest({'output_dir':str(root/'out'),'quality':'low','images':[
            {'id':'cover_portrait','filename':'cover.png','size':'1024x1536','quality':'high','prompt':'approved cover'},
            {'id':'SH001','filename':'story.png','size':'1536x1024','prompt':'approved story'}]},root/'manifest.json')

    def test_cover_high_body_low_and_explicit_batch_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.manifest(Path(tmp));seen=[]
            def fake(api,token,image,output,quality,*args):
                seen.append((image['id'],quality,images.image_fingerprint(api,image,quality)))
                return {'id':image['id'],'status':'success'}
            with patch.object(images,'_prepare_one',side_effect=fake):
                images.run_batch(cfg,'not-a-real-token')
                self.assertEqual(dict((i,q) for i,q,_ in seen),{'cover_portrait':'high','SH001':'low'})
                cover_fp=next(fp for i,q,fp in seen if i=='cover_portrait');seen.clear()
                images.run_batch(cfg,'not-a-real-token',quality='medium')
                self.assertEqual({q for i,q,fp in seen},{'medium'})
                self.assertNotEqual(cover_fp,next(fp for i,q,fp in seen if i=='cover_portrait'))

    def test_existing_low_cover_is_not_implicitly_regenerated_high(self):
        rows=[{'id':'cover_portrait','quality':'high'}]
        team_visuals.preserve_overrides(rows,{'quality':'low','images':[{'id':'cover_portrait','revision':3}]})
        self.assertNotIn('quality',rows[0]);self.assertEqual(rows[0]['revision'],3)

    def test_explicit_quality_does_not_create_false_legacy_ambiguity(self):
        import job_versions
        from artifacts import atomic_json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw={'output_dir':str(root/'out'),'images':[
                {'id':'cover_portrait','filename':'cover.png','size':'1024x1536','quality':'high','prompt':'approved'}]}
            atomic_json(root/'image_manifest.json',raw)
            data=images.validate_manifest(raw,root/'image_manifest.json')
            fp=images.image_fingerprint(images.DEFAULT_API_BASE,data['images'][0],'high')
            atomic_json(root/'out/.receipts/cover_portrait.json',{'fingerprint':fp})
            with patch('shared_album.album_root',return_value=root/'shared'):
                job_versions.pin_existing_quality(root,{})
            import json
            self.assertEqual(json.loads((root/'image_manifest.json').read_text()),raw)

    def test_black_background_does_not_select_arbitrary_palette(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'scene.png';Image.new('RGB',(20,12),'yellow').save(p)
            with patch.object(solid_frame,'choose_color',side_effect=AssertionError('palette must not be consulted')):
                result=solid_frame.materialize(root,[{'id':'s','path':p}])
            self.assertEqual(result['color'],'#000000')
            with Image.open(result['path']) as im:self.assertEqual(im.getextrema(),((0,0),(0,0),(0,0)))

if __name__=='__main__':unittest.main()
