"""Independent edge-case checks for build 7's search and document behavior."""
import copy
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from Bio.Seq import Seq

from app import Editor
from core import Base, Contig, Column, Project, Source
from sequence_find import consensus_coordinates, find_matches
from test_core import synthetic


class IndependentBuild7Tests(unittest.TestCase):
    def test_random_edited_consensus_coordinates_remain_valid_after_reverse(self):
        rng = random.Random(71104)
        alphabet = 'ACGTRYSWKMBDHVN'
        for _ in range(120):
            count = rng.randrange(1, 35)
            contig = Contig('sample', 'marker', None, None, [], [],
                [Column(None, None, rng.choice(alphabet), 'single', 40)
                    for _ in range(count)], '', 0, None, '')
            for index in range(count):
                if rng.random() < .3:
                    contig.overrides[str(index)] = rng.choice(alphabet) if rng.random() < .5 else ''
            for index in range(count + 1):
                if rng.random() < .25:
                    contig.insertions[str(index)] = ''.join(rng.choice(alphabet) for _ in range(rng.randrange(1, 8)))
            before = contig.sequence()
            project = Project()
            read, _ = synthetic(project, ''.join(column.call for column in contig.columns), '')
            contig.sources = [Source(read.id, False,
                [Base(column.call, index, 40) for index, column in enumerate(contig.columns)])]
            project.contigs['test'] = contig
            for expected in (before, str(Seq(before).reverse_complement())):
                sequence, coordinates = consensus_coordinates(contig)
                self.assertEqual(sequence, expected)
                self.assertEqual(len(sequence), len(coordinates))
                self.assertTrue(all(0 <= column < count for column in coordinates))
                self.assertEqual(list(coordinates), sorted(coordinates))
                if sequence:
                    start = rng.randrange(len(sequence))
                    query = sequence[start:start + 7]
                    self.assertTrue(any(hit.start == start for hit in find_matches(sequence, query)))
                project.reverse_contig('test')
            self.assertEqual(contig.sequence(), before)

    def test_failed_first_save_does_not_adopt_filename_or_clear_recovery(self):
        project = Project()
        project.dirty = True
        controller = SimpleNamespace(project=project, focus_track=Mock(),
            clear_recovery=Mock(), update_title=Mock(), status=Mock())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '20261104_test.json'
            with patch('app.filedialog.asksaveasfilename', return_value=str(path)), \
                 patch('core.atomic_text', side_effect=OSError('disk full')), \
                 patch('app.messagebox.showerror'):
                self.assertFalse(Editor.save_project(controller))
            self.assertIsNone(project.path)
            self.assertTrue(project.dirty)
            self.assertFalse(path.exists())
            controller.clear_recovery.assert_not_called()

    def test_cancel_save_as_preserves_existing_path_and_dirty_edits(self):
        project = Project()
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / 'original.json'
            project.save(original)
            saved = original.read_bytes()
            project.find_settings['identity'] = 85
            project.dirty = True
            state = copy.deepcopy(project.snapshot())
            controller = SimpleNamespace(project=project, focus_track=Mock(),
                clear_recovery=Mock(), update_title=Mock(), status=Mock())
            with patch('app.filedialog.asksaveasfilename', return_value=''):
                self.assertFalse(Editor.save_project(controller, save_as=True))
            self.assertEqual(project.path, original.resolve())
            self.assertEqual(project.snapshot(), state)
            self.assertTrue(project.dirty)
            self.assertEqual(original.read_bytes(), saved)
            controller.clear_recovery.assert_not_called()


if __name__ == '__main__':
    unittest.main()
