import tempfile
import shutil
from pathlib import Path
import unittest

from core import Project,Trace
from scripts.create_demo import create_demo


class DemoTests(unittest.TestCase):
    def test_synthetic_ab1s_are_parseable_and_project_is_portable(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / 'original'
            paths = create_demo(original)
            moved = Path(directory) / 'moved'
            shutil.copytree(original, moved)
            p = Project.load(moved / 'Demo.json')
            self.assertEqual(len(paths),5);self.assertEqual(len(p.reads),4);self.assertEqual(len(p.contigs),2)
            self.assertTrue(all(not co.stale for co in p.contigs.values()))
            self.assertTrue(any(co.issues() for co in p.contigs.values()))
            for path in paths[:4]:
                trace=Trace.load(path);self.assertGreater(len(trace.sequence),400)
                self.assertEqual(set(trace.channels),set('ACGT'))
            self.assertNotIn('/Users/',(moved/'Demo.json').read_text())
            before={path:path.read_bytes() for path in paths}
            with self.assertRaises(FileExistsError):create_demo(original)
            self.assertEqual(before,{path:path.read_bytes() for path in paths})

    def test_legacy_signature_migrates_only_when_it_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, _, _, path = create_demo(Path(directory))
            project = Project.load(path)
            first, second = project.contigs.values()
            first.signature = project.signature(first.read_ids(), legacy_paths=True)
            second.signature = 'outdated'
            project.save(path)

            reopened = Project.load(path)
            first, second = reopened.contigs.values()
            self.assertFalse(first.stale)
            self.assertEqual(first.signature, reopened.signature(first.read_ids()))
            self.assertTrue(second.stale)


if __name__=='__main__':unittest.main()
