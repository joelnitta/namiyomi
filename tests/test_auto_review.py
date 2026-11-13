import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_review import ReviewOutcome, local_review, model_review, signal_supports
from batch import review_project, run_batch
from core import Project
from llm_review import OFFLINE, ReviewError, ReviewSettings
from review_fixtures import add_peak, forward_source, make_contig, make_trace
from scripts.create_demo import create_demo
from waveform_review import WaveformSite, review_sites

ROOT = Path(__file__).resolve().parents[1]
SEQUENCE = 'ACGTACGTACGTACGTACGT'


def length_difference(project):
    """A clean read against a degraded read that carries one extra call in column 9."""
    clean = make_trace(project, 'clean', SEQUENCE)
    other = SEQUENCE[:9] + 'G' + SEQUENCE[9:]
    tail = make_trace(project, 'tail', other, [22] * len(other))
    layout = list(range(9)) + [None] + list(range(9, len(SEQUENCE)))
    return make_contig(project, [forward_source(clean, 'clean', layout),
                                 forward_source(tail, 'tail', range(len(other)))])


class LocalReviewTests(unittest.TestCase):
    def test_resolved_repeat_peaks_are_applied_with_a_local_note(self):
        project = Project()
        long_sequence = SEQUENCE[:9] + 'CCC' + SEQUENCE[10:]
        short_sequence = SEQUENCE[:9] + 'CC' + SEQUENCE[10:]
        long = make_trace(project, 'long', long_sequence)
        short = make_trace(project, 'short', short_sequence)
        first, second = short.peaks[9:11]
        short.channels['C'][first + 1:second] = [950] * (second - first - 1)
        layout = list(range(9)) + [None] + list(range(9, len(short_sequence)))
        contig = make_contig(project, [forward_source(long, 'long', range(len(long_sequence))),
                                      forward_source(short, 'short', layout)])
        outcome, remaining = local_review(project)
        self.assertEqual((outcome.local, remaining), ({'resolved_run': 1}, []))
        self.assertEqual((contig.call(9), contig.ai_notes['9'], contig.flag_color(9)),
                         ('C', 'local rule: resolved repeat peaks', 'black'))

    def test_extra_base_is_removed_from_the_sequence_and_keeps_its_history_flag(self):
        project = Project()
        contig = length_difference(project)
        self.assertEqual(contig.sequence(), SEQUENCE[:9] + 'N' + SEQUENCE[9:])
        outcome, remaining = local_review(project)
        self.assertEqual((outcome.sites, outcome.local_resolved, outcome.removed, remaining), (1, 1, 1, []))
        self.assertEqual(contig.sequence(), SEQUENCE)
        self.assertEqual((contig.call(9), contig.ai_review['9'], contig.ai_notes['9']),
                         ('', '', 'local rule: clean read'))
        self.assertTrue(contig.ai_decided(9))
        self.assertEqual(contig.issues(), [])
        self.assertEqual(contig.flag_color(9), 'black')
        self.assertEqual(review_sites(project), [])

    def test_unresolved_review_never_erases_a_call_the_consensus_made(self):
        project = Project()
        first = make_trace(project, 'first', SEQUENCE, [35] * len(SEQUENCE))
        other = SEQUENCE[:9] + 'T' + SEQUENCE[10:]
        second = make_trace(project, 'second', other, [15] * len(SEQUENCE))
        add_peak(first, 'T', first.peaks[9], 0.9)          # both reads are doubtful here
        add_peak(second, 'A', second.peaks[9], 0.9)
        first.qualities[8] = 20
        contig = make_contig(project, [forward_source(first, 'first', range(len(SEQUENCE))),
                                       forward_source(second, 'second', range(len(SEQUENCE)))])
        self.assertEqual((contig.columns[9].call, contig.columns[9].reason), ('C', 'quality'))
        outcome, remaining = local_review(project)
        self.assertEqual(remaining, [WaveformSite('contig', 9)])
        reply = '{"decisions":[{"id":"P00001","call":"N","confidence":"low"}]}'
        with patch('llm_review._generate', return_value=reply):
            model_review(project, remaining, ReviewSettings(key='test-credential'), outcome)
        self.assertEqual((contig.ai_review['9'], contig.call(9)), ('N', 'C'))
        self.assertTrue(contig.ai_unresolved(9))
        self.assertEqual(contig.flag_color(9), 'red')
        self.assertEqual(contig.issues(), [9])
        self.assertEqual(review_sites(project), remaining)   # still open for a later online review

    def test_decisions_survive_save_reopen_and_reverse(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / 'Demo.json'
            create_demo(original.parent)
            project = Project.load(original)
            key, contig = next(iter(project.contigs.items()))
            width = len(contig.columns)
            contig.ai_review.update({'5': '', '6': 'A', '7': 'N'})
            contig.ai_notes.update({'5': 'local rule: clean read', '6': 'gpt-6-luna: high confidence',
                                    '7': 'local rules: not settled'})
            before = contig.sequence()
            saved = original.with_name('Reviewed.json')
            project.export_project(saved)
            reopened = Project.load(saved)
            again = reopened.contigs[key]
            self.assertEqual((again.sequence(), again.ai_review, again.ai_notes),
                             (before, contig.ai_review, contig.ai_notes))
            self.assertFalse(again.ai_unresolved(7))
            reopened.reverse_contig(key)
            self.assertEqual(again.ai_review[str(width - 6)], '')
            self.assertEqual(again.ai_review[str(width - 7)], 'T')
            self.assertEqual(again.ai_notes[str(width - 6)], 'local rule: clean read')
            reopened.reverse_contig(key)
            self.assertEqual(again.sequence(), before)

            raw = json.loads(saved.read_text())
            for field, value in (('ai_review', {'5': 'R'}), ('ai_review', {'99999': 'A'}),
                                 ('ai_notes', {'8': 'no decision for this column'}),
                                 ('ai_notes', {'5': 'line\nbreak'})):
                broken = json.loads(json.dumps(raw))
                broken['contigs'][key][field] = value
                bad = original.with_name('Broken.json')
                bad.write_text(json.dumps(broken))
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'review'):
                    Project.load(bad)

    def test_editing_a_source_base_discards_the_review_of_that_column(self):
        project = Project()
        contig = length_difference(project)
        local_review(project)
        self.assertIn('9', contig.ai_review)
        project.edit_read_bases('tail', [9], 'A')
        self.assertNotIn('9', contig.ai_review)
        self.assertNotIn('9', contig.ai_notes)


