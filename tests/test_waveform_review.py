import unittest

from core import Base, Column, Contig, Project, Source, Trace
from review_fixtures import forward_source, make_contig, make_trace, reverse_source
from waveform_review import WaveformSite, needs_review, review_sites, waveform_evidence

SEQUENCE = 'ACGTACGTACGTACGTACGT'


class ReviewSiteTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        columns = [Column(None, None, 'A', 'single', 45) for _ in range(30)]
        self.contig = Contig('private-specimen', 'private-locus', None, None,
                             [], [], columns, '', 0, None, 'assembled')
        self.project.contigs['private-contig-id'] = self.contig
        self.site = WaveformSite('private-contig-id', 13)
        self.contig.columns[13] = Column(None, None, 'N', 'no_support', None)

    def test_sites_are_open_flags_and_skip_user_decisions(self):
        for i in (1, 2, 3, 4, 5):
            self.contig.columns[i] = Column(None, None, 'N', 'conflict', None)
        self.contig.overrides['2'] = 'N'
        self.contig.black_marks['3'] = False
        self.contig.ai_review['4'] = 'A'
        self.contig.ai_review['5'] = 'N'           # reviewed before and still open: review again
        self.assertEqual(review_sites(self.project),
                         [WaveformSite(self.site.contig, 1), WaveformSite(self.site.contig, 5), self.site])
        self.assertEqual(waveform_evidence(self.project, self.site), 'No source chromatograms cover this column.')
        self.contig.stale = True
        self.assertFalse(review_sites(self.project))

    def test_a_flagged_call_is_reviewed_and_a_settled_one_is_not(self):
        self.contig.columns[7] = Column(None, None, 'C', 'quality', 30)
        self.assertTrue(needs_review(self.contig, 7))
        self.contig.ai_review['7'] = 'C'
        self.assertFalse(needs_review(self.contig, 7))
        self.contig.ai_review['7'] = ''
        self.assertFalse(needs_review(self.contig, 7))
        self.assertFalse(needs_review(self.contig, 8))

    def test_double_peak_and_manual_source_flags_are_left_open(self):
        trace = make_trace(self.project, 'r1', SEQUENCE)
        source = forward_source(trace, 'r1', range(len(SEQUENCE)))
        contig = make_contig(self.project, [source])
        contig.columns[8] = Column(None, None, 'R', 'double_peak', None)
        source.mapping[9] = Base('T', 9, None, True)
        contig.columns[9] = Column(None, None, 'N', 'conflict', None)
        contig.columns[10] = Column(None, None, 'N', 'manual_read_conflict', None)
        self.assertEqual(contig.issues(), [8, 9, 10])
        self.assertEqual([contig.flag_color(i) for i in (8, 9, 10)], ['black'] * 3)
        self.assertEqual(review_sites(self.project), [self.site])


class EvidenceTextTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()

    def test_signal_is_sampled_around_the_call_with_scaled_heights(self):
        qualities = [50] * len(SEQUENCE)
        qualities[9] = 12
        trace = make_trace(self.project, 'private-read', SEQUENCE, qualities)
        make_contig(self.project, [forward_source(trace, 'private-read', range(len(SEQUENCE)))], 'private-key')
        text = waveform_evidence(self.project, WaveformSite('private-key', 9))
        lines = text.splitlines()
        self.assertEqual(lines[0], 'Read 1 (forward strand)')
        self.assertIn('aligned calls -6..+6: T50 A50 C50 G50 T50 A50 C12 G50 T50 A50 C50 G50 T50', lines[1])
        self.assertIn('called peak: summit 0.00 spacings from the call, height 100', lines[2])
        self.assertIn('spacing 12.0 scans per base', lines[3])
        self.assertIn('A50@-12 C12@+0<-target G50@+12', lines[3])
        offsets = lines[4].split()[1:]
        rows = {line.split()[0]: line.split()[1:] for line in lines[5:9]}
        self.assertEqual(set(rows), set('ACGT'))
        self.assertEqual((offsets[0], offsets[-1]), ('-36', '36'))
        centre = offsets.index('0')
        self.assertEqual(rows['C'][centre], '100')
        self.assertEqual(rows['A'][offsets.index('-12')], '100')
        self.assertEqual(rows['G'][offsets.index('12')], '100')
        self.assertEqual(rows['T'][centre], '0')
        self.assertNotIn('private', text)

    def test_reverse_read_is_flipped_and_complemented(self):
        qualities = [50] * len(SEQUENCE)
        qualities[9] = 12
        trace = make_trace(self.project, 'private-read', SEQUENCE, qualities)
        layout = [len(SEQUENCE) - 1 - column for column in range(len(SEQUENCE))]
        make_contig(self.project, [reverse_source(trace, 'private-read', layout)], 'private-key')
        column = layout.index(9)
        text = waveform_evidence(self.project, WaveformSite('private-key', column))
        lines = text.splitlines()
        self.assertEqual(lines[0], 'Read 1 (reverse strand, shown reverse-complemented)')
        # AB1 order ...A(8) C(9) G(10)... reads C(10) G(9) T(8) in the consensus.
        self.assertIn('C50@-12 G12@+0<-target T50@+12', lines[3])
        offsets = lines[4].split()[1:]
        rows = {line.split()[0]: line.split()[1:] for line in lines[5:9]}
        self.assertEqual(rows['G'][offsets.index('0')], '100')
        self.assertEqual(rows['C'][offsets.index('-12')], '100')
        self.assertEqual(rows['T'][offsets.index('12')], '100')

    def test_gap_is_centred_between_flanking_calls_and_uncovered_reads_are_left_out(self):
        long = make_trace(self.project, 'long', SEQUENCE)
        short_sequence = SEQUENCE[:9] + SEQUENCE[10:]
        short = make_trace(self.project, 'short', short_sequence, [22] * len(short_sequence))
        ended = make_trace(self.project, 'ended', SEQUENCE[:5])
        gap_layout = list(range(9)) + [None] + list(range(9, len(short_sequence)))
        make_contig(self.project, [
            forward_source(long, 'long', range(len(SEQUENCE))),
            forward_source(short, 'short', gap_layout),
            forward_source(ended, 'ended', list(range(5)) + [None] * (len(SEQUENCE) - 5))], 'key')
        text = waveform_evidence(self.project, WaveformSite('key', 9))
        self.assertIn('Read 1 (forward strand)', text)
        self.assertIn('Read 2 (forward strand)', text)
        self.assertNotIn('Read 3', text)
        second = text[text.index('Read 2'):]
        self.assertIn('aligned calls -6..+6: T22 A22 C22 G22 T22 A22 - G22 T22 A22 C22 G22 T22', second)
        self.assertIn('no call at this column: x=0 is midway between the two flanking calls', second)
        self.assertIn('A22@-6 G22@+6', second)
        self.assertNotIn('called peak', second)

    def test_user_edit_is_named_and_two_read_layout_matches_sources(self):
        qualities = [50] * len(SEQUENCE)
        trace = make_trace(self.project, 'r1', SEQUENCE, qualities)
        contig = make_contig(self.project, [forward_source(trace, 'r1', range(len(SEQUENCE)))], 'key')
        site = WaveformSite('key', 9)
        plain = waveform_evidence(self.project, site)
        source = contig.sources[0]
        contig.sources = []
        contig.f_id, contig.f_bases = source.read_id, source.mapping
        for index, column in enumerate(contig.columns):
            column.f = index
        self.assertEqual(waveform_evidence(self.project, site), plain)
        contig.sources = [source]
        source.mapping[9] = Base('T', 9, None, True)
        self.assertIn('the call in this column was edited by the user', waveform_evidence(self.project, site))

    def test_missing_trace_is_reported_not_guessed(self):
        trace = make_trace(self.project, 'r1', SEQUENCE)
        make_contig(self.project, [forward_source(trace, 'r1', range(len(SEQUENCE)))], 'key')
        del self.project.traces['r1']
        self.assertIn('signal unavailable', waveform_evidence(self.project, WaveformSite('key', 9)))
        with self.assertRaisesRegex(ValueError, 'outside the contig'):
            waveform_evidence(self.project, WaveformSite('key', 99))


if __name__ == '__main__':
    unittest.main()
