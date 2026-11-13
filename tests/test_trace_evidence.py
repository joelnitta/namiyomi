import unittest

from core import Base, Project
from review_fixtures import SPACING, add_peak, forward_source, make_contig, make_trace, reverse_source
from trace_evidence import REMOVE, Reviewer, clear_base, peak_evidence

SEQUENCE = 'ACGTACGTACGTACGTACGT'


class PeakEvidenceTests(unittest.TestCase):
    def test_isolated_peak_is_clear_and_a_co_located_rival_is_not(self):
        project = Project()
        trace = make_trace(project, 'r1', SEQUENCE)
        evidence = peak_evidence(trace, 9)
        self.assertTrue(evidence.clear)
        self.assertEqual((evidence.base, evidence.offset, evidence.rival_peak), ('C', 0.0, 0.0))
        add_peak(trace, 'T', trace.peaks[9], 0.8)        # a second summit under the same call
        mixed = peak_evidence(trace, 9)
        self.assertFalse(mixed.clear)
        self.assertAlmostEqual(mixed.rival_peak, 0.8)

    def test_a_flank_of_the_neighbouring_peak_is_not_a_rival(self):
        project = Project()
        trace = make_trace(project, 'r1', SEQUENCE)
        scan = trace.peaks[9]
        for offset in range(-2, 9):                      # the next G peak leans into this call
            trace.channels['G'][scan + offset] = max(trace.channels['G'][scan + offset], 400 + 60 * offset)
        evidence = peak_evidence(trace, 9)
        self.assertGreater(evidence.rival_at_summit, 0.3)
        self.assertEqual(evidence.rival_peak, 0.0)
        self.assertTrue(evidence.clear)

    def test_degraded_stretch_needs_stricter_spacing(self):
        project = Project()
        peaks = [30 + SPACING * i for i in range(len(SEQUENCE))]
        peaks[9] -= 4                                    # 0.67 of a spacing from the previous call
        good = make_trace(project, 'good', SEQUENCE, peaks=peaks)
        self.assertTrue(peak_evidence(good, 9).clear)
        poor = make_trace(project, 'poor', SEQUENCE, [15] * len(SEQUENCE), peaks=peaks)
        self.assertFalse(peak_evidence(poor, 9).clear)

    def test_n_call_takes_the_single_clear_channel(self):
        project = Project()
        trace = make_trace(project, 'r1', SEQUENCE[:9] + 'N' + SEQUENCE[10:])
        self.assertIsNone(clear_base(trace, 9))
        add_peak(trace, 'C', trace.peaks[9])
        self.assertEqual(clear_base(trace, 9), 'C')
        add_peak(trace, 'A', trace.peaks[9], 0.9)
        self.assertIsNone(clear_base(trace, 9))


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()

    def single(self, qualities, **options):
        trace = make_trace(self.project, 'r1', SEQUENCE, qualities, **options)
        contig = make_contig(self.project, [forward_source(trace, 'r1', range(len(SEQUENCE)))])
        return trace, contig

    def test_low_phred_call_with_a_clear_peak_is_confirmed(self):
        qualities = [50] * len(SEQUENCE)
        qualities[9] = 12
        _, contig = self.single(qualities)
        self.assertEqual((contig.columns[9].call, contig.columns[9].reason), ('N', 'low_quality'))
        verdict = Reviewer(self.project, contig).verdict(9)
        self.assertEqual((verdict.call, verdict.rule), ('C', 'clear_peak'))

    def test_mixed_signal_stays_unresolved(self):
        qualities = [50] * len(SEQUENCE)
        qualities[9] = 12
        trace, contig = self.single(qualities)
        add_peak(trace, 'T', trace.peaks[9], 0.8)
        verdict = Reviewer(self.project, contig).verdict(9)
        self.assertFalse(verdict.resolved)
        self.assertEqual(verdict.rule, 'unclear_peak')

    def test_two_calls_on_one_peak_are_never_removed_from_a_single_read(self):
        # Held-out reads showed that the base caller's count is right in most
        # such cases, so a single read is not enough to remove a call.
        qualities = [50] * len(SEQUENCE)
        qualities[9] = qualities[10] = 12
        peaks = [30 + SPACING * i for i in range(len(SEQUENCE))]
        sequence = SEQUENCE[:10] + 'C' + SEQUENCE[11:]
        peaks[10] = peaks[9] + 4
        trace = make_trace(self.project, 'r1', sequence, qualities, peaks=peaks)
        contig = make_contig(self.project, [forward_source(trace, 'r1', range(len(sequence)))])
        for column in (9, 10):
            self.assertNotEqual(Reviewer(self.project, contig).verdict(column).call, REMOVE)

    def pair(self, second_sequence, second_qualities, layout, first_layout=None, reverse=False):
        first = make_trace(self.project, 'clean', SEQUENCE)
        second = make_trace(self.project, 'tail', second_sequence, second_qualities)
        source = (reverse_source if reverse else forward_source)(second, 'tail', layout)
        return make_contig(self.project, [
            forward_source(first, 'clean', first_layout or range(len(SEQUENCE))), source])

    def test_clean_read_outranks_a_degraded_read_at_a_mismatch(self):
        other = SEQUENCE[:9] + 'T' + SEQUENCE[10:]
        contig = self.pair(other, [22] * len(SEQUENCE), range(len(SEQUENCE)))
        self.assertEqual((contig.columns[9].call, contig.columns[9].reason), ('N', 'conflict'))
        verdict = Reviewer(self.project, contig).verdict(9)
        self.assertEqual((verdict.call, verdict.rule), ('C', 'anchored_read'))
        self.assertIn('Phred >= 40', verdict.detail)

    def test_base_missing_from_the_degraded_read_is_kept(self):
        other = SEQUENCE[:9] + SEQUENCE[10:]
        layout = list(range(9)) + [None] + list(range(9, len(other)))
        contig = self.pair(other, [22] * len(other), layout)
        self.assertEqual(contig.columns[9].reason, 'indel')
        self.assertEqual(Reviewer(self.project, contig).verdict(9).call, 'C')

    def test_extra_base_in_the_degraded_read_is_removed(self):
        other = SEQUENCE[:9] + 'G' + SEQUENCE[9:]
        first_layout = list(range(9)) + [None] + list(range(9, len(SEQUENCE)))
        contig = self.pair(other, [22] * len(other), range(len(other)), first_layout)
        self.assertEqual(contig.columns[9].reason, 'indel')
        verdict = Reviewer(self.project, contig).verdict(9)
        self.assertEqual((verdict.call, verdict.rule), (REMOVE, 'anchored_read'))

    def test_two_clean_reads_that_differ_stay_unresolved(self):
        other = SEQUENCE[:9] + 'T' + SEQUENCE[10:]
        contig = self.pair(other, [50] * len(SEQUENCE), range(len(SEQUENCE)))
        verdict = Reviewer(self.project, contig).verdict(9)
        self.assertEqual((verdict.resolved, verdict.rule), (False, 'anchored_reads_disagree'))

    def test_lower_tier_applies_only_when_no_read_reaches_the_higher_one(self):
        first = make_trace(self.project, 'clean', SEQUENCE, [33] * len(SEQUENCE))
        other = SEQUENCE[:9] + 'T' + SEQUENCE[10:]
        second = make_trace(self.project, 'tail', other, [21] * len(SEQUENCE))
        contig = make_contig(self.project, [forward_source(first, 'clean', range(len(SEQUENCE))),
                                            forward_source(second, 'tail', range(len(SEQUENCE)))])
        verdict = Reviewer(self.project, contig).verdict(9)
        self.assertEqual(verdict.call, 'C')
        self.assertIn('Phred >= 30', verdict.detail)

    def test_uneven_spacing_or_an_edit_breaks_the_clean_stretch(self):
        other = SEQUENCE[:9] + 'T' + SEQUENCE[10:]
        contig = self.pair(other, [22] * len(SEQUENCE), range(len(SEQUENCE)))
        clean = self.project.traces['clean']
        saved = list(clean.peaks)
        clean.peaks[8] -= 6
        self.assertNotEqual(Reviewer(self.project, contig).verdict(9).rule, 'anchored_read')
        clean.peaks[:] = saved
        contig.sources[0].mapping[8] = Base('G', 8, None, True)
        verdict = Reviewer(self.project, contig).verdict(9)
        self.assertNotEqual(verdict.rule, 'anchored_read')

    def test_user_edit_in_the_column_is_never_overruled(self):
        other = SEQUENCE[:9] + 'T' + SEQUENCE[10:]
        contig = self.pair(other, [22] * len(SEQUENCE), range(len(SEQUENCE)))
        contig.sources[1].mapping[9] = Base('T', 9, None, True)
        verdict = Reviewer(self.project, contig).verdict(9)
        self.assertEqual((verdict.resolved, verdict.rule), (False, 'manual_edit'))

    def test_reverse_read_is_judged_in_consensus_orientation(self):
        from Bio.Seq import Seq
        other = SEQUENCE[:9] + 'T' + SEQUENCE[10:]
        raw = str(Seq(other).reverse_complement())
        layout = [len(raw) - 1 - column for column in range(len(raw))]
        contig = self.pair(raw, [22] * len(raw), layout, reverse=True)
        self.assertEqual(contig.sources[1].mapping[9].base, 'T')
        self.assertEqual(Reviewer(self.project, contig).verdict(9).call, 'C')

    def test_read_that_ends_before_the_column_is_not_evidence(self):
        qualities = [50] * len(SEQUENCE)
        qualities[15] = 12
        first = make_trace(self.project, 'clean', SEQUENCE, qualities)
        second = make_trace(self.project, 'short', SEQUENCE[:8])
        layout = list(range(8)) + [None] * (len(SEQUENCE) - 8)
        contig = make_contig(self.project, [forward_source(first, 'clean', range(len(SEQUENCE))),
                                            forward_source(second, 'short', layout)])
        verdict = Reviewer(self.project, contig).verdict(15)
        self.assertEqual((verdict.call, verdict.rule), ('T', 'clear_peak'))


if __name__ == '__main__':
    unittest.main()
