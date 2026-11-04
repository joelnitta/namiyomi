"""Headless controller tests; no desktop windows or synthetic OS input."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from app import Editor
from core import Project, bases_for_read
from tkinterdnd2 import COPY, REFUSE_DROP
import test_core
import test_assembly


class InterfaceTests(unittest.TestCase):
    def test_developer_dialog_uses_namiyomi_brand_and_requested_credit(self):
        with patch('app.messagebox.showinfo') as show:
            Editor.developer_dialog(SimpleNamespace())
        show.assert_called_once_with('Developer',
            'namiyomi ver 0.0.1\n\nYoneoka Katsuhiro\nMishima, Shizuoka, JAPAN')

    def test_undo_redo_restores_source_and_consensus_as_one_operation(self):
        p,_,_,_,_,rt=test_core.CoreTests().paired();rt.qualities=[25]*len(rt.sequence)
        key=p.key('s1','rbcL');p.assemble(key)
        controller=SimpleNamespace(project=p,view_kind='contig',read_id=None,group_key=key,
            selection=100,anchor=100,offset=80,active_track='F',undo_stack=[],redo_stack=[],
            tree_groups={'group:0':key},tree=Mock(),sync_settings=Mock(),refresh_tree=Mock(),
            refresh_view=Mock(),ensure_visible=Mock(),update_status=Mock(),focus_track=Mock())
        for method in ('history_entry','remember','restore_history','undo','redo'):
            setattr(controller,method,MethodType(getattr(Editor,method),controller))
        entry=controller.history_entry();p.edit_aligned_read(key,'F',[100],'');controller.remember(entry)
        self.assertEqual(p.contigs[key].columns[100].quality,25)
        controller.selection=150;controller.undo()
        self.assertEqual(p.contigs[key].columns[100].quality,40)
        self.assertFalse(p.reads[p.contigs[key].f_id].edits)
        self.assertEqual(controller.selection,100);self.assertEqual(controller.active_track,'F')
        controller.redo();self.assertEqual(p.contigs[key].columns[100].quality,25)
        self.assertEqual(p.reads[p.contigs[key].f_id].edits['100'],'')
        self.assertFalse(p.contigs[key].stale)

    def editing_controller(self,view='contig',track='consensus'):
        p,_,f,_,r,rt=test_core.CoreTests().paired();rt.qualities=[25]*len(rt.sequence)
        key=p.key('s1','rbcL');p.assemble(key)
        c=SimpleNamespace(project=p,view_kind=view,read_id=(r.id if track=='R' else f.id),group_key=key,
            selection=100,anchor=100,offset=80,active_track=track,undo_stack=[],redo_stack=[],
            tree_groups={'group:0':key},tree=Mock(),sync_settings=Mock(),refresh_tree=Mock(),
            ensure_visible=Mock(),update_status=Mock(),focus_track=Mock(),
            sequence_canvas=Mock(),f_pane=SimpleNamespace(canvas=Mock(),track='F'),r_pane=SimpleNamespace(canvas=Mock(),track='R'),
            focus_get=Mock(return_value=None),status=Mock(),schedule_draw=Mock(),update_title=Mock())
        c.confirm_action=Mock(return_value=True)
        c.trace_panes=[c.f_pane,c.r_pane];c.read_sort_reverse=None
        def refresh():
            co=p.contigs[key]
            c.f_pane.mapping=([co.f_bases[col.f] if col.f is not None else None for col in co.columns]
                if c.view_kind=='contig' else bases_for_read(p.reads[c.read_id],p.traces[c.read_id],raw_view=True))
            c.r_pane.mapping=[co.r_bases[col.r] if col.r is not None else None for col in co.columns]
        c.refresh_view=refresh
        for method in ('history_entry','remember','restore_history','undo','redo','length','editing_track',
                       'deletion_positions','delete_base','edit_source_bases','edit_base','arrow','pane_for_track',
                       'edit_history_entry','resolve_read','is_marked','mark_reviewed','review_selection'):
            setattr(c,method,MethodType(getattr(Editor,method),c))
        refresh();return c

    def test_repeated_backspace_and_each_undo_redo_in_all_five_views(self):
        for view,track in (('contig','consensus'),('contig','F'),('contig','R'),('read','F'),('read','R')):
            with self.subTest(view=view,track=track):
                c=self.editing_controller(view,track);p=c.project
                original=p.snapshot()
                for expected in (100,99,98):
                    c.delete_base();self.assertEqual(c.selection,expected)
                self.assertEqual(len(c.undo_stack),3)
                if view=='contig' and track=='consensus':
                    self.assertEqual(p.contigs[c.group_key].sequence().__len__(),277)
                else:
                    rid=c.read_id if view=='read' else (p.contigs[c.group_key].f_id if track=='F' else p.contigs[c.group_key].r_id)
                    self.assertEqual(sum(v=='' for v in p.reads[rid].edits.values()),3)
                    self.assertFalse(p.contigs[c.group_key].stale)
                after=p.snapshot()
                for remaining in (2,1,0):
                    c.undo();self.assertEqual(len(c.undo_stack),remaining)
                self.assertEqual(p.snapshot(),original)
                for remaining in (2,1,0):
                    c.redo();self.assertEqual(len(c.redo_stack),remaining)
                self.assertEqual(p.snapshot(),after)

    def test_range_then_repeated_delete_and_boundary_noop(self):
        c=self.editing_controller();c.anchor=98;c.delete_base()
        self.assertEqual(set(c.project.contigs[c.group_key].overrides),{'98','99','100'})
        self.assertEqual(len(c.undo_stack),1)
        c.delete_base();self.assertEqual(c.selection,97)
        c.selection=c.anchor=0;c.delete_base();count=len(c.undo_stack)
        c.delete_base();self.assertEqual(len(c.undo_stack),count)

    def test_delayed_tree_refresh_selection_preserves_edit_cursor_and_track(self):
        for view,track in (('contig','R'),('read','F')):
            with self.subTest(view=view):
                c=self.editing_controller(view,track);c._ignore_tree=False
                c.tree.selection.return_value=['group:0' if view=='contig' else c.read_id]
                c.show_read=Mock();c.show_contig=Mock()
                Editor.tree_select(c)
                c.show_read.assert_not_called();c.show_contig.assert_not_called()
                self.assertEqual(c.selection,100);self.assertEqual(c.active_track,track)

    def test_tree_selection_does_not_steal_focus_from_multi_select(self):
        c=self.editing_controller('read','F');c._ignore_tree=False
        other=next(rid for rid in c.project.reads if rid!=c.read_id)
        c.tree.selection.return_value=[c.read_id,other];c.tree.focus.return_value=other
        c.focus_get.return_value=c.tree;c.show_read=Mock()
        Editor.tree_select(c)
        c.show_read.assert_called_once_with(other);c.tree.focus_set.assert_called_once()

    def test_repeated_delete_and_undo_on_third_source(self):
        c=self.editing_controller();p,_,ids=test_assembly.SelectedAssemblyTests().multiple()
        key=p.assemble_reads(ids,'Three');c.project=p;c.group_key=key
        c.trace_panes.append(SimpleNamespace(canvas=Mock(),track='source:2'))
        c.active_track='source:2';source=p.contigs[key].sources[2]
        c.selection=c.anchor=next(i+5 for i,b in enumerate(source.mapping) if b is not None)
        def refresh():
            for pane,s in zip(c.trace_panes,p.contigs[key].sources):pane.mapping=s.mapping
        c.refresh_view=refresh;refresh();before=p.snapshot()
        for _ in range(3):c.delete_base()
        self.assertEqual(len(p.reads[source.read_id].edits),3);self.assertFalse(p.contigs[key].stale)
        for _ in range(3):c.undo()
        self.assertEqual(p.snapshot(),before)

    def test_reverse_read_typed_base_is_stored_in_original_orientation(self):
        c=self.editing_controller('read','R');read=c.project.reads[c.read_id];read.reverse=True
        c.f_pane.mapping=list(reversed(c.f_pane.mapping))
        raw=c.f_pane.mapping[c.selection].raw
        c.edit_base('A');self.assertEqual(read.edits[str(raw)],'T')

    def test_auto_assembly_uses_original_filename_prefix_not_read_name(self):
        c=self.editing_controller();p=c.project
        p.contigs={}
        for index,read in enumerate(p.reads.values()):
            read.path=f'/SpecA_arbitrary_{index}.ab1';read.name=f'NOT_THE_PREFIX_{index}'
        c.configure=Mock();c.update_idletasks=Mock();c.confirm_rebuild=Mock(return_value=True);c.show_contig=Mock()
        with patch('app.messagebox.showinfo'):
            Editor.build_auto(c)
        self.assertEqual(len(p.contigs),1)
        co=p.contigs[p.key('SpecA','auto')]
        self.assertEqual(co.sequence_name,'SpecA');self.assertEqual(len(co.sources),2)
        self.assertEqual(len(c.undo_stack),1)

    def test_ab1_header_double_click_alternates_sort(self):
        c=SimpleNamespace(tree=Mock(),read_sort_reverse=None,refresh_tree=Mock())
        c.tree.identify_row.return_value='section:ab1'
        event=SimpleNamespace(x=15,y=50)
        Editor.tree_double_click(c,event);self.assertIs(c.read_sort_reverse,False)
        Editor.tree_double_click(c,event);self.assertIs(c.read_sort_reverse,True)

    def test_shift_arrows_extend_and_contract_read_selection(self):
        tree=Mock();tree.get_children.return_value=['a','b','c'];tree.focus.return_value='a'
        c=SimpleNamespace(tree=tree)
        Editor.extend_read_selection(c,1);tree.selection_set.assert_called_with(['a','b'])
        tree.focus.return_value='b';Editor.extend_read_selection(c,1)
        tree.selection_set.assert_called_with(['a','b','c'])
        tree.focus.return_value='c';Editor.extend_read_selection(c,-1)
        tree.selection_set.assert_called_with(['a','b'])

    def test_focus_track_restores_native_keyboard_focus(self):
        canvas=Mock();c=SimpleNamespace(active_track='R',pane_for_track=Mock(return_value=SimpleNamespace(canvas=canvas)),focus_force=Mock())
        Editor.focus_track(c);c.focus_force.assert_called_once();canvas.focus_set.assert_called_once()

    def test_right_click_preserves_existing_multiple_selection(self):
        p,_,f,_,r,_=test_core.CoreTests().paired();tree=Mock()
        tree.identify_row.return_value=r.id;tree.selection.return_value=[f.id,r.id]
        c=SimpleNamespace(tree=tree,project=p,tree_groups={},build_manual=Mock(),rename_selected=Mock(),
            delete_sequences=Mock(),selected_reads=lambda:[f.id,r.id],resolve_read=lambda i:i,
            copy_selected_sequences=Mock(),copy_fasta=Mock(),export_selected=Mock())
        with patch('app.tk.Menu') as menu:
            self.assertEqual(Editor.tree_context_menu(c,SimpleNamespace(y=30,x_root=100,y_root=100)),'break')
        tree.selection_set.assert_not_called()
        entries=menu.return_value.add_command.call_args_list
        self.assertEqual([call.kwargs['label'] for call in entries],['Build Contig (manual)','Return Reads to AB1','Rename…','Copy Sequence','Copy FASTA','Export FASTA…','Delete'])
        self.assertEqual(entries[0].kwargs['state'],'normal');self.assertEqual(entries[1].kwargs['state'],'disabled')
        self.assertEqual(entries[2].kwargs['state'],'disabled')

    def test_context_delete_and_undo_restore_model_as_one_operation(self):
        c=self.editing_controller();p=c.project;before=p.snapshot()
        c.tree.selection.return_value=list(p.reads);c.refresh_view=Mock()
        Editor.delete_sequences(c)
        self.assertEqual(c.confirm_action.call_args.args[0],'delete_sequences')
        self.assertFalse(p.reads);self.assertFalse(p.contigs);self.assertEqual(len(c.undo_stack),1)
        c.undo();self.assertEqual(p.snapshot(),before)

    def test_keyboard_context_menu_uses_the_same_selected_item_handler(self):
        tree=Mock();tree.focus.return_value='read';tree.bbox.return_value=(20,60,200,20)
        tree.winfo_rootx.return_value=10;tree.winfo_rooty.return_value=30
        c=SimpleNamespace(tree=tree,update_idletasks=Mock(),tree_context_menu=Mock(return_value='break'))
        self.assertEqual(Editor.tree_keyboard_context_menu(c),'break')
        event=c.tree_context_menu.call_args.args[0]
        self.assertEqual((event.y,event.x_root,event.y_root),(70,150,100))

    def test_flat_sidebar_lengths_follow_edits_and_undo(self):
        c=self.editing_controller('read','F');p=c.project;rid=c.read_id
        p.reads[rid].start=10;p.reads[rid].end=190;p.invalidate()
        p.assemble(c.group_key);c.refresh_view()
        c.after_idle=lambda f:f();c.tree.selection.return_value=[];c.tree.get_children.return_value=[]
        Editor.refresh_tree(c)
        row=next(call.kwargs for call in c.tree.insert.call_args_list if call.kwargs['iid'].endswith(':read:'+rid))
        self.assertEqual(row['values'],(180,))
        self.assertFalse(any(call.kwargs['iid']==rid for call in c.tree.insert.call_args_list))
        c.delete_base();c.tree.insert.reset_mock();Editor.refresh_tree(c)
        row=next(call.kwargs for call in c.tree.insert.call_args_list if call.kwargs['iid'].endswith(':read:'+rid))
        self.assertEqual(row['values'],(179,))
        c.undo();c.tree.insert.reset_mock();Editor.refresh_tree(c)
        row=next(call.kwargs for call in c.tree.insert.call_args_list if call.kwargs['iid'].endswith(':read:'+rid))
        self.assertEqual(row['values'],(180,))

    def test_focused_source_is_used_even_if_previous_track_is_consensus(self):
        c=self.editing_controller();c.focus_get.return_value=c.f_pane.canvas
        c.delete_base()
        co=c.project.contigs[c.group_key]
        self.assertEqual(co.columns[100].quality,25)
        self.assertNotIn('100',co.overrides)

    def test_standalone_read_delete_recalculates_support_and_undo_restores_it(self):
        c=self.editing_controller('read','F');co=c.project.contigs[c.group_key]
        self.assertEqual(co.columns[100].quality,40)
        c.delete_base();self.assertEqual(co.columns[100].quality,25)
        c.undo();self.assertEqual(c.project.contigs[c.group_key].columns[100].quality,40)

    def test_typing_into_deleted_cell_restores_base_and_is_one_undo(self):
        for track in ('consensus','F','R'):
            with self.subTest(track=track):
                c=self.editing_controller(track=track);c.delete_base();state=c.project.snapshot()
                c.edit_base('A');self.assertEqual(c.selection,101)
                self.assertEqual(len(c.undo_stack),2)
                c.undo();self.assertEqual(c.project.snapshot(),state)

    def test_native_drop_tcl_list_preserves_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory(prefix='drop files ') as temp:
            paths=[Path(temp)/'read one.ab1',Path(temp)/'リード二.AB1']
            for path in paths:path.touch()
            pending=[];accept=Mock()
            controller=SimpleNamespace(tk=tk.Tcl(),status=Mock(),after_idle=pending.append,accept_paths=accept)
            data=' '.join('{'+str(p)+'}' for p in paths)
            self.assertEqual(Editor.on_drop(controller,SimpleNamespace(data=data)),COPY)
            accept.assert_not_called();self.assertEqual(len(pending),1)
            pending[0]();self.assertEqual(list(accept.call_args.args[0]),list(map(str,paths)))

    def test_mixed_drop_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as temp:
            a=Path(temp)/'one.ab1';b=Path(temp)/'session.sanger.json'
            a.touch();b.touch();pending=[]
            controller=SimpleNamespace(tk=tk.Tcl(),status=Mock(),after_idle=pending.append,accept_paths=Mock())
            event=SimpleNamespace(data=f'{{{a}}} {{{b}}}')
            self.assertEqual(Editor.on_drop(controller,event),REFUSE_DROP)
            self.assertEqual(len(pending),1);controller.accept_paths.assert_not_called()
            with patch('app.messagebox.showerror') as show:
                pending[0]();show.assert_called_once()

    def test_double_click_routes_to_exact_row(self):
        tree=Mock();tree.identify_row.return_value='read-id'
        controller=SimpleNamespace(tree=tree,tree_select=Mock(),rename_selected=Mock())
        self.assertEqual(Editor.rename_from_tree(controller,SimpleNamespace(y=120)),'break')
        tree.selection_set.assert_called_once_with('read-id')
        controller.rename_selected.assert_called_once_with('read-id')

    def test_zoom_and_amplitude_are_independent(self):
        controller=SimpleNamespace(cell=19,trace_gain=1.,spacing_label=Mock(),height_label=Mock(),schedule_draw=Mock())
        Editor.change_zoom(controller,'28');self.assertEqual(controller.cell,28);self.assertEqual(controller.trace_gain,1.)
        Editor.change_trace_height(controller,'0.5');self.assertEqual(controller.cell,28);self.assertEqual(controller.trace_gain,.5)
        controller.height_label.config.assert_called_once_with(text='50%')

    def test_renamed_sequence_is_used_by_fasta_export(self):
        with test_core.demo_project() as (p,key,_):
            p.rename_contig(key,'FASTA_contig_name')
            controller=SimpleNamespace(view_kind='contig',group_key=key,project=p)
            name,sequence=Editor.current_fasta(controller)
            self.assertEqual(name,'FASTA_contig_name');self.assertEqual(sequence,p.contigs[key].sequence())
            rid=p.contigs[key].f_id;p.rename_read(rid,'FASTA_read_name')
            controller.view_kind='read';controller.read_id=rid
            self.assertEqual(Editor.current_fasta(controller)[0],'FASTA_read_name')


if __name__=='__main__':unittest.main()
