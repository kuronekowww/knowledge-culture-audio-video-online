"""A document clock may be reused only where speech from the used audio supports it."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from timed_caption_route import plan, prefer_full_pass, probe_windows


def source_blocks(count=120):
    return [{'id':f'D{i:03d}', 'start_ms':i*2_000,'end_ms':(i+1)*2_000,
             'text':f'{i:03d}号讲述江南故事。'} for i in range(count)]


def evidence(rows, clips, offset=lambda s: 0):
    windows=probe_windows(rows,clips)
    words=[]
    output=0
    for c in clips:
        speed=c.get('speed',1)
        for b in rows:
            a=b['start_ms']/1e3+offset(b['start_ms']/1e3)
            t=output+(a-c['start'])/speed
            if not c['start']<=a<c['end']: continue
            a_us=round(t*1e6)
            if any(lo<=a_us and a_us+200_000<=hi for lo,hi in windows):
                words.append({'text':b['text'],'start_us':a_us,'end_us':a_us+200_000,'timing_source':'word'})
        output+=(c['end']-c['start'])/speed
    return {'windows':windows,'words':words,'source':'bound_word_cache'}


class DocumentClockTests(unittest.TestCase):
    def test_full_pass_when_long_blocks_dominate_even_after_speed(self):
        rows=[{'startTime':i*10000,'endTime':(i+1)*10000,'text':'长段需要定位。'} for i in range(40)]
        self.assertTrue(prefer_full_pass(rows,[{'start':0,'end':400,'speed':1.2}]))
        self.assertTrue(prefer_full_pass(rows,[{'start':11,'end':132},{'start':207,'end':330}]))

    def test_full_pass_preference_uses_output_duration_not_source_duration(self):
        rows=[{'startTime':i*5000,'endTime':(i+1)*5000,'text':'识别比较。'} for i in range(60)]
        self.assertTrue(prefer_full_pass(rows,[{'start':0,'end':300,'speed':1}]))
        self.assertFalse(prefer_full_pass(rows,[{'start':0,'end':300,'speed':1.4}]))
        self.assertFalse(prefer_full_pass(source_blocks(),[{'start':0,'end':240}]))

    def test_sparse_text_missing_clocks_and_crosscut_do_not_predict_nearly_full_alignment(self):
        self.assertFalse(prefer_full_pass([], [{'start':0,'end':500}]))
        self.assertFalse(prefer_full_pass([{'text':'无时码'}], [{'start':0,'end':500}]))
        two=[{'startTime':0,'endTime':60000,'text':'长段。'}, {'startTime':440000,'endTime':500000,'text':'另一段。'}]
        self.assertFalse(prefer_full_pass(two,[{'start':0,'end':500}]))
        self.assertFalse(prefer_full_pass(two[:1],[{'start':10,'end':11}]))

    def test_calibrates_positive_offset_and_keeps_uncovered_intro(self):
        rows=source_blocks()
        clip=[{'start':0,'end':250}]
        p=plan(rows,clip,evidence(rows,clip,lambda t:10))
        self.assertEqual(p['route'],'hybrid')
        self.assertEqual(p['clips'][0]['source_offset_seconds'],10)
        self.assertEqual(p['direct'][0]['start_us'],10_000_000)
        self.assertEqual(p['align'][0]['start_us'],0)
        self.assertEqual(p['align'][0]['end_us'],10_000_000)
        self.assertEqual(p['align'][0]['reasons'],['document_gap'])
        self.assertEqual(len(p['direct']),120)

    def test_document_only_keeps_native_sentence_rows(self):
        rows=source_blocks()
        clip=[{'start':0,'end':240}]
        p=plan(rows,clip,evidence(rows,clip))
        self.assertEqual(p['route'],'document')
        self.assertEqual(len(p['direct']),len(rows))
        self.assertEqual(p['direct'][0]['text'],rows[0]['text'])
        self.assertNotIn('words',p['direct'][0])
        self.assertEqual(p['align'],[])

    def test_speed_multicut_and_do_not_clip_or_interpolate_a_sentence(self):
        rows=source_blocks()
        clips=[{'start':21,'end':105,'speed':1.2},{'start':138,'end':222,'speed':1.2}]
        p=plan(rows,clips,evidence(rows,clips),word_ranges=[(20,23)])
        cue=next(r for r in p['direct'] if r['block_id']=='D069')
        self.assertEqual(cue['clip_index'],2)
        self.assertEqual(cue['start_us'],70_000_000)
        self.assertEqual(cue['end_us'],71_666_667)
        self.assertEqual(p['align'][0]['start_us'],0)
        self.assertTrue(p['align'][0]['references'][0]['partial'])
        self.assertTrue(any('word_timing_requested' in r['reasons'] for r in p['align']))
        self.assertTrue(all(d['end_us']<=r['start_us'] or r['end_us']<=d['start_us']
                            for r in p['align'] for d in p['direct']))

    def test_jump_preserves_only_verified_clock_and_no_reference_guessed_for_remainder(self):
        rows=source_blocks()
        clip=[{'start':0,'end':252}]
        p=plan(rows,clip,evidence(rows,clip,lambda t: 0 if t<160 else 12))
        self.assertEqual(p['route'],'hybrid')
        zones=p['clips'][0]['verified_ranges']
        self.assertEqual(len(zones),1)
        self.assertLess(zones[0]['end'],160)
        tail=p['align'][-1]
        self.assertIn('unverified_clock',tail['reasons'])
        self.assertEqual(tail['end'],252)
        self.assertTrue(all(r['end']<=zones[0]['end'] for r in p['direct']))

    def test_findings_from_duration_only_or_ambiguous_repeated_words_never_release(self):
        rows=source_blocks()
        clips=[{'start':0,'end':240}]
        self.assertEqual(plan(rows,clips,{})['route'],'asr')
        for row in rows:row['text']='今天去参观学习。'
        self.assertEqual(plan(rows,clips,evidence(rows,clips))['route'],'asr')
        anchors=evidence(source_blocks(),clips)
        for w in anchors['words']:w['timing_source']='segment'
        self.assertEqual(plan(source_blocks(),clips,anchors)['route'],'asr')

    def test_long_block_supplies_text_to_align_without_making_word_timestamps(self):
        rows=source_blocks()
        rows[70].update(end_ms=148_000,text='这段文字持续八秒，包含两句需要定位的原话。')
        del rows[71:74]
        clip=[{'start':0,'end':240}]
        p=plan(rows,clip,evidence(rows,clip))
        r=next(r for r in p['align'] if 'D070' in r['block_ids'])
        self.assertEqual(r['reference_text'],[rows[70]['text']])
        self.assertNotIn('words',r['references'][0])
        self.assertEqual((r['start'],r['end']),(140.,148.))

    def test_only_tiny_overlap_is_trimmed_automatically(self):
        rows=source_blocks()
        rows[6]['end_ms']+=120
        rows[8]['end_ms']+=550
        clip=[{'start':0,'end':240}]
        p=plan(rows,clip,evidence(rows,clip))
        self.assertEqual(next(r for r in p['direct'] if r['block_id']=='D006')['end'],14)
        bad=next(r for r in p['align'] if 'D008' in r['block_ids'])
        self.assertIn('D009',bad['block_ids'])
        self.assertIn('substantial_overlap',bad['reasons'])
        self.assertTrue(all(r['block_id'] not in ('D008','D009') for r in p['direct']))

    def test_metadata_omitted_and_original_rpc_fields_supported(self):
        rows=source_blocks()
        clips=[{'start':0,'end':240}]
        raw=[{'startTime':r['start_ms'],'endTime':r['end_ms'],'text':r['text']} for r in rows]
        raw.append({'text':'文稿版本：TAF测试'})
        p=plan(raw,clips,evidence(rows,clips))
        self.assertEqual(len(p['direct']),len(rows))
        self.assertEqual(p['route'],'document')


if __name__=='__main__':
    unittest.main()