class ModelReviewTests(unittest.TestCase):
    def test_signal_check_for_a_model_answer(self):
        project = Project()
        length_difference(project)
        site = WaveformSite('contig', 9)
        self.assertTrue(signal_supports(project, site, ''))       # a read already has a gap here
        self.assertTrue(signal_supports(project, site, 'G'))      # the other read has a G summit
        self.assertFalse(signal_supports(project, site, 'T'))     # nothing in the T channel
        single = Project()
        trace = make_trace(single, 'r1', SEQUENCE)
        make_contig(single, [forward_source(trace, 'r1', range(len(SEQUENCE)))])
        self.assertFalse(signal_supports(single, site, ''))
        self.assertTrue(signal_supports(single, site, 'C'))

    def test_single_read_answer_is_applied_only_as_an_early_confirmation(self):
        from auto_review import SINGLE_READ_LIMIT
        long = SEQUENCE * 17
        qualities = [50] * len(long)
        early, late = 9, SINGLE_READ_LIMIT + 9
        for index in (early, late):
            qualities[index] = 12
        isolated = late + 20
        qualities[isolated] = 12
        qualities[isolated - 5:isolated] = [21] * 5              # a degraded stretch: stricter local limits
        qualities[isolated + 1:isolated + 6] = [21] * 5
        peaks = [30 + 12 * i for i in range(len(long))]
        peaks[isolated] -= 4                                     # too close for the local rule there
        project = Project()
        trace = make_trace(project, 'r1', long, qualities, peaks=peaks)
        for index in (early, late):
            add_peak(trace, 'T', trace.peaks[index], 0.8)        # mixed: the local rules leave both open
        contig = make_contig(project, [forward_source(trace, 'r1', range(len(long)))])
        outcome, sites = local_review(project)
        self.assertEqual([site.column for site in sites], [early, late, isolated])
        reply = '{"decisions":[' + ','.join(
            '{"id":"P%05d","call":"C","confidence":"high"}' % number for number in (1, 2, 3)) + ']}'
        with patch('llm_review._generate', return_value=reply):
            model_review(project, sites, ReviewSettings(key='test-credential', model='model-x'), outcome)
        # Early in the read a confirmation is enough; later it also needs an isolated summit.
        self.assertEqual((contig.call(early), contig.call(late), contig.call(isolated)), ('C', 'N', 'C'))
        self.assertEqual(contig.ai_notes[str(late)],
                         f'model-x: suggests C; single read past base {SINGLE_READ_LIMIT}, peak not isolated, not applied')
        self.assertEqual((outcome.model_resolved, outcome.model_suggested), (2, 1))
        self.assertTrue(contig.ai_unresolved(late))

    def test_interrupted_model_step_changes_nothing(self):
        project = Project()
        contig = length_difference(project)
        sites = review_sites(project)
        outcome = ReviewOutcome(sites=len(sites))
        with patch('llm_review._generate', side_effect=ReviewError('Connection failed')):
            with self.assertRaisesRegex(ReviewError, 'Connection failed'):
                model_review(project, sites, ReviewSettings(key='test-credential'), outcome)
        self.assertEqual((contig.ai_review, outcome.completed, outcome.model_resolved), ({}, False, 0))

    def test_model_removal_is_recorded_as_an_empty_call(self):
        project = Project()
        contig = length_difference(project)
        sites = review_sites(project)
        outcome = ReviewOutcome(sites=len(sites))
        reply = '{"decisions":[{"id":"P00001","call":"-","confidence":"high"}]}'
        with patch('llm_review._generate', return_value=reply):
            model_review(project, sites, ReviewSettings(key='test-credential', model='model-x', effort='high'), outcome)
        self.assertEqual((contig.ai_review, contig.ai_notes), ({'9': ''}, {'9': 'model-x: high confidence'}))
        self.assertEqual((outcome.model_resolved, outcome.removed, outcome.unresolved), (1, 1, 0))

    def test_length_disagreement_needs_high_reasoning_effort(self):
        for effort in ('Default', 'low', 'medium'):
            with self.subTest(effort=effort):
                project = Project()
                contig = length_difference(project)
                sites = review_sites(project)
                outcome = ReviewOutcome(sites=len(sites))
                reply = '{"decisions":[{"id":"P00001","call":"-","confidence":"high"}]}'
                with patch('llm_review._generate', return_value=reply):
                    model_review(project, sites, ReviewSettings(key='test-credential', model='model-x', effort=effort),
                                 outcome)
                self.assertEqual(contig.ai_review, {'9': 'N'})
                self.assertIn('suggests no base', contig.ai_notes['9'])
                self.assertIn('High reasoning effort', contig.ai_notes['9'])
                self.assertEqual((outcome.model_resolved, outcome.model_suggested), (0, 1))


