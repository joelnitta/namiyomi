import contextlib
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

import batch
from core import Project
from scripts.create_demo import abif_bytes, create_demo


def write_ab1(path, sequence, seed=1):
    path.write_bytes(abif_bytes(sequence, seed, 'synthetic'))  # SMPL tag is ASCII-only
    return path


def random_seq(seed, length=500):
    return ''.join(random.Random(seed).choices('ACGT', k=length))


class BatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.demo = self.root / 'demo'
        create_demo(self.demo)
        self.out = self.root / 'out'

    def tearDown(self):
        self._tmp.cleanup()

    def snapshot(self, directory):
        return {p.name: p.read_bytes() for p in Path(directory).glob('*.ab1')}

    def test_matches_gui_workflow_on_demo(self):
        before = self.snapshot(self.demo)
        summary = batch.run_batch(self.demo, self.out)
        self.assertEqual(before, self.snapshot(self.demo))  # originals untouched
        gui = Project.load(self.demo / 'Demo.json')  # what the GUI workflow produced
        mine = Project.load(self.out / 'project.json')
        self.assertEqual(len(mine.contigs), len(gui.contigs))
        gui_by_name = {c.sequence_name: c for c in gui.contigs.values()}
        for contig in mine.contigs.values():
            ref = gui_by_name[contig.sequence_name]
            self.assertEqual(contig.sequence(), ref.sequence())
            self.assertEqual(contig.issues(), ref.issues())
            self.assertFalse(contig.stale)
        cats = {r['name']: r['category'] for r in summary['contigs']}
        self.assertEqual(cats, {'Demo01': 'needs_review', 'Demo02': 'clean'})
        self.assertEqual(summary['counts']['failed'], 0)
        for name in ('project.json', 'summary.json', 'summary.tsv', 'auto.fasta'):
            self.assertTrue((self.out / name).exists())

    def test_dry_run_writes_nothing(self):
        summary = batch.run_batch(self.demo, self.out, dry_run=True)
        self.assertTrue(summary['dry_run'])
        self.assertEqual(len(summary['plan']['groups']), 2)
        self.assertFalse(self.out.exists())

    def test_deterministic_outputs(self):
        batch.run_batch(self.demo, self.out)
        first = {n: (self.out / n).read_bytes() for n in ('summary.json', 'summary.tsv', 'auto.fasta')}
        batch.run_batch(self.demo, self.out)  # rerun into an existing output directory
        self.assertEqual(first, {n: (self.out / n).read_bytes() for n in first})

    def test_sheet_mode_groups_by_sheet_and_reports_gaps(self):
        sheet = self.root / 'sheet.csv'
        sheet.write_text('file,sample,marker\n'
                         'Demo01_F.ab1,S1,rbcL\nDemo01_R.ab1,S1,rbcL\n'
                         'Demo02_F.ab1,S2,matK\nGhost.ab1,S9,matK\n', encoding='utf-8')
        summary = batch.run_batch(self.demo, self.out, sheet=sheet)
        self.assertEqual(summary['unassigned'], ['Demo02_R.ab1'])
        self.assertEqual(summary['sheet_missing'], ['Ghost.ab1'])
        cats = {r['name']: r['category'] for r in summary['contigs']}
        self.assertEqual(cats, {'S1_rbcL': 'needs_review', 'S2_matK': 'single_read'})
        # single-read is not exported unless requested
        self.assertEqual(summary['fasta'], ['rbcL.fasta'])
        self.assertEqual(summary['single_read_not_exported'], ['S2_matK'])
        out2 = self.root / 'out2'
        summary = batch.run_batch(self.demo, out2, sheet=sheet, allow_single=True)
        self.assertEqual(sorted(summary['fasta']), ['matK.fasta', 'rbcL.fasta'])

    def test_bad_sheets_are_rejected(self):
        for text in ('file,sample\nA.ab1,S1\n',
                     'file,sample,marker\nA.ab1,S1,\n',
                     'file,sample,marker\nA.ab1,S 1,rbcL\n',
                     'file,sample,marker\nA.ab1,S1,rbcL\nA.ab1,S2,rbcL\n'):
            sheet = self.root / 'bad.csv'
            sheet.write_text(text, encoding='utf-8')
            with self.assertRaises(ValueError, msg=text):
                batch.read_sheet(sheet)

    def test_failures_do_not_abort_batch(self):
        d = self.root / 'mixed'
        d.mkdir()
        base = random_seq(1)
        write_ab1(d / 'A_F.ab1', base[:400], 1)
        write_ab1(d / 'A_R.ab1', base[100:], 2)        # overlaps A_F, same orientation
        write_ab1(d / 'B_F.ab1', random_seq(2), 3)     # unrelated pair: no overlap
        write_ab1(d / 'B_R.ab1', random_seq(3), 4)
        write_ab1(d / 'C_F.ab1', random_seq(4), 5)     # single read
        (d / 'D_F.ab1').write_bytes(b'not an ab1 file')  # corrupt
        summary = batch.run_batch(d, self.out)
        cats = {r['name']: r['category'] for r in summary['contigs']}
        self.assertEqual(cats['A'], 'clean')
        self.assertEqual(cats['C'], 'single_read')
        self.assertEqual([f['group'] for f in summary['failed']], ['B/auto', 'D/auto'])
        self.assertIn('No sufficient overlap', summary['failed'][0]['error'])
        self.assertEqual(len(summary['read_errors']), 1)
        self.assertIn('D_F.ab1', summary['read_errors'][0])
        tsv = (self.out / 'summary.tsv').read_text()
        self.assertIn('B/auto\tB\tauto\tfailed', tsv)
        self.assertEqual(summary['single_read_not_exported'], ['C'])

    def test_duplicate_sanitized_names_get_suffixes(self):
        d = self.root / 'dups'
        d.mkdir()
        write_ab1(d / 'a b_F.ab1', random_seq(7), 1)
        write_ab1(d / 'a\u00a0b_F.ab1', random_seq(8), 2)  # NBSP also sanitizes to '_'
        summary = batch.run_batch(d, self.out, allow_single=True)
        self.assertEqual(sorted(r['name'] for r in summary['contigs']), ['a_b', 'a_b_2'])
        # both sample IDs contain whitespace: not valid FASTA identifiers, reported, no crash
        self.assertEqual(summary['not_exported_invalid_sample_id'], ['a_b', 'a_b_2'])
        self.assertEqual(summary['fasta'], [])

    def test_empty_folder_and_bad_output(self):
        empty = self.root / 'empty'
        empty.mkdir()
        with self.assertRaises(ValueError):
            batch.run_batch(empty, self.out)
        with self.assertRaises(ValueError):
            batch.run_batch(self.demo, self.demo)
        with self.assertRaises(ValueError):
            batch.run_batch(self.root / 'missing', self.out)

    def test_non_ascii_filenames(self):
        d = self.root / 'nihongo'
        d.mkdir()
        seq = random_seq(11)
        write_ab1(d / '標本1_F.ab1', seq[:400], 1)
        write_ab1(d / '標本1_R.ab1', seq[100:], 2)
        summary = batch.run_batch(d, self.out)
        self.assertEqual(summary['contigs'][0]['name'], '標本1')
        self.assertEqual(len(Project.load(self.out / 'project.json').contigs), 1)
        json.loads((self.out / 'summary.json').read_text(encoding='utf-8'))

    def test_cli(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(batch.main(['--input', str(self.demo), '--output', str(self.out)]), 0)
            self.assertEqual(batch.main(['--input', str(self.root / 'nope'), '--output', str(self.out)]), 2)


if __name__ == '__main__':
    unittest.main()
