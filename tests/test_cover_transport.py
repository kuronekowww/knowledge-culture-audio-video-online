import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from artifacts import read_json
import generate_images as images
import image_finish
import image_provenance
import requests


class TransportTests(unittest.TestCase):
    def test_documented_wire_size_and_complete_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=Path(tmp)
            cover={'id':'cover_portrait','filename':'cover.png','size':'1024x1536',
                   'cover_fit':'full_width_9x16','prompt':'测试标题','revision':1}
            # Keep the original side labels and all pixels across the centre of the native composition.
            original=Image.new('RGB',(1024,1536),'red')
            original.paste('green',(80,0,944,768))
            original.paste('blue',(80,768,944,1536))
            image_bytes=io.BytesIO()
            original.save(image_bytes,format='PNG')
            with patch('generate_images.requests.post') as post, patch('generate_images.requests.get') as get:
                post.return_value.status_code=200
                post.return_value.json.return_value={'data':[{'url':'https://example.test/cover.png'}]}
                post.return_value.headers={}
                get.return_value.status_code=200
                get.return_value.content=image_bytes.getvalue()
                result=images.generate_one(images.DEFAULT_API_BASE,'fixture',cover,job,'low',240,60)
                self.assertEqual(result['status'],'success',result)
                self.assertEqual(post.call_args.kwargs['json'],{'prompt':'测试标题\n'+image_finish.RULE,'size':'1024x1536','quality':'low','n':1,'include_b64':False})
                self.assertEqual(post.call_args.kwargs['timeout'],(10,240))
                self.assertEqual(images.generate_one(images.DEFAULT_API_BASE,'fixture',cover,job,'low',240,60)['status'],'skipped')
                self.assertEqual(post.call_count,1)
                self.assertEqual(get.call_count,1)
            with Image.open(job/'cover.png') as im:
                self.assertEqual(im.size,(1080,1920))
                self.assertEqual(im.getpixel((0,0)),(255,0,0))
                self.assertEqual(im.getpixel((540,150)),(0,128,0))
                self.assertEqual(im.getpixel((1079,1919)),(255,0,0))
                self.assertEqual(im.getpixel((540,1769)),(0,0,255))
            evidence=read_json(job/'.receipts/cover_portrait.json')['provenance']
            self.assertEqual(evidence['prompt'],post.call_args.kwargs['json']['prompt'])
            self.assertEqual(evidence['content_rect_xyxy'],[0,150,1080,1770])
            self.assertEqual(Path(evidence['source_path']).read_bytes(),image_bytes.getvalue())

    def test_old_canvas_cannot_be_submitted_or_counted_as_uncertain(self):
        cover={'id':'cover_portrait','filename':'cover.png','size':'1080x1920','prompt':'历史'}
        with tempfile.TemporaryDirectory() as tmp, patch('generate_images.requests.post') as post:
            job=Path(tmp)
            result=images.generate_one(images.DEFAULT_API_BASE,'fixture',cover,job,'low',240,60)
            self.assertEqual(result['attempts'],0)
            self.assertEqual(result['error_details']['billing_state'],'not_submitted')
            self.assertEqual(result['status'],'failed')
            self.assertIn('不能提交',result['error'])
            self.assertFalse(list(job.rglob('request.json')))
            post.assert_not_called()

    def test_timeout_categories(self):
        self.assertEqual(image_provenance.error_details(requests.ReadTimeout())['timeout_source'],'client_read')
        self.assertEqual(image_provenance.error_details(images.GenerationError('gateway',504))['timeout_source'],'upstream_gateway')
