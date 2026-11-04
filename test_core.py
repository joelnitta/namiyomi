import copy
from contextlib import contextmanager
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Bio.Seq import Seq
from core import (Base, Project, Read, Trace, base_text, bases_for_read,
                  classify_import_paths, export_markers, fasta_text, guess_name, mott_bounds)
from scripts.create_demo import create_demo


@contextmanager
def demo_project():
    """Keep synthetic AB1 sources available for each integration-test lifetime."""
    with tempfile.TemporaryDirectory(prefix='namiyomi-demo-test-') as directory:
        original=(Path(directory)/'Demo.json').resolve()
        create_demo(original.parent)
        project=Project.load(original)
        key=next(key for key,contig in project.contigs.items() if contig.sequence_name=='Demo01')
        yield project,key,original


def synthetic(project,sequence,direction,sample='s1',marker='rbcL',quality=40):
    rid=f'{sample}_{direction}_{len(project.reads)}'
    trace=Trace(rid+'.ab1',sequence,[quality]*len(sequence),list(range(1,len(sequence)+1)),
                {b:[0]*(len(sequence)+2) for b in 'ACGT'},rid)
    read=Read(rid,trace.path,trace.sha256,sample,marker,direction,0,len(sequence))
    project.reads[rid]=read;project.traces[rid]=trace
    return read,trace


