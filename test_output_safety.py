"""Output-path safeguards; all fixtures are disposable fake source files."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import Project


class OutputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='namiyomi-output-safety-')
        self.addCleanup(self.temporary.cleanup)
        self.directory=Path(self.temporary.name)
        self.source=self.directory/'source.ab1'
        self.source.write_bytes(b'FAKE AB1 -- immutable test fixture')
        self.original=self.source.read_bytes()
        self.project=Project()
        self.project.traces['removed-read']=SimpleNamespace(path=str(self.source))
        self.project.dirty=True

    def assert_rejected_without_writes(self,path):
        for operation in (self.project.export_project,self.project.save):
            with self.subTest(operation=operation.__name__,path=path):
                with patch('core.atomic_text') as write:
                    with self.assertRaises(ValueError):operation(path)
                write.assert_not_called()
                self.assertIsNone(self.project.path)
                self.assertTrue(self.project.dirty)
                self.assertEqual(self.source.read_bytes(),self.original)

    def make_symlink(self,alias,target):
        try:alias.symlink_to(target)
        except OSError as exc:
            # Windows may require Developer Mode/admin for creating test links.
            if os.name=='nt' and getattr(exc,'winerror',None)==1314:
                self.skipTest('Creating symlinks requires Windows Developer Mode or administrator rights')
            raise

    def test_original_source_and_any_case_ab1_suffix_are_rejected(self):
        self.assert_rejected_without_writes(self.source)
        for name in ('another.ab1','another.AB1','another.Ab1'):
            target=self.directory/name
            self.assert_rejected_without_writes(target)
            self.assertFalse(target.exists())

    def test_symlink_alias_with_json_suffix_is_rejected(self):
        alias=self.directory/'source-alias.json'
        self.make_symlink(alias,self.source)
        self.assert_rejected_without_writes(alias)
        self.assertTrue(alias.is_symlink())

    def test_hardlink_alias_with_fasta_suffix_is_rejected(self):
        alias=self.directory/'source-alias.fasta'
        os.link(self.source,alias)
        self.assert_rejected_without_writes(alias)
        self.assertTrue(alias.samefile(self.source))

    def test_ab1_named_symlink_to_non_source_json_is_rejected(self):
        destination=self.directory/'ordinary.json'
        destination.write_bytes(b'preserve existing output')
        alias=self.directory/'misleading.ab1'
        self.make_symlink(alias,destination)
        self.assert_rejected_without_writes(alias)
        self.assertEqual(destination.read_bytes(),b'preserve existing output')

    def test_trace_source_with_nonstandard_suffix_is_protected(self):
        unusual=self.directory/'instrument-result.dat'
        unusual.write_bytes(self.original)
        self.project.traces['nonstandard']=SimpleNamespace(path=str(unusual))
        self.assert_rejected_without_writes(unusual)
        self.assertEqual(unusual.read_bytes(),self.original)

    def test_validation_has_no_writes_and_returns_resolved_regular_path(self):
        folder=self.directory/'new-folder'
        destination=folder/'ordinary.json'
        self.assertEqual(self.project.validate_output_path(destination),destination.resolve())
        self.assertFalse(folder.exists())
        self.project.save(destination)
        self.assertTrue(destination.exists())
        self.assertEqual(self.project.path,destination.resolve())
        self.assertFalse(self.project.dirty)
        self.assertEqual(self.source.read_bytes(),self.original)

    def test_fasta_validation_accepts_normal_output_and_rejects_source_alias(self):
        destination=self.directory/'ordinary.fasta'
        self.assertEqual(self.project.validate_output_path(destination),destination.resolve())
        self.assertFalse(destination.exists())
        alias=self.directory/'instrument.fasta'
        self.make_symlink(alias,self.source)
        with self.assertRaises(ValueError):self.project.validate_output_path(alias)


if __name__=='__main__':unittest.main()
