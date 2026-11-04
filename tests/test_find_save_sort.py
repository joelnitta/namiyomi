import copy
from datetime import datetime
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace,MethodType
import unittest
from unittest.mock import Mock,patch

from app import Editor,default_filename
from core import Project
from sequence_find import find_matches,consensus_coordinates,ordered_entries,validate_find_settings
import test_interface


class FindTests(unittest.TestCase):
    def test_exact_reverse_overlapping_and_palindromic_matches(self):
        self.assertEqual([(h.start,h.strand) for h in find_matches('TTTAGGGAACGAGATTTTCTCGTTCCCTTT','AGGGAACGAGA')],[(3,'+'),(17,'-')])
        self.assertEqual([h.start for h in find_matches('AAAAA','AAA')],[0,1,2])
        self.assertEqual([(h.start,h.strand) for h in find_matches('CATATG','ATAT')],[(1,'±')])
        self.assertFalse(find_matches('ACG','ACGT'))

    def test_percent_boundary_and_no_gaps(self):
        self.assertFalse(find_matches('AAAAACAAAA','AAAAAAAAAA',100))
        self.assertEqual(find_matches('AAAAACAAAA','AAAAAAAAAA',90)[0].identity,90)
        self.assertFalse(find_matches('AAAAACCAAA','AAAAAAAAAA',90))
        self.assertEqual(find_matches('AAAAACCAAA','AAAAAAAAAA',80)[0].identity,80)
        self.assertFalse(find_matches('AACCC','AAAAA',80))
        self.assertFalse(find_matches('AAGCGTA','AACGTA',100))

    def test_whitespace_case_literal_ambiguity_and_validation(self):
        self.assertEqual(find_matches('aaccg',' aa\ncc '),find_matches('AACCG','AACC'))
        self.assertFalse(find_matches('AAAA','NNNN'))
        self.assertEqual(len(find_matches('NNNN','NNNN')),1)
        for query in ('>seq','ACG-','ACX'):
            with self.assertRaises(ValueError):find_matches('ACGT',query)
        for identity in (79,101,80.0,True,'100'):
            with self.assertRaises(ValueError):validate_find_settings(dict(identity=identity))

    def test_results_match_brute_force_for_all_thresholds(self):
        random.seed(71)
        from core import COMPLEMENT
        for _ in range(15):
            seq=''.join(random.choice('ACGT') for _ in range(70));query=seq[10:21]
            reverse=''.join(COMPLEMENT[b] for b in reversed(query))
            for threshold in range(80,101):
                expected={i for i in range(len(seq)-len(query)+1) if any(
                    sum(a==b for a,b in zip(seq[i:i+len(query)],pattern))*100>=threshold*len(query)
                    for pattern in (query,reverse))}
                self.assertEqual({h.start for h in find_matches(seq,query,threshold)},expected)

    def test_search_uses_edited_ungapped_consensus_with_coordinate_map(self):
        c=test_interface.InterfaceTests().editing_controller();co=c.project.contigs[c.group_key]
        co.overrides['20']='';co.insertions['21']='AGGGAACGAGA';co.insertions[str(len(co.columns))]='AACCGG'
        sequence,mapping=consensus_coordinates(co)
        self.assertEqual(sequence,co.sequence());self.assertEqual(len(mapping),len(sequence))
        hit=next(h for h in find_matches(sequence,'AGGGAACGAGA') if h.strand=='+')
        self.assertEqual(set(mapping[hit.start:hit.end]),{21})
        self.assertNotIn(20,mapping);self.assertEqual(mapping[-1],len(co.columns)-1)

    def test_navigation_refreshes_after_edit_and_undo_without_editing_project(self):
        c=test_interface.InterfaceTests().editing_controller();co=c.project.contigs[c.group_key]
        for i,b in enumerate('AGGGAACGAGA'):co.overrides[str(30+i)]=b
        c.find_query=Mock();c.find_query.get.return_value='AGGGAACGAGA'
        c.find_count=Mock();c.find_job=None;c.find_cache=None;c.find_index=-1;c.find_results=[];c.find_map=()
        for name in ('update_find_results','find_next','search_highlights'):
            setattr(c,name,MethodType(getattr(Editor,name),c))
        before=c.project.snapshot();c.find_next()
        self.assertTrue(c.find_results);hit=c.find_results[c.find_index]
        self.assertEqual(c.selection,c.find_map[hit.start]);self.assertEqual(c.project.snapshot(),before)
        all_columns,active=c.search_highlights();self.assertTrue(set(range(30,41))<=all_columns)
        self.assertTrue(active);c.project.contigs[c.group_key].overrides['30']='T'
        c.search_highlights();self.assertEqual(c.find_index,-1)
        c.project.restore(before);c.search_highlights();self.assertTrue(c.find_results)

    def test_find_settings_roundtrip_default_legacy_and_undo(self):
        p=Project();before=p.snapshot();p.find_settings['identity']=83
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'project.json';p.save(path);q=Project.load(path)
            self.assertEqual(q.find_settings,dict(identity=83))
            raw=json.loads(path.read_text());raw.pop('find_settings');path.write_text(json.dumps(raw))
            self.assertEqual(Project.load(path).find_settings,dict(identity=100))
            raw['find_settings']=dict(identity=1);path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):Project.load(path)
        p.restore(before);self.assertEqual(p.find_settings,dict(identity=100))