class BatchReviewTests(unittest.TestCase):
    def test_batch_review_writes_decisions_and_never_needs_a_network(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'in'
            create_demo(source)
            (source / 'Demo.json').unlink()
            (source / 'README.md').unlink(missing_ok=True)
            with patch('llm_review._post', side_effect=AssertionError('local review called the network')):
                summary = run_batch(source, Path(directory) / 'out', review=True)
            review = summary['review']
            self.assertEqual(review['sent'], 0)
            self.assertEqual(review['sites'], review['local_resolved'] + review['unresolved'])
            rows = (Path(directory) / 'out' / 'review.tsv').read_text().splitlines()
            self.assertEqual(rows[0].split('\t'), ['name', 'position', 'consensus', 'flag', 'decision', 'note'])
            self.assertEqual(len(rows) - 1, review['sites'])
            self.assertIsNone(summary['review_failure'])
            plain = run_batch(source, Path(directory) / 'plain')
            self.assertNotIn('review', plain)
            self.assertFalse((Path(directory) / 'plain' / 'review.tsv').exists())

    def test_this_device_only_config_is_the_same_as_no_config(self):
        project = Project()
        length_difference(project)
        outcome, rows, failure = review_project(project, ReviewSettings(OFFLINE, ''))
        self.assertEqual((outcome.local_resolved, failure), (1, None))
        self.assertEqual(rows, [dict(name='Example', position=10, consensus='N', flag='indel',
                                     decision='remove', note='local rule: clean read')])


if __name__ == '__main__':
    unittest.main()
