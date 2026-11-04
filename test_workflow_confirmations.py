"""Confirmation opt-outs and non-destructive contig dissolution."""
import copy
from pathlib import Path
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import Editor
from preferences import ConfirmationPreferences
import test_interface


class ConfirmationControllerTests(unittest.TestCase):
    def test_native_menu_callbacks_return_before_opening_tk_modal_windows(self):
        c=SimpleNamespace(after_idle=Mock());operation=Mock()
        callback=Editor.menu_action(c,operation);callback()
        c.after_idle.assert_called_once_with(operation);operation.assert_not_called()
        c.after_idle.call_args.args[0]();operation.assert_called_once_with()

    def controller(self,path):
        return SimpleNamespace(confirmations=ConfirmationPreferences(path),ask_confirmation=Mock())

    def test_first_time_asks_then_explicit_optout_persists_only_that_action(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'preferences.json';c=self.controller(path)
            c.ask_confirmation.return_value=(True,True)
            self.assertTrue(Editor.confirm_action(c,'delete_sequences','Delete','Message','Delete'))
            c.ask_confirmation.assert_called_once_with('Delete','Message','Delete')
            reopened=self.controller(path)
            self.assertTrue(Editor.confirm_action(reopened,'delete_sequences','Delete','Message','Delete'))
            reopened.ask_confirmation.assert_not_called()
            self.assertTrue(reopened.confirmations.should_ask('return_to_ab1'))
            self.assertTrue(reopened.confirmations.should_ask('export_review_flags'))
            self.assertTrue(reopened.confirmations.should_ask('unsaved_changes'))

    def test_unchecked_yes_and_checked_cancel_do_not_suppress(self):
        with tempfile.TemporaryDirectory() as directory:
            c=self.controller(Path(directory)/'preferences.json')
            for accepted,suppressed in ((True,False),(False,True),(False,False)):
                c.ask_confirmation.return_value=(accepted,suppressed)
                self.assertEqual(Editor.confirm_action(c,'delete_sequences','Delete','Message','Delete'),accepted)
                self.assertTrue(c.confirmations.should_ask('delete_sequences'))
            self.assertFalse(c.confirmations.path.exists())

    def test_failed_persistence_does_not_claim_next_confirmation_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            c=self.controller(Path(directory)/'preferences.json');c.ask_confirmation.return_value=(True,True)
            with patch('preferences.atomic_text',side_effect=OSError('disk full')),patch('app.messagebox.showerror') as show:
                self.assertTrue(Editor.confirm_action(c,'delete_sequences','Delete','Message','Delete'))
            show.assert_called_once();self.assertTrue(c.confirmations.should_ask('delete_sequences'))

    def test_delete_cancel_preserves_project_and_undo(self):
        c=test_interface.InterfaceTests().editing_controller();before=c.project.snapshot()
        c.tree.selection.return_value=['group:0'];c.confirm_action.return_value=False
        Editor.delete_sequences(c)
        self.assertEqual(c.project.snapshot(),before);self.assertFalse(c.undo_stack)

    def test_review_flag_warning_is_separately_scoped(self):
        c=test_interface.InterfaceTests().editing_controller();co=c.project.contigs[c.group_key]
        co.black_marks['100']=True;c.confirm_action.return_value=False
        self.assertFalse(Editor.confirm_export_issues(c,[('contig',c.group_key)]))
        self.assertEqual(c.confirm_action.call_args.args[0],'export_review_flags')
        co.black_marks={str(i):False for i in range(len(co.columns))};c.confirm_action.reset_mock()
        self.assertTrue(Editor.confirm_export_issues(c,[('contig',c.group_key)]));c.confirm_action.assert_not_called()


class ReturnToAB1Tests(unittest.TestCase):
    def controller(self):
        c=test_interface.InterfaceTests().editing_controller();c.refresh_view=Mock();c.show_read=Mock();c.show_contig=Mock()
        c.project.assemble_reads(c.project.contigs[c.group_key].read_ids(),'Original',c.group_key)
        c.return_reads_to_ab1=MethodType(Editor.return_reads_to_ab1,c)
        return c

    def test_return_preserves_sources_edits_cuts_and_undo_redo_restores_consensus(self):
        c=self.controller();p=c.project;key=c.group_key;co=p.contigs[key];rid=co.read_ids()[0]
        p.edit_read_bases(rid,[100],'A');p.reads[rid].start=5;p.invalidate()
        p.assemble_reads(co.read_ids(),'Original',key);co=p.contigs[key]
        co.overrides['101']='T';co.insertions['102']='GG';co.black_marks['103']=False
        before=p.snapshot();reads=dict(p.reads);traces=dict(p.traces)
        c.tree.selection.return_value=['group:0']
        c.return_reads_to_ab1()
        self.assertFalse(p.contigs);self.assertEqual(len(c.undo_stack),1)
        for source in reads:self.assertIs(p.reads[source],reads[source]);self.assertIs(p.traces[source],traces[source])
        self.assertEqual(p.reads[rid].start,5);self.assertEqual(p.reads[rid].edits['100'],'A')
        self.assertEqual(c.confirm_action.call_args.args[0],'return_to_ab1')
        c.tree.selection_set.assert_called_with(co.read_ids())
        c.undo();self.assertEqual(p.snapshot(),before)
        c.redo();self.assertFalse(p.contigs);self.assertEqual(p.reads[rid].edits['100'],'A')

    def test_cancel_changes_nothing_and_does_not_record_undo(self):
        c=self.controller();c.tree.selection.return_value=['group:0'];c.confirm_action.return_value=False
        before=c.project.snapshot();c.return_reads_to_ab1()
        self.assertEqual(c.project.snapshot(),before);self.assertFalse(c.undo_stack)
        c.refresh_tree.assert_not_called()

    def test_shared_reads_do_not_remove_or_edit_other_contigs(self):
        c=self.controller();p=c.project;other=copy.deepcopy(p.contigs[c.group_key]);other.name='Other'
        p.contigs['other']=other;before=copy.deepcopy(other)
        c.tree.selection.return_value=['group:0'];c.return_reads_to_ab1()
        self.assertEqual(list(p.contigs),['other']);self.assertEqual(p.contigs['other'],before)
        self.assertEqual(len(p.reads),2);c.show_read.assert_not_called();c.show_contig.assert_called_once_with('other')
        self.assertIn('shared',c.confirm_action.call_args.args[2])
        self.assertIn('Returned 0 read(s)',c.status.set.call_args.args[0])

    def test_multiple_contigs_and_mixed_read_selection_return_sources_once(self):
        c=self.controller();p=c.project;key=c.group_key
        p.contigs['other']=copy.deepcopy(p.contigs[key]);p.contigs['other'].name='Other'
        c.tree_groups['group:1']='other'
        c.tree.selection.return_value=['group:0','group:1',next(iter(p.reads))]
        c.return_reads_to_ab1()
        self.assertFalse(p.contigs);self.assertEqual(len(p.reads),2);self.assertEqual(len(c.undo_stack),1)
        self.assertIn('Returned 2 read(s)',c.status.set.call_args.args[0])

    def test_selecting_only_a_read_does_not_implicitly_dissolve_its_contig(self):
        c=self.controller();c.tree.selection.return_value=[next(iter(c.project.reads))]
        before=c.project.snapshot();c.return_reads_to_ab1()
        self.assertEqual(c.project.snapshot(),before);self.assertFalse(c.undo_stack);c.confirm_action.assert_not_called()

    def test_returned_read_rows_are_restored_under_ab1_not_deleted(self):
        c=self.controller();ids=list(c.project.reads);c.tree.selection.return_value=['group:0']
        c.return_reads_to_ab1()
        c.after_idle=lambda f:f();c.tree.selection.return_value=[];c.tree.get_children.return_value=[]
        Editor.refresh_tree(c)
        rows=[call.kwargs['iid'] for call in c.tree.insert.call_args_list if call.args[0]=='section:ab1']
        self.assertEqual(set(rows),set(ids));self.assertFalse(c.project.contigs)


if __name__=='__main__':unittest.main()
