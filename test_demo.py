import tempfile
from pathlib import Path
import unittest

from core import Project,Trace
from scripts.create_demo import create_demo


class DemoTests(unittest.TestCase):
    def test_synthetic_ab1s_are_parseable_and_project_is_portable(self):
        with tempfile.TemporaryDirectory() as directory:
            paths=create_demo(Path(directory));p=Project.load(Path(directory)/'Demo.json')
            self.assertEqual(len(paths),5);self.assertEqual(len(p.reads),4);self.assertEqual(len(p.contigs),2)
            self.assertTrue(all(not co.stale for co in p.contigs.values()))
            self.assertTrue(any(co.issues() for co in p.contigs.values()))
            for path in paths[:4]:
                trace=Trace.load(path);self.assertGreater(len(trace.sequence),400)
                self.assertEqual(set(trace.channels),set('ACGT'))
            self.assertNotIn('/Users/',(Path(directory)/'Demo.json').read_text())
            before={path:path.read_bytes() for path in paths}
            with self.assertRaises(FileExistsError):create_demo(Path(directory))
            self.assertEqual(before,{path:path.read_bytes() for path in paths})


if __name__=='__main__':unittest.main()
