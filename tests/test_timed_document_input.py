import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from artifacts import atomic_json, read_json
import document_context, shared_album, subtitle_review


class TimedDocumentInputTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.job=Path(tmp.name)/'job'
        self.cfg={'album_id':'42','shared_root':str(Path(tmp.name)/'shared')}
        self.plan={'track_id':'34','clips':[{'start':100,'end':150}]}
        atomic_json(self.job/'cut_plan.json',self.plan)
        self.raw={'methodName':'queryAiDocByTrackId','body':[
            {'startTime':1000,'endTime':3000,'text':'第一句。'},
            {'startTime':3000,'endTime':4000,'text':'文稿版本：TAF'},
            {'startTime':35000,'endTime':37000,'text':'第二句。'}]}

    def test_raw_document_is_not_precut_and_preserves_semantic_override(self):
        atomic_json(self.job/'doc.json',self.raw)
        (self.job/'supplied.txt').write_text('用户参考文稿。')
        cfg={**self.cfg,'ai_doc_path':'doc.json','user_transcript_path':'supplied.txt'}
        data=document_context.caption_document(self.job,cfg,fetch=False)
        self.assertEqual([b['text'] for b in data['blocks']],['第一句。','第二句。'])
        self.assertEqual(shared_album.selected_context(self.job,cfg)['supplied_transcript'],'用户参考文稿。')
        self.assertEqual(shared_album.fetch(self.job,{**self.cfg,'ai_doc_path':'doc.json'}),'')
        self.assertIn('第一句', (self.job/'album_42_transcripts.csv').read_text())

    def test_wrong_sound_stops_before_remote_access(self):
        file=self.job/'doc.json'
        atomic_json(file,{**self.raw,'trackId':71})
        with self.assertRaisesRegex(ValueError,'声音 ID'):
            document_context.caption_document(self.job,{**self.cfg,'ai_doc_path':str(file)})

    def test_warm_sidecar_with_plain_text_and_no_network(self):
        home=shared_album.album_root(self.cfg)/'transcripts'
        atomic_json(home/'34.json',{'origin':'csv','text':'参考。'})
        cache=document_context.record('34','remote',document_context.normalize_document(self.raw))
        atomic_json(home/'timed/34.json',cache)
        with patch('mosn_album_track.get_track_document_result',side_effect=AssertionError('unexpected fetch')):
            self.assertEqual(document_context.caption_document(self.job,self.cfg)['blocks_hash'],cache['blocks_hash'])
        self.assertEqual(read_json(home/'34.json')['text'],'参考。')

    def test_srt_uses_exact_csv_boundaries(self):
        cues=[{'id':'C0001','text':'一句话','start_us':1622430,'end_us':4312340}]
        result=subtitle_review.srt_bytes(subtitle_review.csv_bytes(cues)).decode('utf-8-sig')
        self.assertEqual(result,'1\n00:00:01,622 --> 00:00:04,312\n一句话\n\n')
