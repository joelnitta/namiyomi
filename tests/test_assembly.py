import copy
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Bio.Seq import Seq
from assembly import read_bases
from core import Project, Trace, validate_appearance,validate_trim_settings
from test_core import synthetic, demo_project


class SelectedAssemblyTests(unittest.TestCase):
    def multiple(self):
        p=Project();p.settings['min_overlap']=60
        random.seed(45);sequence=''.join(random.choice('ACGT') for _ in range(500))
        reads=[]
        for index,(start,end,reverse) in enumerate(((0,240,False),(120,380,True),(280,500,False))):
            seq=sequence[start:end]
            read,trace=synthetic(p,str(Seq(seq).reverse_complement()) if reverse else seq,'',sample=f'unrelated_name_{index}')
            read.name=f'anything{index}'
            reads.append(read.id)
        return p,sequence,reads

    def test_three_arbitrarily_named_reads_are_oriented_and_assembled(self):
        p,seq,ids=self.multiple();original=copy.deepcopy(p.traces)
        key=p.assemble_reads(ids,'Manual_1');co=p.contigs[key]
        self.assertIn(co.sequence(),(seq,str(Seq(seq).reverse_complement())))
        self.assertEqual(set(co.read_ids()),set(ids));self.assertEqual(len(co.sources),3)
        self.assertFalse(co.issues());self.assertFalse(co.stale)
        self.assertEqual(p.traces,original)
        self.assertEqual({p.reads[i].sample for i in ids},{f'unrelated_name_{n}' for n in range(3)})

    def test_selected_assembly_failure_leaves_project_unchanged(self):
        p,_,ids=self.multiple();bad,_=synthetic(p,'A'*230,'F')
        before=p.snapshot()
        with self.assertRaisesRegex(ValueError,'No sufficient overlap'):p.assemble_reads(ids+[bad.id],'Bad')
        self.assertEqual(p.snapshot(),before)

    def test_all_sources_edit_and_recall_including_third_source(self):
        p,_,ids=self.multiple();key=p.assemble_reads(ids,'Three');co=p.contigs[key]
        index=2;source=co.sources[index]
        pos=next(i for i,b in enumerate(source.mapping) if b is not None and sum(s.mapping[i] is not None for s in co.sources)>1)
        raw=source.mapping[pos].raw;original=copy.deepcopy(p.traces)
        p.edit_source(key,index,[pos],'')
        self.assertEqual(p.reads[source.read_id].edits[str(raw)],'')
        self.assertEqual(source.mapping[pos].base,'-');self.assertNotEqual(co.call(pos),'N')
        self.assertFalse(co.stale);self.assertEqual(p.traces,original)
        p.edit_source(key,index,[pos],'A')
        self.assertEqual(source.mapping[pos].base,'A')
        self.assertEqual(p.reads[source.read_id].edits[str(raw)],'T' if source.reverse else 'A')

    def test_multi_source_reverse_complement_twice_is_identity(self):
        p,_,ids=self.multiple();key=p.assemble_reads(ids,'Three');co=p.contigs[key]
        co.overrides['20']='C';co.overrides['21']='';co.insertions['22']='AG';co.black_marks['23']=True
        original=copy.deepcopy(co);seq=co.sequence();p.reverse_contig(key)
        self.assertEqual(co.sequence(),str(Seq(seq).reverse_complement()))
        p.reverse_contig(key);self.assertEqual(co,original)

    def test_trimming_on_import_is_quality_based_and_does_not_assemble(self):
        p=Project();t=Trace('/example.ab1','N'*8+'ACGT'*30+'N'*10,[0]*8+[40]*120+[0]*10,
            list(range(138)),{b:[0]*140 for b in 'ACGT'},'sha')
        with tempfile.TemporaryDirectory() as temp,patch.object(Trace,'load',return_value=t):
            path=Path(temp)/'unpredictable_name.ab1';path.touch()
            ids,errors=p.add([path])
        self.assertFalse(errors);r=p.reads[ids[0]]
        self.assertEqual((r.start,r.end),(8,128));self.assertFalse(p.contigs)
        self.assertEqual(len(t.sequence),138)

    def test_bad_short_reads_trim_to_empty_and_never_force_assembly(self):
        p=Project();p.trim_settings['min_length']=20
        t=Trace('/bad.ab1','ACGTA',[40]*5,list(range(5)),{b:[0]*7 for b in 'ACGT'},'sha')
        with tempfile.TemporaryDirectory() as temp,patch.object(Trace,'load',return_value=t):
            path=Path(temp)/'bad.ab1';path.touch();ids,_=p.add([path])
        self.assertEqual((p.reads[ids[0]].start,p.reads[ids[0]].end),(0,0))
        with self.assertRaisesRegex(ValueError,'no retained bases'):p.assemble_reads(ids,'Bad')

    def test_standalone_read_orientation_does_not_invalidate_contig(self):
        p,_,ids=self.multiple();key=p.assemble_reads(ids,'Three')
        p.reads[ids[0]].reverse=True;p.invalidate();self.assertFalse(p.contigs[key].stale)
        original=read_bases(p,ids[0]);reverse=read_bases(p,ids[0],True)
        self.assertEqual(''.join(b.base for b in reverse),str(Seq(''.join(b.base for b in original)).reverse_complement()))

    def test_settings_validation(self):
        for value in (-1,0,100):
            with self.assertRaises(ValueError):validate_trim_settings(dict(enabled=True,quality=value,min_length=20))
        with self.assertRaises(ValueError):validate_appearance(dict(font_size=30,colors={}))

    def test_project_delete_removes_linked_contigs_but_retains_sources_for_undo(self):
        p,_,ids=self.multiple();key=p.assemble_reads(ids,'Three')
        before=p.snapshot();traces=copy.deepcopy(p.traces)
        removed,linked=p.remove_sequences([ids[2]])
        self.assertEqual(removed,{ids[2]});self.assertEqual(linked,{key})
        self.assertNotIn(ids[2],p.reads);self.assertNotIn(key,p.contigs)
        self.assertEqual(p.traces,traces)
        p.restore(before);self.assertEqual(p.snapshot(),before)

    def test_delete_contig_only_keeps_all_reads_and_unknown_target_is_atomic(self):
        p,_,ids=self.multiple();key=p.assemble_reads(ids,'Three');before=p.snapshot()
        with self.assertRaises(ValueError):p.remove_sequences([ids[0],'unknown'])
        self.assertEqual(p.snapshot(),before)
        p.remove_sequences(contig_keys=[key]);self.assertEqual(set(p.reads),set(ids));self.assertFalse(p.contigs)

    def test_generated_pair_assembly_settings_sources_and_orientation_roundtrip(self):
        with demo_project() as (p,demo_key,original):
            ids=p.contigs[demo_key].read_ids()
            key=p.assemble_reads(ids,'Manual_Demo');co=p.contigs[key]
            self.assertGreater(len(co.sequence()),600)
            before=co.sequence();p.reverse_contig(key)
            self.assertEqual(co.sequence(),str(Seq(before).reverse_complement()))
            p.trim_settings['quality']=25;p.appearance['font_size']=15
            path=original.with_name('multi.json');p.save(path);q=Project.load(path)
            self.assertEqual(q.contigs[key],co);self.assertFalse(q.contigs[key].stale)
            self.assertEqual(q.trim_settings,p.trim_settings);self.assertEqual(q.appearance,p.appearance)


if __name__=='__main__':unittest.main()
