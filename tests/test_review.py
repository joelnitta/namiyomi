import tempfile
import unittest
from pathlib import Path

import batch
import review
from scripts.create_demo import create_demo

PNG = b'\x89PNG\r\n\x1a\n'
try:
    import matplotlib  # noqa: F401
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        demo = root / 'demo'
        create_demo(demo)
        self.out = root / 'out'
        self.summary = batch.run_batch(demo, self.out)
        self.project_path = self.out / 'project.json'
        self.reviewed = self.out / 'project.reviewed.json'
        self.flag = self.summary['contigs'][0]['issue_columns'][0]  # Demo01 has one conflict

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_and_details(self):
        project = review.load_project(self.project_path)
        self.assertEqual([r['name'] for r in review.list_contigs(project, 'needs_review')], ['Demo01'])
        self.assertEqual([r['name'] for r in review.list_contigs(project, 'clean')], ['Demo02'])
        with self.assertRaises(ValueError):
            review.list_contigs(project, 'bogus')
        d = review.contig_details(project, 'Demo01')
        self.assertEqual(len(d['sources']), 2)
        flag = d['flagged'][0]
        self.assertEqual(flag['column'], self.flag)
        self.assertEqual(flag['reason'], 'conflict')
        self.assertEqual(len(flag['reads']), 2)
        self.assertTrue(all(r['base'] and r['phred'] is not None for r in flag['reads']))
        self.assertEqual(review.contig_details(project, d['key'])['name'], 'Demo01')  # key works too
        with self.assertRaises(ValueError):
            review.contig_details(project, 'nope')

    def test_find_sequence(self):
        project = review.load_project(self.project_path)
        seq = next(c for c in project.contigs.values() if c.sequence_name == 'Demo02').sequence()
        hits = review.find_sequence(project, 'Demo02', seq[100:120])
        self.assertEqual(hits[0]['start'], 100)
        self.assertEqual(hits[0]['strand'], '+')

    @unittest.skipUnless(HAVE_MPL, 'matplotlib not installed')
    def test_render_trace(self):
        project = review.load_project(self.project_path)
        for ref, col in (('Demo01', self.flag), ('Demo01', 0), ('Demo02', 300)):
            png = review.render_trace(project, ref, col)
            self.assertTrue(png.startswith(PNG) and len(png) > 5000)
        with self.assertRaises(ValueError):
            review.render_trace(project, 'Demo01', 10 ** 6)

    def test_apply_edits_writes_copy_and_report(self):
        before = self.project_path.read_bytes()
        result = review.apply_review_edits(self.project_path, self.reviewed, [
            dict(contig='Demo01', column=self.flag, action='set', value='a', reason='Clean A peak in F')])
        self.assertEqual(self.project_path.read_bytes(), before)  # input untouched
        self.assertEqual(result['still_needs_review'], [])
        project = review.load_project(self.reviewed)
        key, contig = review.resolve_contig(project, 'Demo01')
        orig = review.resolve_contig(review.load_project(self.project_path), 'Demo01')[1]
        self.assertEqual(contig.call(self.flag), 'A')
        self.assertEqual(contig.issues(), [])
        other = [i for i in range(len(contig.columns)) if i != self.flag]
        self.assertTrue(all(contig.call(i) == orig.call(i) for i in other))
        report = Path(result['report']).read_text(encoding='utf-8')
        self.assertIn('Clean A peak in F', report)
        review.apply_review_edits(self.project_path, self.out / 'second.json', [
            dict(contig='Demo01', column=self.flag, action='confirm', reason='kept N')],
            report_path=result['report'])
        self.assertEqual(Path(result['report']).read_text(encoding='utf-8').count('## Claude'), 2)

    def test_confirm_marks_reviewed_without_changing_call(self):
        review.apply_review_edits(self.project_path, self.reviewed, [
            dict(contig='Demo01', column=self.flag, action='confirm', reason='N is correct here')])
        contig = review.resolve_contig(review.load_project(self.reviewed), 'Demo01')[1]
        self.assertEqual(contig.call(self.flag), 'N')
        self.assertNotIn(self.flag, contig.issues())
        self.assertEqual(contig.black_marks[str(self.flag)], False)

    def test_invalid_edits_change_nothing(self):
        good = dict(contig='Demo01', column=self.flag, action='set', value='A', reason='ok')
        bad_cases = [dict(good, value='Z'), dict(good, value='AC'), dict(good, reason=' '),
                     dict(good, column=10 ** 6), dict(good, action='delete'), dict(good, contig='nope')]
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=str(bad)):
                review.apply_review_edits(self.project_path, self.reviewed, [good, bad])
            self.assertFalse(self.reviewed.exists())
        with self.assertRaises(ValueError):
            review.apply_review_edits(self.project_path, self.project_path, [good])
        with self.assertRaises(ValueError):
            review.apply_review_edits(self.project_path, self.reviewed, [])

    def test_export_fasta_from_project(self):
        out = self.out / 'again'
        result = review.export_fasta(self.project_path, out)
        self.assertEqual(result['fasta'], ['auto.fasta'])
        self.assertEqual((out / 'auto.fasta').read_text(), (self.out / 'auto.fasta').read_text())


if __name__ == '__main__':
    unittest.main()