class CoreTests(unittest.TestCase):
    def paired(self):
        p=Project();p.settings['min_overlap']=60
        random.seed(143);seq=''.join(random.choice('ACGT') for _ in range(280))
        f,ft=synthetic(p,seq[:200],'F');r,rt=synthetic(p,str(Seq(seq[80:]).reverse_complement()),'R')
        return p,seq,f,ft,r,rt

    def test_reverse_complement_reverses_quality_and_raw_coordinates(self):
        p=Project();r,t=synthetic(p,'ACGTTA','R');t.qualities=[10,20,30,40,50,60]
        r.start=1;r.end=5
        bs=bases_for_read(r,t)
        self.assertEqual(''.join(b.base for b in bs),'AACG')
        self.assertEqual([b.raw for b in bs],[4,3,2,1])
        self.assertEqual([b.quality for b in bs],[50,40,30,20])

    def test_perfect_overlap_union_and_no_source_change(self):
        p,seq,f,ft,r,rt=self.paired();original=copy.deepcopy((ft,rt))
        c=p.assemble(p.key('s1','rbcL'))
        self.assertEqual(c.sequence(),seq);self.assertEqual(c.overlap,120);self.assertEqual(c.identity,1.)
        self.assertEqual((ft,rt),original)

    def test_high_quality_conflict_is_N_and_black_mark(self):
        p,seq,f,ft,r,rt=self.paired();ft.sequence=ft.sequence[:100]+('A' if seq[100]!='A' else 'T')+ft.sequence[101:]
        c=p.assemble(p.key('s1','rbcL'))
        self.assertEqual(c.call(100),'N');self.assertIn(100,c.issues())
        c.overrides['100']=seq[100]
        self.assertNotIn(100,c.issues())
        c.black_marks['100']=True;self.assertIn(100,c.issues())

    def test_read_edit_does_not_inherit_phred(self):
        p,_,f,ft,_,_=self.paired();f.edits['40']='A';f.insertions['41']='TG'
        bs=bases_for_read(f,ft)
        self.assertIsNone(bs[40].quality);self.assertTrue(bs[40].manual)
        self.assertIsNone(bs[41].raw);self.assertIsNone(bs[41].quality)
        self.assertEqual(ft.qualities[40],40)

    def test_indel_not_silently_filled(self):
        p,_,f,ft,_,_=self.paired();ft.sequence=ft.sequence[:110]+ft.sequence[111:];ft.qualities.pop()
        f.end-=1
        c=p.assemble(p.key('s1','rbcL'))
        self.assertTrue(any(col.reason=='indel' and col.call=='N' for col in c.columns))

    def test_poor_overlap_never_creates_contig(self):
        p=Project();synthetic(p,'A'*200,'F');synthetic(p,'C'*200,'R')
        with self.assertRaises(ValueError):p.assemble(p.key('s1','rbcL'))
        self.assertFalse(p.contigs)

    def test_duplicate_and_unknown_directions_rejected(self):
        p,_,_,_,_,_=self.paired();synthetic(p,'A'*200,'F')
        with self.assertRaisesRegex(ValueError,'Multiple'):p.assemble(p.key('s1','rbcL'))
        p=Project();synthetic(p,'A'*200,'')
        with self.assertRaisesRegex(ValueError,'F or R'):p.assemble(p.key('s1','rbcL'))

    def test_trim_and_stale_consensus_undo(self):
        p,_,f,_,_,_=self.paired();key=p.key('s1','rbcL');p.assemble(key);saved=p.snapshot()
        f.start=20;p.invalidate();self.assertTrue(p.contigs[key].stale)
        p.restore(saved);self.assertFalse(p.contigs[key].stale)
        p.reads[f.id].black_marks['3']=True;p.invalidate();self.assertFalse(p.contigs[key].stale)

    def test_importing_mate_invalidates_single_read_consensus(self):
        p,_,_,_,r,rt=self.paired();p.reads.pop(r.id);p.traces.pop(r.id)
        key=p.key('s1','rbcL');p.assemble(key)
        rt.path=str(Path('s1_rbcL_cr3-PREMIX.ab1').resolve())
        with patch.object(Trace,'load',return_value=rt):
            added,errors=p.add([rt.path])
        self.assertEqual(len(added),1);self.assertFalse(errors)
        self.assertTrue(p.contigs[key].stale)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'Rebuild'):export_markers(p,temp,allow_single=True)

    def test_consensus_backspace_replacement_insertion_export(self):
        p,seq,_,_,_,_=self.paired();c=p.assemble(p.key('s1','rbcL'))
        c.overrides['5']='';self.assertEqual(c.sequence(),seq[:5]+seq[6:])
        c.overrides['5']='N';c.insertions['6']='AC';self.assertEqual(c.sequence(),seq[:5]+'NAC'+seq[6:])
        with tempfile.TemporaryDirectory() as temp:
            files=export_markers(p,temp)
            self.assertEqual(files,['rbcL.fasta'])
            output=Path(temp,'rbcL.fasta').read_text()
            self.assertEqual(''.join(output.splitlines()[1:]),c.sequence())

    def test_fasta_validation_and_marker_collision(self):
        with self.assertRaises(ValueError):fasta_text([('bad name','ACTG')])
        with self.assertRaises(ValueError):fasta_text([('s1','ACTG'),('s1','ACTG')])
        p,_,_,_,_,_=self.paired();key=p.key('s1','rbcL');c=p.assemble(key)
        c.marker='a/b';p.contigs['extra']=copy.deepcopy(c);p.contigs['extra'].marker='a_b'
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):export_markers(p,temp)
            self.assertEqual(list(Path(temp).iterdir()),[])

    def test_mott_all_bad_has_empty_interval(self):
        p=Project();_,t=synthetic(p,'N'*30,'F',quality=0)
        self.assertEqual(mott_bounds(t),(0,0))

    def test_primer_direction_not_alphabetical(self):
        self.assertEqual(guess_name(Path('Demo01_trnLF_f-PREMIX.ab1')),('Demo01','trnL-F','R'))
        self.assertEqual(guess_name(Path('Demo02_trnLF_1Ir1-PREMIX.ab1')),('Demo02','trnL-F','F'))
        self.assertEqual(guess_name(Path('Demo03-PREMIX.ab1')),('Demo03','marker',''))

    def test_rename_does_not_invalidate_or_modify_source(self):
        p,seq,f,ft,_,_=self.paired();key=p.key('s1','rbcL');c=p.assemble(key)
        original=copy.deepcopy(ft);signature=c.signature
        p.rename_read(f.id,'Forward_read');p.rename_contig(key,'Consensus_renamed');p.invalidate()
        self.assertEqual(f.sequence_name,'Forward_read');self.assertEqual(c.sequence_name,'Consensus_renamed')
        self.assertEqual(ft,original);self.assertEqual(c.signature,signature);self.assertFalse(c.stale)
        c2=p.assemble(key)
        self.assertEqual(c2.sequence_name,'Consensus_renamed');self.assertEqual(c2.sequence(),seq)

    def test_rename_validation_is_non_mutating(self):
        p,_,f,_,_,_=self.paired();key=p.key('s1','rbcL');c=p.assemble(key)
        for name in ['', 'two words','bad>name','a\nb']:
            with self.assertRaises(ValueError):p.rename_read(f.id,name)
            with self.assertRaises(ValueError):p.rename_contig(key,name)
        self.assertFalse(f.name);self.assertFalse(c.name)

    def test_source_deletion_recalculates_support_quality(self):
        p,seq,f,ft,r,rt=self.paired();rt.qualities=[25]*len(rt.sequence)
        key=p.key('s1','rbcL');c=p.assemble(key);original=copy.deepcopy((ft,rt))
        self.assertEqual(c.columns[100].quality,40)
        c.overrides['130']='N';c.insertions['140']='AT'
        self.assertTrue(p.edit_aligned_read(key,'F',[100],''))
        self.assertEqual(c.call(100),seq[100]);self.assertEqual(c.columns[100].quality,25)
        self.assertEqual(c.f_bases[c.columns[100].f].base,'-')
        self.assertEqual(f.edits['100'],'');self.assertFalse(c.stale)
        self.assertEqual(c.overrides['130'],'N');self.assertEqual(c.insertions['140'],'AT')
        self.assertEqual((ft,rt),original)

    def test_source_deletion_resolves_conflict_from_remaining_read(self):
        p,seq,f,ft,_,_=self.paired();ft.sequence=ft.sequence[:100]+('A' if seq[100]!='A' else 'T')+ft.sequence[101:]
        key=p.key('s1','rbcL');c=p.assemble(key)
        self.assertEqual(c.call(100),'N');self.assertIn(100,c.issues())
        p.edit_aligned_read(key,'F',[100],'')
        self.assertEqual(c.call(100),seq[100]);self.assertEqual(c.columns[100].quality,40)
        self.assertNotIn(100,c.issues())

    def test_source_deletion_does_not_invent_confidence(self):
        p,_,_,_,_,rt=self.paired();rt.qualities=[10]*len(rt.sequence)
        key=p.key('s1','rbcL');c=p.assemble(key)
        p.edit_aligned_read(key,'F',[100],'')
        self.assertEqual(c.call(100),'N');self.assertIsNone(c.columns[100].quality);self.assertIn(100,c.issues())
        p.edit_aligned_read(key,'R',[100],'')
        self.assertEqual(c.columns[100].reason,'no_support');self.assertEqual(c.call(100),'N')

    def test_aligned_reverse_input_stored_in_original_orientation(self):
        p,_,_,_,r,rt=self.paired();key=p.key('s1','rbcL');c=p.assemble(key)
        raw=c.r_bases[c.columns[100].r].raw;original=copy.deepcopy(rt)
        p.edit_aligned_read(key,'R',[100],'A')
        self.assertEqual(r.edits[str(raw)],'T');self.assertEqual(c.r_bases[c.columns[100].r].base,'A')
        self.assertIsNone(c.r_bases[c.columns[100].r].quality);self.assertEqual(rt,original)

    def test_invalid_source_edit_does_not_partially_mutate(self):
        p,_,_,_,_,_=self.paired();key=p.key('s1','rbcL');p.assemble(key);before=p.snapshot()
        with self.assertRaises(ValueError):p.edit_aligned_read(key,'F',[100,99999],'')
        self.assertEqual(p.snapshot(),before)

    def test_standalone_reverse_edit_recalls_complement_without_altering_raw_trace(self):
        p,_,_,ft,r,rt=self.paired();key=p.key('s1','rbcL');c=p.assemble(key)
        original=copy.deepcopy((ft,rt));raw=c.r_bases[c.columns[100].r].raw
        value='C' if rt.sequence[raw]!='C' else 'A'
        self.assertTrue(p.edit_read_bases(r.id,[raw],value))
        self.assertEqual(c.r_bases[c.columns[100].r].base,str(Seq(value).complement()))
        self.assertEqual(r.edits[str(raw)],value);self.assertFalse(c.stale)
        self.assertEqual((ft,rt),original)

    def test_standalone_edit_validates_all_positions_and_does_not_unstale_contig(self):
        p,_,f,_,_,_=self.paired();key=p.key('s1','rbcL');c=p.assemble(key)
        before=p.snapshot()
        with self.assertRaises(ValueError):p.edit_read_bases(f.id,[100,99999],'')
        self.assertEqual(p.snapshot(),before)
        f.start=5;p.invalidate();self.assertTrue(c.stale)
        p.edit_read_bases(f.id,[100],'');self.assertTrue(c.stale)
        self.assertFalse(p.edit_read_bases(f.id,[100],''))

    def test_source_deletion_saved_project_preserves_recomputed_consensus(self):
        with demo_project() as (p,key,original):
            c=p.contigs[key]
            pos=next(i for i,col in enumerate(c.columns) if col.f is not None and col.r is not None)
            p.edit_aligned_read(key,'R',[pos],'')
            path=original.with_name('edited.json');p.save(path);q=Project.load(path)
            self.assertFalse(q.contigs[key].stale)
            self.assertEqual(q.contigs[key].columns,c.columns)
            self.assertEqual(q.contigs[key].r_bases,c.r_bases)

    def test_drop_classification_spaces_unicode_and_mixed_rejection(self):
        with tempfile.TemporaryDirectory(prefix='Sanger drop ') as temp:
            temp=Path(temp).resolve();a=temp/'sample with spaces.ab1';b=temp/'サンプル.AB1';pr=temp/'session.sanger.json'
            for path in (a,b,pr):path.touch()
            kind,paths=classify_import_paths([str(a),str(b),str(a)])
            self.assertEqual(kind,'ab1');self.assertEqual(paths,[a,b])
            self.assertEqual(classify_import_paths([str(pr)]),('project',[pr]))
            for invalid in ([a,pr],[temp],[temp/'missing.ab1'],[]):
                with self.assertRaises(ValueError):classify_import_paths(invalid)

    def test_old_project_names_roundtrip_and_fasta_headers(self):
        with demo_project() as (p,key,original):
            # Older project records omit optional display names. Exercise that
            # compatibility with generated sources rather than private projects.
            payload=p.payload(original.parent)
            for read in payload['reads']:read.pop('name')
            for contig in payload['contigs'].values():contig.pop('name')
            original.write_text(json.dumps(payload),encoding='utf-8')
            p=Project.load(original);c=p.contigs[key];rid=c.f_id
            self.assertFalse(c.stale)
            p.rename_read(rid,'Read_Demo01_F');p.rename_contig(key,'Sample_Demo01')
            path=original.with_name('renamed.json');p.save(path);q=Project.load(path)
            self.assertEqual(q.reads[rid].sequence_name,'Read_Demo01_F')
            self.assertEqual(q.contigs[key].sequence_name,'Sample_Demo01')
            self.assertFalse(q.contigs[key].stale)
            self.assertTrue(fasta_text([(q.contigs[key].sequence_name,c.sequence())]).startswith('>Sample_Demo01\n'))
            self.assertFalse(Project.load(original).contigs[key].name)

    def test_generated_AB1_project_roundtrip_overwrite_and_integrity(self):
        with demo_project() as (demo,demo_key,original):
            paths=[Path(demo.reads[rid].path) for rid in demo.contigs[demo_key].read_ids()]
            before={path:path.read_bytes() for path in paths}
            p=Project();added,errors=p.add(paths);self.assertEqual(len(added),2);self.assertFalse(errors)
            for r in p.reads.values():
                trace=p.traces[r.id]
                self.assertEqual((r.start,r.end),mott_bounds(trace))
                self.assertGreater(r.start,0);self.assertLess(r.end,len(trace.sequence))
            key=p.assemble_reads(added,'Roundtrip_Demo');c=p.contigs[key]
            c.overrides['8']='N';c.insertions['9']='AT';c.black_marks['10']=True
            path=original.with_name('roundtrip.json');p.save(path);q=Project.load(path)
            self.assertEqual(q.contigs[key].sequence(),c.sequence());self.assertFalse(q.contigs[key].stale)
            q.contigs[key].overrides['9']='';q.save(path);r=Project.load(path)
            self.assertEqual(r.contigs[key].overrides['9'],'')
            self.assertEqual(before,{path:path.read_bytes() for path in paths})
            paths[0].write_bytes(paths[1].read_bytes())
            with self.assertRaisesRegex(ValueError,'has changed'):Project.load(path)


if __name__=='__main__':unittest.main(verbosity=2)
