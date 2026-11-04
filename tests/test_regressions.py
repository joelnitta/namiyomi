"""Regression coverage for review, clipboard, orientation, scoped Undo and recovery."""
import copy
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace, MethodType
from unittest.mock import Mock, patch

from Bio.Seq import Seq
from app import Editor
from assembly import orientation_match
from core import Project, ScopedSnapshot, classify_import_paths
from recovery import RecoveryStore
from test_core import synthetic, demo_project
import test_interface


class RegressionTests(unittest.TestCase):
    def controller(self):return test_interface.InterfaceTests().editing_controller()

    def test_mixed_sequencing_drop_imports_ab1_and_reports_ignored_fasta(self):
        with tempfile.TemporaryDirectory() as directory:
            paths=[Path(directory)/name for name in ('read one.ab1','read one.fasta','two.AB1')]
            for path in paths:path.touch()
            kind,accepted=classify_import_paths(paths)
            self.assertEqual(kind,'ab1');self.assertEqual(accepted,[paths[0].resolve(),paths[2].resolve()])
            status=Mock();status.get.return_value='Imported 2 reads'
            c=SimpleNamespace(status=status,import_paths=Mock())
            Editor.accept_paths(c,paths)
            c.import_paths.assert_called_once_with(accepted)
            self.assertIn('Ignored 1',status.set.call_args.args[0])

    def test_consensus_and_source_base_edits_do_not_snapshot_other_contigs(self):
        for track in ('consensus','F','R'):
            c=test_interface.InterfaceTests().editing_controller(track=track);p=c.project
            original=p.contigs[c.group_key]
            for index in range(42):p.contigs[f'extra{index}']=copy.deepcopy(original)
            others={key:id(co) for key,co in p.contigs.items() if key!=c.group_key}
            # Source edits legitimately touch all contigs sharing that read;
            # unrelated contigs have different read IDs (not 43 copies of one read).
            if track!='consensus':
                for key in others:
                    p.contigs[key].f_id=p.contigs[key].r_id=None
            with patch.object(p,'snapshot',side_effect=AssertionError('whole-project copy')):
                c.delete_base()
                self.assertIsInstance(c.undo_stack[-1][0],ScopedSnapshot)
                self.assertEqual(set(c.undo_stack[-1][0].contigs),{c.group_key})
                # The artificial unrelated contigs are removed before Undo's
                # global signature validation; snapshot scope is tested above.
                for key in others:self.assertEqual(id(p.contigs[key]),others[key])
                for key in others:p.contigs.pop(key)
                c.undo();c.redo()

    def test_review_all_reason_types_and_undo_without_changing_quality(self):
        for reason in ('manual_read','quality','F_quality','R_quality','conflict'):
            c=self.controller();co=c.project.contigs[c.group_key]
            co.columns[100].reason=reason;co.columns[100].call='A'
            q=co.columns[100].quality
            self.assertTrue(c.is_marked(100))
            c.edit_base('A')
            self.assertFalse(c.is_marked(100));self.assertEqual(co.columns[100].quality,q)
            self.assertEqual(len(c.undo_stack),1)
            c.undo();self.assertTrue(c.is_marked(100))

    def test_review_range_and_evidence_change_invalidates_old_approval(self):
        c=self.controller();co=c.project.contigs[c.group_key]
        c.anchor=98;c.mark_reviewed()
        self.assertTrue(all(co.black_marks[str(i)] is False for i in range(98,101)))
        c.project.edit_aligned_read(c.group_key,'F',[100],'')
        self.assertNotIn('100',co.black_marks)

    def test_new_contigs_follow_first_matching_orientation(self):
        random.seed(1104);sequence=''.join(random.choice('ACGT') for _ in range(350))
        p=Project()
        a,_=synthetic(p,sequence,'',sample='first')
        b,_=synthetic(p,str(Seq(sequence).reverse_complement()),'',sample='second')
        first=p.assemble_reads([a.id],'First');second=p.assemble_reads([b.id],'Second')
        self.assertEqual(p.contigs[first].sequence(),sequence)
        self.assertEqual(p.contigs[second].sequence(),sequence)
        self.assertTrue(p.contigs[second].sources[0].reverse)
        self.assertIsNone(p.normalize_orientation(first))
        p.reverse_contig(second);self.assertTrue(p.normalize_orientation(second)[1])
        self.assertEqual(p.contigs[second].sequence(),sequence)
        unrelated,_=synthetic(p,'A'*350,'',sample='third')
        third=p.assemble_reads([unrelated.id],'Third')
        self.assertEqual(p.contigs[third].sequence(),'A'*350)
        self.assertIsNone(p.normalize_orientation(third))
        self.assertIsNone(orientation_match('AT'*150,'AT'*150,p.settings))

    def test_unique_default_names_and_explicit_collision_rejected_atomically(self):
        c=self.controller();p=c.project;ids=list(p.reads)
        p.contigs.clear()
        one=p.assemble_reads(ids,'Contig_1');two=p.assemble_reads(ids,'Contig_2')
        p.remove_sequences(contig_keys=[one])
        self.assertEqual(p.next_contig_name(),'Contig_1')
        before=p.snapshot()
        with self.assertRaisesRegex(ValueError,'already'):p.assemble_reads(ids,'Contig_2')
        with self.assertRaisesRegex(ValueError,'already'):
            other=p.assemble_reads(ids,'Other');p.rename_contig(other,'Contig_2')
        self.assertEqual(p.contigs[two].sequence_name,'Contig_2')

    def test_read_export_keeps_displayed_low_quality_calls_cuts_and_orientation(self):
        p=Project();read,trace=synthetic(p,'AACGTACGTT','',sample='raw')
        trace.qualities=[0]*len(trace.sequence);read.start=2;read.end=8
        read.edits['4']='';read.reverse=True
        self.assertEqual(p.fasta_record('read',read.id)[1],str(Seq('CGACG').reverse_complement()))
        self.assertTrue(p.read_issues(read.id))
        read.black_marks={str(i):False for i in range(2,8)}
        self.assertFalse(p.read_issues(read.id))

    def test_list_copy_fasta_contains_only_selected_alias_and_named_read(self):
        c=self.controller();p=c.project;rid=next(iter(p.reads))
        p.rename_read(rid,'Only_this_read');c.tree_reads={'alias':rid};c.tree.selection.return_value=['alias']
        for method in ('selected_items','selected_records','copy_fasta','copy_selected_sequences','put_clipboard'):
            setattr(c,method,MethodType(getattr(Editor,method),c))
        c.clipboard_clear=Mock();c.clipboard_append=Mock()
        c.copy_fasta(True);text=c.clipboard_append.call_args.args[0]
        self.assertEqual(text.count('>'),1);self.assertTrue(text.startswith('>Only_this_read\n'))
        self.assertNotIn(p.contigs[c.group_key].sequence_name,text)
        c.copy_selected_sequences()
        self.assertEqual(c.clipboard_append.call_args.args[0],p.fasta_record('read',rid)[1])

    def test_canvas_copy_only_selected_bases_and_insertions(self):
        c=self.controller();co=c.project.contigs[c.group_key]
        co.insertions['99']='TG';co.overrides['100']='';c.anchor=98;c.selection=100
        c.canvas_focus=lambda:True;c.put_clipboard=Mock()
        Editor.copy_sequence(c)
        self.assertEqual(c.put_clipboard.call_args.args[0],co.call(98)+'TG'+co.call(99))

    def test_sidebar_refresh_preserves_keyboard_focus_and_stable_contig_ids(self):
        c=self.controller();key=c.group_key;item='contig:'+key
        c.tree_groups={item:key};c.tree.selection.return_value=[item];c.tree.focus.return_value=item
        c.tree.get_children.return_value=[];c.tree.item.return_value=True;c.after_idle=lambda f:f()
        Editor.refresh_tree(c)
        c.tree.focus.assert_called_with(item)
        c.tree.selection_set.assert_called_with([item])
        self.assertEqual(c.tree_groups[item],key)
        rows=c.tree.insert.call_args_list
        self.assertTrue(any(call.kwargs['iid']==item and call.kwargs['open'] for call in rows))

    def test_selecting_view_never_collapses_existing_multi_selection(self):
        c=self.controller();rid=c.read_id or next(iter(c.project.reads));c.tree_reads={'alias':rid}
        c.tree.selection.return_value=['alias','other'];c.focus_get.return_value=c.tree
        Editor.select_view_item(c,'read',rid)
        c.tree.selection_set.assert_not_called();c.tree.focus.assert_called_with('alias')

    def test_opened_contig_gets_sidebar_keyboard_focus_without_stealing_canvas_focus(self):
        c=self.controller();c.tree.selection.return_value=[]
        Editor.select_view_item(c,'contig',c.group_key)
        c.tree.selection_set.assert_called_once_with('group:0');c.tree.focus.assert_called_with('group:0')
        c.tree.focus_set.assert_not_called()

    def test_project_export_preserves_current_path_and_unsaved_state(self):
        c=self.controller();p=c.project
        with tempfile.TemporaryDirectory() as directory:
            original=(Path(directory)/'original.json').resolve();export=(Path(directory)/'copy.json').resolve()
            p.save(original);saved=original.read_bytes();p.contigs[c.group_key].overrides['100']='N';p.dirty=True
            c.focus_track=Mock();c.update_title=Mock();c.clear_recovery=Mock()
            with patch('app.filedialog.asksaveasfilename',return_value=str(export)):
                self.assertTrue(Editor.save_project(c,True))
            self.assertEqual(p.path,original);self.assertTrue(p.dirty)
            self.assertEqual(original.read_bytes(),saved);self.assertTrue(export.exists())
            c.clear_recovery.assert_not_called()
            self.assertTrue(Editor.save_project(c));self.assertFalse(p.dirty)
            self.assertNotEqual(original.read_bytes(),saved)


