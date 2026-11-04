import copy
from types import SimpleNamespace
import unittest

from core import Base
from trace_plot import trace_samples


class TracePlotTests(unittest.TestCase):
    def trace(self):
        return SimpleNamespace(peaks=[5,15],channels={b:list(range(21)) for b in 'ACGT'})

    def test_alignment_gap_keeps_every_original_signal_sample(self):
        trace=self.trace();original=copy.deepcopy(trace)
        mapping=[Base('A',0,40),None,None,Base('T',1,40)]
        points=trace_samples(trace,mapping,0,4,20)
        self.assertEqual([sample for x,sample in points],list(range(21)))
        by_sample={sample:x for x,sample in points}
        self.assertEqual(by_sample[5],82);self.assertEqual(by_sample[15],142)
        self.assertEqual(by_sample[10],112);self.assertEqual(trace,original)
        self.assertTrue(all(a[0]<b[0] for a,b in zip(points,points[1:])))

    def test_reverse_gap_is_continuous_in_display_order(self):
        mapping=[Base('A',1,40),None,None,Base('T',0,40)]
        points=trace_samples(self.trace(),mapping,0,4,20,reverse=True)
        self.assertEqual([sample for x,sample in points],list(range(20,-1,-1)))
        self.assertTrue(all(a[0]<b[0] for a,b in zip(points,points[1:])))

    def test_viewport_entirely_inside_alignment_gap_still_shows_signal(self):
        mapping=[Base('A',0,40),None,None,Base('T',1,40)]
        points=trace_samples(self.trace(),mapping,1,3,20)
        self.assertTrue(points);self.assertEqual([s for x,s in points],list(range(7,14)))

    def test_no_signal_fabricated_outside_read_coverage(self):
        mapping=[None]*10+[Base('A',0,40),Base('T',1,40)]+[None]*10
        self.assertEqual(trace_samples(self.trace(),mapping,0,5,20),[])
        self.assertEqual(trace_samples(self.trace(),mapping,15,20,20),[])

    def test_manual_insertion_adds_no_synthetic_raw_samples(self):
        mapping=[Base('A',0,40),Base('G',None,None,True),Base('T',1,40)]
        points=trace_samples(self.trace(),mapping,0,3,20)
        self.assertEqual([sample for x,sample in points],list(range(21)))


if __name__=='__main__':unittest.main()