class SortSaveTests(unittest.TestCase):
    def test_name_and_numeric_length_in_both_directions_without_mutating_ids(self):
        ids=['c','a','b','stale'];names=dict(c='Zebra',a='apple',b='Banana',stale='missing')
        lengths=dict(c=1000,a=99,b=100,stale=None)
        self.assertEqual(ordered_entries(ids,names,lengths,'name'),['a','b','stale','c'])
        self.assertEqual(ordered_entries(ids,names,lengths,'name',True),['c','stale','b','a'])
        self.assertEqual(ordered_entries(ids,names,lengths,'length'),['a','b','c','stale'])
        self.assertEqual(ordered_entries(ids,names,lengths,'length',True),['c','b','a','stale'])
        self.assertEqual(ids,['c','a','b','stale'])

    def test_headers_toggle_correct_sort_column_and_contigs_section_works(self):
        c=SimpleNamespace(tree=Mock(),refresh_tree=Mock(),sort_column=None,sort_reverse=False)
        c.tree.identify_row.return_value='';c.tree.identify_region.return_value='heading'
        c.tree.identify_column.return_value='#1';event=SimpleNamespace(x=250,y=5)
        Editor.tree_double_click(c,event);self.assertEqual((c.sort_column,c.sort_reverse),('length',False))
        Editor.tree_double_click(c,event);self.assertEqual((c.sort_column,c.sort_reverse),('length',True))
        c.tree.identify_region.return_value='tree';c.tree.identify_row.return_value='section:contigs'
        Editor.tree_double_click(c,event);self.assertEqual((c.sort_column,c.sort_reverse),('name',False))

    def test_tree_sorts_contigs_without_changing_orientation_reference_order(self):
        c=test_interface.InterfaceTests().editing_controller();p=c.project;original=c.group_key
        p.contigs[original].name='Zebra'
        co=copy.deepcopy(p.contigs[original]);co.name='apple';co.columns=co.columns[:80];p.contigs['other']=co
        c.after_idle=lambda callback:callback();c.tree.selection.return_value=[];c.tree.get_children.return_value=[]
        c.sort_column='name';c.sort_reverse=False
        for column,descending,expected in (('name',False,['apple','Zebra']),('name',True,['Zebra','apple']),
                                            ('length',False,['apple','Zebra']),('length',True,['Zebra','apple'])):
            c.sort_column=column;c.sort_reverse=descending;c.tree.insert.reset_mock();Editor.refresh_tree(c)
            rows=[call.kwargs['text'] for call in c.tree.insert.call_args_list if call.args[0]=='section:contigs']
            self.assertEqual(rows,expected);self.assertEqual(list(p.contigs),[original,'other'])

    def test_default_date_names(self):
        today=datetime(2026,11,4)
        self.assertEqual(default_filename('json',today),'20261104_.json')
        self.assertEqual(default_filename('.fasta',today),'20261104_.fasta')

    def test_first_save_adopts_name_then_save_overwrites_without_dialog(self):
        p=Project();c=SimpleNamespace(project=p,focus_track=Mock(),clear_recovery=Mock(),status=Mock(),title=Mock())
        c.update_title=MethodType(Editor.update_title,c)
        with tempfile.TemporaryDirectory() as directory:
            path=(Path(directory)/'20261104_test.json').resolve()
            with patch('app.filedialog.asksaveasfilename',return_value=str(path)) as choose:
                self.assertTrue(Editor.save_project(c));choose.assert_called_once()
                self.assertTrue(choose.call_args.kwargs['initialfile'].endswith('_.json'))
            self.assertEqual(p.path,path);self.assertNotIn('Untitled',c.title.call_args.args[0])
            p.find_settings['identity']=90;p.dirty=True
            with patch('app.filedialog.asksaveasfilename',side_effect=AssertionError('No dialog on overwrite')):
                self.assertTrue(Editor.save_project(c))
            self.assertEqual(Project.load(path).find_settings['identity'],90);self.assertFalse(p.dirty)

    def test_save_as_retargets_but_export_copy_does_not(self):
        p=Project();c=SimpleNamespace(project=p,focus_track=Mock(),clear_recovery=Mock(),status=Mock(),update_title=Mock())
        with tempfile.TemporaryDirectory() as directory:
            one=(Path(directory)/'one.json').resolve();two=(Path(directory)/'two.json').resolve();three=(Path(directory)/'three.json').resolve()
            p.save(one)
            with patch('app.filedialog.asksaveasfilename',return_value=str(two)):
                self.assertTrue(Editor.save_project(c,save_as=True));self.assertEqual(p.path,two)
            p.dirty=True
            with patch('app.filedialog.asksaveasfilename',return_value=str(three)):
                self.assertTrue(Editor.save_project(c,True));self.assertEqual(p.path,two);self.assertTrue(p.dirty)


class NativeMenuTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='darwin' and shutil.which('clang'),'AppKit/clang required')
    def test_optional_actions_hidden_without_removing_or_reindexing_items(self):
        with tempfile.TemporaryDirectory(prefix='namiyomi-menu-test-') as directory:
            executable=Path(directory)/'menu-test'
            subprocess.run(['clang','-fno-modules','-framework','AppKit',str(Path(__file__).parent/'qa/test_native_menus.m'),'-o',str(executable)],check=True,capture_output=True)
            result=subprocess.run([str(executable)],capture_output=True,text=True,check=True)
            self.assertIn('item count and order preserved',result.stdout)


if __name__=='__main__':unittest.main()
