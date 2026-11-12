"""AUTO assembly must only consume currently unassembled AB1 reads."""
import copy
from pathlib import Path
import random
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from Bio.Seq import Seq
from app import Editor
from auto_assembly import abi_sample_groups, assemble_groups
from core import Project
from mcp_server import quality_snapshot
from test_core import synthetic


class AutoAssemblyScopeTests(unittest.TestCase):
    def controller(self, project):
        return SimpleNamespace(project=project, status=Mock(), configure=Mock(),
            update_idletasks=Mock(), history_entry=Mock(side_effect=project.snapshot),
            remember=Mock(), refresh_tree=Mock(), show_contig=Mock(),
            confirm_rebuild=Mock(side_effect=AssertionError('AUTO must not rebuild existing contigs')))

    def pair(self, project, prefix, label, seed=73):
        rng=random.Random(seed)
        seq=''.join(rng.choice('ACGT') for _ in range(220))
        forward,_=synthetic(project,seq[:160],'',sample=label+'F')
        reverse,_=synthetic(project,str(Seq(seq[60:]).reverse_complement()),'',sample=label+'R')
        for read,suffix in ((forward,'left'),(reverse,'right')):
            read.path=str(Path('/unmodified-inputs')/f'{prefix}_{label}_{suffix}.ab1')
        return [forward.id,reverse.id]

    def run_auto(self, controller):
        with patch('app.messagebox.showinfo') as info:
            Editor.build_auto(controller)
        return info

    def test_manual_then_auto_preserves_manual_sources_consensus_and_flags(self):
        project=Project();manual_ids=self.pair(project,'first','manual')
        key=project.assemble_reads(manual_ids,'Manually_named')
        contig=project.contigs[key]
        contig.overrides['20']='C';contig.insertions['21']='AG';contig.black_marks['23']=False
        # A manual contig need not use all reads with the same filename prefix.
        auto_ids=self.pair(project,'first','new')
        original=copy.deepcopy(contig);reads=copy.deepcopy(project.reads)
        controller=self.controller(project)
        with patch.object(project,'assemble_reads',wraps=project.assemble_reads) as assemble:
            self.run_auto(controller)
        self.assertEqual(assemble.call_count,1)
        self.assertEqual(assemble.call_args.args[0],auto_ids)
        self.assertEqual(project.contigs[key],original)
        self.assertIs(project.contigs[key],contig)
        self.assertEqual(project.reads,reads)
        self.assertEqual(len(project.contigs),2)
        controller.confirm_rebuild.assert_not_called();controller.remember.assert_called_once()

    def test_repeated_auto_is_a_noop_without_dirty_or_undo_changes(self):
        project=Project();self.pair(project,'sample','initial')
        self.run_auto(self.controller(project));project.dirty=False
        before=project.snapshot();originals=dict(project.contigs)
        controller=self.controller(project)
        with patch.object(project,'assemble_reads',wraps=project.assemble_reads) as assemble:
            info=self.run_auto(controller)
        self.assertEqual(project.snapshot(),before);self.assertFalse(project.dirty)
        for key,contig in originals.items():self.assertIs(project.contigs[key],contig)
        assemble.assert_not_called();controller.history_entry.assert_not_called()
        controller.remember.assert_not_called();controller.refresh_tree.assert_not_called()
        controller.configure.assert_not_called();info.assert_not_called()
        self.assertIn('No unassembled AB1 reads',controller.status.set.call_args.args[0])

    def test_empty_project_does_not_change_state_or_history(self):
        project=Project();controller=self.controller(project)
        self.run_auto(controller)
        self.assertFalse(project.contigs);self.assertFalse(project.dirty)
        controller.history_entry.assert_not_called();controller.remember.assert_not_called()

    def test_stale_and_legacy_contig_sources_remain_excluded(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy):
                project=Project();ids=self.pair(project,'used','first')
                key=project.assemble_reads(ids,'Used')
                if legacy:project.contigs[key].sources=[]
                project.contigs[key].stale=True
                before=copy.deepcopy(project.contigs[key]);project.dirty=False
                controller=self.controller(project)
                with patch.object(project,'assemble_reads',wraps=project.assemble_reads) as assemble:
                    self.run_auto(controller)
                assemble.assert_not_called()
                self.assertEqual(project.contigs[key],before);self.assertFalse(project.dirty)
                controller.history_entry.assert_not_called();controller.remember.assert_not_called()

    def test_new_reads_reusing_auto_prefix_get_unique_key_and_name(self):
        project=Project();self.pair(project,'sample','first')
        self.run_auto(self.controller(project))
        old_key=project.key('sample','auto');old=project.contigs[old_key]
        old.overrides['10']='A';before=copy.deepcopy(old)
        new_ids=self.pair(project,'sample','second')
        self.run_auto(self.controller(project))
        self.assertEqual(project.contigs[old_key],before);self.assertIs(project.contigs[old_key],old)
        new_key=project.key('sample','auto_2')
        self.assertEqual(set(project.contigs[new_key].read_ids()),set(new_ids))
        self.assertEqual(project.contigs[new_key].sequence_name,'sample_2')
        self.assertEqual(len({co.sequence_name for co in project.contigs.values()}),2)

    def test_groups_use_original_filename_prefix_not_display_name_or_guess(self):
        project=Project();ids=self.pair(project,'filename','different_metadata')
        project.reads[ids[0]].name='renamed_read';project.reads[ids[0]].sample='wrong'
        singleton,_=synthetic(project,'ACGT'*30,'',sample='single')
        singleton.path='/unmodified-inputs/second.ab1'
        self.run_auto(self.controller(project))
        self.assertEqual(set(project.contigs[project.key('filename','auto')].read_ids()),set(ids))
        second=project.contigs[project.key('second','auto')]
        self.assertEqual(second.read_ids(),[singleton.id]);self.assertEqual(second.sequence_name,'second')

    def test_sanitized_name_collisions_are_resolved_without_overwrite(self):
        project=Project();first=self.pair(project,'a b','one')
        second=self.pair(project,'a>b','two')
        self.run_auto(self.controller(project))
        one=project.contigs[project.key('a b','auto')];two=project.contigs[project.key('a>b','auto')]
        self.assertEqual(set(one.read_ids()),set(first));self.assertEqual(set(two.read_ids()),set(second))
        self.assertEqual((one.sequence_name,two.sequence_name),('a_b','a_b_2'))

    def test_abi_names_group_by_sample_and_keep_runs_separate(self):
        project = Project()
        expected = {}
        for folder, names, group in (
            ('run1', ('A01_64ar', 'A02_64bf'), '64'),
            ('run2', ('64f_001_A09', '64r_A10'), '64_2'),
            ('run3', ('13a-PREMIX', '13c-PREMIX'), '13'),
        ):
            ids = self.pair(project, 'unused', folder)
            for rid, name in zip(ids, names):
                project.reads[rid].path = f'/inputs/{folder}/{name}.ab1'
            expected[group] = ids
        for folder, name in (('run1', '98f_A09'), ('run2', '98r_A10'), ('run3', 'HiDi_G09')):
            read, _ = synthetic(project, 'ACGT' * 30, '')
            read.path = f'/inputs/{folder}/{name}.ab1'
        self.assertEqual(abi_sample_groups(project), expected)

    def test_fully_trimmed_read_does_not_prevent_usable_mate_from_assembling(self):
        project = Project()
        first, second = self.pair(project, 'sample', 'group')
        project.reads[second].start = project.reads[second].end = 0
        made, errors = assemble_groups(project, {'sample': [first, second]})
        self.assertEqual(errors, [])
        self.assertEqual(project.contigs[made[0]].read_ids(), [first])
        summary, _ = quality_snapshot(project, made)
        self.assertEqual((summary['reads_used'], summary['fully_trimmed_reads'], summary['single_read_contigs']),
                         (1, 1, 1))

    def test_abi_normalizes_primer_separators_without_merging_distinct_samples(self):
        project = Project()
        expected = {}
        for folder, names, group in (
            ('one', ('660af_A01', '660_cr_A02'), '660'),
            ('two', ('660-af', '660bf'), '660_3'),
            ('one', ('660_2_af_F01', '660_2cr_F03'), '660_2'),
            ('fas', ('AY226_rbcL_af3-PREMIX', 'AY226-rbcLcr3_PREMIX'), 'AY226_rbcL'),
            ('fas', ('A01_rbcL_af3', 'A01_rbcL_cr3'), 'A01_rbcL'),
        ):
            ids = self.pair(project, 'unused', folder)
            for rid, name in zip(ids, names):
                project.reads[rid].path = f'/inputs/{folder}/{name}.ab1'
            expected[group] = ids
        self.assertEqual(abi_sample_groups(project), expected)


if __name__=='__main__':unittest.main()
