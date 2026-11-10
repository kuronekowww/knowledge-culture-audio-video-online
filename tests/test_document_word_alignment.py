from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from document_word_alignment import align_phrases,clean


def timed(texts):
    return [{'text':s,'start_us':i*300000,'end_us':(i+1)*300000} for i,s in enumerate(texts)]


class PhraseAlignmentTests(unittest.TestCase):
    def test_ref_only_supplies_literal_punctuation_at_acoustic_word_edges(self):
        w = timed(['红','顶','商人，','走','进','房间。','王','有灵。'])
        ref = {'block_id':'D5','text':'红顶商人。走进房间！王有龄。','start_us':0,'end_us':2400000}
        res, stats = align_phrases(w,[ref])
        self.assertEqual(''.join(r['text'] for r in res),'红顶商人。走进房间！王有灵。')
        self.assertEqual(stats['matched_phrases'],2)
        self.assertEqual([(r['start_us'],r['end_us']) for r in res],[(r['start_us'],r['end_us']) for r in w])
        self.assertEqual(w[2]['text'],'商人，')

    def test_repeated_phrase_or_wrong_clock_does_not_replace_words(self):
        w=timed(['请坐吧。','请坐吧。','先开门。','明日再来。'])
        ref={'block_id':'D1','text':'请坐吧！先开门！','start_us':5000000,'end_us':8000000}
        res,stats=align_phrases(w,[ref])
        self.assertEqual(res,w)
        self.assertEqual(stats['matched_words'],0)

    def test_no_split_inside_word_tokens(self):
        w=timed(['走进来','了吗？'])
        ref={'block_id':'D1','text':'进来了吗！','start_us':0,'end_us':600000}
        self.assertEqual(align_phrases(w,[ref])[1]['matched_phrases'],0)