class RecoveryTests(unittest.TestCase):
    def test_startup_offer_waits_for_a_mapped_window_and_native_sheet_has_parent(self):
        c=SimpleNamespace(winfo_viewable=Mock(return_value=False),after=Mock(),recovery=Mock(),status=Mock(),recover_project=Mock())
        c.offer_recovery=MethodType(Editor.offer_recovery,c)
        c.offer_recovery();c.after.assert_called_once_with(250,c.offer_recovery)
        c.recovery.candidates.assert_not_called()
        c.winfo_viewable.return_value=True
        c.recovery.candidates.return_value=[(1,Path('/test.recovery.json'),dict(original_path='/test.json'))]
        with patch('app.messagebox.askyesno',return_value=False) as ask:
            c.offer_recovery();self.assertIs(ask.call_args.kwargs['parent'],c)
        c.recover_project.assert_not_called()

    def test_orphan_after_abrupt_subprocess_exit_and_original_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            original=(Path(directory)/'original.json').resolve()
            p=Project();p.save(original);saved=original.read_bytes()
            code=("import os,sys; from pathlib import Path; from core import Project; from recovery import RecoveryStore; "
                  "p=Project.load(sys.argv[2]); p.settings['min_quality']=27; p.dirty=True; "
                  "RecoveryStore(sys.argv[1]).write(p,1); os._exit(3)")
            result=subprocess.run([sys.executable,'-c',code,directory,str(original)],cwd=Path(__file__).resolve().parents[1]/'src')
            self.assertEqual(result.returncode,3)
            store=RecoveryStore(directory);candidates=store.candidates();self.assertEqual(len(candidates),1)
            path=candidates[0][1];recovered=store.load(path)
            self.assertEqual(recovered.path,original);self.assertTrue(recovered.dirty)
            self.assertEqual(recovered.settings['min_quality'],27);self.assertEqual(original.read_bytes(),saved)
            store.adopt(path,recovered,1);self.assertFalse(path.exists());self.assertTrue(store.path.exists())
            store.clear();self.assertFalse(store.path.exists())

    def test_live_window_snapshot_is_not_offered_and_same_revision_is_not_written(self):
        with tempfile.TemporaryDirectory() as directory:
            store=RecoveryStore(directory);p=Project();p.dirty=True
            self.assertTrue(store.write(p,1));timestamp=store.path.stat().st_mtime_ns
            self.assertFalse(store.write(p,1));self.assertEqual(store.path.stat().st_mtime_ns,timestamp)
            self.assertFalse(RecoveryStore(directory).candidates())
            p.dirty=False;self.assertFalse(store.write(p,2))
            if os.name!='nt':self.assertEqual(store.path.stat().st_mode & 0o777,0o600)

    def test_corrupt_snapshot_is_preserved_after_failed_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'broken.recovery.json';path.write_text('{"recovery":{}}')
            store=RecoveryStore(directory)
            with self.assertRaises(ValueError):store.load(path)
            self.assertTrue(path.exists())

    def test_recovered_read_edits_flags_and_ab1_checksums(self):
        with demo_project() as (p,key,original):
            rid=p.contigs[key].read_ids()[0];source=Path(p.reads[rid].path)
            before=source.read_bytes()
            p.edit_read_bases(rid,[100],'');p.reads[rid].black_marks['101']=False
            store=RecoveryStore(original.parent/'Recovery');self.assertTrue(store.write(p,1))
            q=store.load(store.path)
            self.assertEqual(q.reads[rid],p.reads[rid]);self.assertEqual(q.contigs,p.contigs)
            self.assertEqual(source.read_bytes(),before)
            source.write_bytes(before+b'altered')
            with self.assertRaisesRegex(ValueError,'has changed'):store.load(store.path)


if __name__=='__main__':unittest.main()
