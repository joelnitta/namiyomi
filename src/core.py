"""namiyomi core. AB1s are immutable; edits belong to a saved project.

Reuses the workflow concepts in the user's sanger_ab1_contig_aligner_v2.1
and scripts/build_marker_fasta.py: ABI/Phred parsing, RC quality reversal,
Mott trimming, quality-aware consensus, and marker FASTA export.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from Bio import Align, SeqIO
from Bio.Seq import Seq

ALPHABET = set('ACGTRYSWKMBDHVN')
AMBIGUOUS = ALPHABET - set('ACGT')
COMPLEMENT = dict(zip('ACGTRYSWKMBDHVN', 'TGCAYRSWMKVHDBN'))
PRIMER_DIRECTIONS = {'af3':'F', 'cr3':'R', '1610f':'F', 'rrfq':'R', '1ir1':'F', 'f':'R'}


def classify_import_paths(paths):
    """Accept AB1s (ignoring companion FASTA) or exactly one project."""
    paths=list(dict.fromkeys(Path(p).resolve() for p in paths))
    if not paths:raise ValueError('No files were provided.')
    if any(not p.is_file() for p in paths):raise ValueError('Drop files, not folders.')
    ab1=[p for p in paths if p.suffix.lower()=='.ab1']
    if ab1 and all(p.suffix.lower() in ('.ab1','.fasta','.fa','.fas','.fna') for p in paths):return 'ab1',ab1
    if len(paths)==1 and paths[0].suffix.lower()=='.json':return 'project',paths
    raise ValueError('Drop AB1 files or one project (.sanger.json). Do not mix file types.')


def atomic_text(path: Path, text: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def base_text(value: str, *, empty=False):
    value = value.upper().strip()
    if (not value and not empty) or any(c not in ALPHABET for c in value):
        raise ValueError('Use DNA/IUPAC bases: A C G T R Y S W K M B D H V N.')
    return value


def guess_name(path: Path):
    stem = re.sub(r'-PREMIX$', '', path.stem, flags=re.I)
    match = re.fullmatch(r'(.+?)_(rbcL|matK|trnL[-_]?F)_(.+)', stem, flags=re.I)
    if match:
        sample, marker, primer = match.groups()
        marker = {'rbcl':'rbcL', 'matk':'matK'}.get(marker.lower(), 'trnL-F')
        direction = PRIMER_DIRECTIONS.get(primer.lower(), '')
        return sample, marker, direction
    match = re.fullmatch(r'(.+)_(F|R)', stem, flags=re.I)
    if match:
        return match[1], 'marker', match[2].upper()
    return stem, 'marker', ''


@dataclass
class Trace:
    path: str
    sequence: str
    qualities: list[int | None]
    peaks: list[int]
    channels: dict[str, list[int]]
    sha256: str

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        rec = SeqIO.read(path, 'abi')
        raw = rec.annotations.get('abif_raw', {})
        sequence = str(rec.seq).upper()
        quality = list(rec.letter_annotations.get('phred_quality', []))
        if not quality:
            quality = [None] * len(sequence)
        if len(quality) != len(sequence):
            raise ValueError(f'{path.name}: Base and quality counts do not match.')
        peaks = list(raw.get('PLOC2', raw.get('PLOC1', [])))
        order = raw.get('FWO_1', b'')
        order = order.decode('ascii') if isinstance(order, bytes) else str(order)
        if len(peaks) != len(sequence) or len(order) != 4 or set(order) != set('ACGT'):
            raise ValueError(f'{path.name}: Required trace channels or peak positions are missing.')
        channels = {b:list(raw.get(f'DATA{i+9}', [])) for i,b in enumerate(order)}
        if not all(channels.values()) or any(x<0 or x>=min(map(len, channels.values())) for x in peaks):
            raise ValueError(f'{path.name}: Trace data are missing or invalid.')
        return cls(str(path), sequence, quality, peaks, channels,
                   hashlib.sha256(path.read_bytes()).hexdigest())


@dataclass
class Read:
    id: str
    path: str
    sha256: str
    sample: str
    marker: str
    direction: str = ''
    start: int = 0  # zero-based inclusive; end is exclusive
    end: int = 0
    edits: dict[str, str] = field(default_factory=dict)
    insertions: dict[str, str] = field(default_factory=dict)
    black_marks: dict[str, bool] = field(default_factory=dict)
    name: str = ''
    reverse: bool = False  # standalone display orientation, not a filename guess

    @property
    def sequence_name(self):
        return self.name or Path(self.path).stem

    @classmethod
    def from_trace(cls, trace):
        sample, marker, direction = guess_name(Path(trace.path))
        return cls(uuid.uuid4().hex, trace.path, trace.sha256, sample, marker,
                   direction, 0, len(trace.sequence))


@dataclass
class Base:
    base: str
    raw: int | None
    quality: int | None
    manual: bool = False


def bases_for_read(read: Read, trace: Trace, *, raw_view=False):
    """Manual calls never inherit a calibrated Phred quality score."""
    result = []
    bounds = range(len(trace.sequence)) if raw_view else range(read.start, read.end)
    for index in bounds:
        for base in read.insertions.get(str(index), ''):
            result.append(Base(base, None, None, True))
        edit = read.edits.get(str(index))
        base = trace.sequence[index] if edit is None else edit
        if base or raw_view:
            result.append(Base(base or '-', index,
                trace.qualities[index] if edit is None else None, edit is not None))
    end = len(trace.sequence) if raw_view else read.end
    for base in read.insertions.get(str(end), ''):
        result.append(Base(base, None, None, True))
    if not raw_view and read.direction == 'R':
        result = [Base(COMPLEMENT.get(b.base, b.base), b.raw, b.quality, b.manual)
                  for b in reversed(result)]
    return result


def mott_bounds(trace: Trace, cutoff=0.05):
    """Maximal segment, as used in the earlier user-specific AB1 pipeline."""
    best = running = 0.
    left = start = end = 0
    for i,(base,q) in enumerate(zip(trace.sequence,trace.qualities)):
        if running <= 0:
            running, left = 0., i
        prob = 10 ** (-q/10) if q is not None and base in 'ACGT' else 1.
        running += cutoff - prob
        if running > best:
            best, start, end = running, left, i+1
    return start,end


@dataclass
class Column:
    f: int | None
    r: int | None
    call: str
    reason: str
    quality: int | None


@dataclass
class Source:
    read_id: str
    reverse: bool
    mapping: list[Base | None]


@dataclass
class ScopedSnapshot:
    """Undo state for affected objects only; raw trace arrays are never copied."""
    reads: dict
    contigs: dict

    @property
    def scope(self):return tuple(self.reads),tuple(self.contigs)


@dataclass
class Contig:
    sample: str
    marker: str
    f_id: str | None
    r_id: str | None
    f_bases: list[Base]
    r_bases: list[Base]
    columns: list[Column]
    signature: str
    overlap: int
    identity: float | None
    status: str
    overrides: dict[str,str] = field(default_factory=dict)
    insertions: dict[str,str] = field(default_factory=dict)
    stale: bool = False
    black_marks: dict[str, bool] = field(default_factory=dict)
    name: str = ''
    sources: list[Source] = field(default_factory=list)
    # Review decisions by column: a base, '' (the column holds no base) or
    # 'N' (reviewed and still unresolved). ai_notes records who decided and why.
    ai_review: dict[str, str] = field(default_factory=dict)
    ai_notes: dict[str, str] = field(default_factory=dict)

    def read_ids(self):
        return [s.read_id for s in self.sources] if self.sources else [rid for rid in (self.f_id,self.r_id) if rid]

    @property
    def sequence_name(self):
        return self.name or f'{self.sample}_{self.marker}'

    def call(self, col):
        key = str(col)
        if key in self.overrides:
            return self.overrides[key]
        reviewed = self.ai_review.get(key)
        # An unresolved review never replaces a call the consensus already made.
        return self.columns[col].call if reviewed in (None, 'N') else reviewed

    def ai_decided(self, col):
        """Whether the review settled this column (a base, or removal of the column)."""
        key = str(col)
        return key not in self.overrides and self.ai_review.get(key, 'N') != 'N'

    def ai_unresolved(self, col):
        key = str(col)
        # Earlier previews also stored N when only local rules had run.
        return (key not in self.overrides and self.ai_review.get(key) == 'N'
                and self.ai_notes.get(key) != 'local rules: not settled'
                and self.black_marks.get(key, True))

    def clear_review(self, col):
        key = str(col)
        self.ai_review.pop(key, None)
        self.ai_notes.pop(key, None)

    def auto_flagged(self, col):
        """Whether the local consensus flagged this column before any overlay."""
        column = self.columns[col]
        return column.call in AMBIGUOUS or column.reason not in ('agree', 'single')

    def _issue_open(self, col, key):
        automatic = (self.call(col) in AMBIGUOUS or
                     (self.auto_flagged(col) and key not in self.overrides
                      and self.ai_review.get(key, 'N') == 'N'))
        return self.black_marks.get(key, automatic)

    def flag_color(self, col):
        """Display open issues and the original flag on an AI replacement."""
        if not 0 <= col < len(self.columns):
            return None
        key = str(col)
        if self.ai_unresolved(col):
            return 'red'
        if self._issue_open(col, key):
            return 'black'
        if self.black_marks.get(key, True) and key not in self.overrides and self.auto_flagged(col):
            return 'black'
        return None

    def sequence(self):
        out=[]
        for i in range(len(self.columns)):
            out.extend(self.insertions.get(str(i), ''))
            out.append(self.call(i))
        out.extend(self.insertions.get(str(len(self.columns)), ''))
        return ''.join(out).replace('-', '')

    def issues(self):
        """Return open issues for review reports and export confirmation."""
        return [i for i in range(len(self.columns)) if self._issue_open(i, str(i))]


def choose_call(f: Base | None, r: Base | None, minq: int, internal_gap: bool):
    if f is None and r is None:
        return 'N','no_support',None
    if f is None or r is None:
        b = f or r
        if internal_gap:
            return 'N','indel',None
        if b.manual:
            return b.base,'manual_read',None
        return (b.base,'single',b.quality) if b.quality is not None and b.quality>=minq and b.base in 'ACGT' else ('N','low_quality',None)
    if f.base == r.base:
        q = max((b.quality for b in (f,r) if b.quality is not None), default=None)
        if f.manual or r.manual:
            return f.base,'manual_read',None
        return (f.base,'agree',q) if q is not None and q>=minq and f.base in 'ACGT' else ('N','low_quality',None)
    if f.manual or r.manual:
        return 'N','manual_read_conflict',None
    fg=f.quality is not None and f.quality>=minq and f.base in 'ACGT'
    rg=r.quality is not None and r.quality>=minq and r.base in 'ACGT'
    if fg and not rg:
        return f.base,'F_quality',f.quality
    if rg and not fg:
        return r.base,'R_quality',r.quality
    return 'N','conflict',None


class Project:
    def __init__(self):
        self.reads: dict[str,Read] = {}
        self.traces: dict[str,Trace] = {}
        self.contigs: dict[str,Contig] = {}
        self.settings = dict(min_quality=20, min_overlap=100, min_identity=.90)
        self.trim_settings = dict(enabled=True,quality=20,min_length=20)
        self.appearance = dict(font_size=12,colors={'A':'#008234','C':'#1559c5','G':'#161616','T':'#d1272c'})
        self.find_settings = dict(identity=100)
        self.path: Path | None = None
        self.dirty=False

    @staticmethod
    def key(sample, marker):
        return json.dumps([sample, marker],ensure_ascii=False)

    def groups(self):
        groups={}
        for read in self.reads.values():
            groups.setdefault(self.key(read.sample,read.marker),[]).append(read.id)
        return groups

    def rename_read(self,rid,name):
        fasta_text([(name,'A')]);self.reads[rid].name=name;self.dirty=True

    def rename_contig(self,key,name):
        fasta_text([(name,'A')])
        if any(k!=key and c.sequence_name==name for k,c in self.contigs.items()):raise ValueError('Another contig already has this name.')
        self.contigs[key].name=name;self.dirty=True

    def next_contig_name(self):
        names={c.sequence_name for c in self.contigs.values()}|{r.sequence_name for r in self.reads.values()}
        index=1
        while f'Contig_{index}' in names:index+=1
        return f'Contig_{index}'

    def fasta_record(self,kind,identifier):
        if kind=='contig':
            co=self.contigs[identifier]
            if co.stale:raise ValueError('Inputs have changed. Rebuild this contig before exporting.')
            return co.sequence_name,co.sequence()
        from assembly import read_bases
        read=self.reads[identifier]
        return read.sequence_name,''.join(b.base for b in read_bases(self,identifier,read.reverse))

    def read_issues(self,rid):
        read=self.reads[rid];trace=self.traces[rid];issues=[]
        for raw in range(read.start,read.end):
            base=read.edits.get(str(raw),trace.sequence[raw])
            if not base:continue
            q=trace.qualities[raw]
            automatic=q is None or q<self.settings['min_quality'] or base in AMBIGUOUS
            if read.black_marks.get(str(raw),automatic):issues.append(raw)
        return issues

    def edit_scope(self,read_ids=(),contig_keys=()):
        ids=set(read_ids);keys=set(contig_keys)
        keys.update(key for key,co in self.contigs.items() if ids.intersection(co.read_ids()))
        return tuple(ids),tuple(keys)

    def snapshot_scope(self,scope):
        ids,keys=scope
        return ScopedSnapshot(copy.deepcopy({i:self.reads[i] for i in ids}),copy.deepcopy({k:self.contigs[k] for k in keys}))

    def remove_sequences(self,read_ids=(),contig_keys=()):
        """Remove project entries only; immutable traces/files remain available to Undo."""
        ids=set(read_ids);keys=set(contig_keys)
        if not ids.issubset(self.reads) or not keys.issubset(self.contigs):raise ValueError('Unknown project sequence.')
        keys.update(key for key,co in self.contigs.items() if ids.intersection(co.read_ids()))
        if not ids and not keys:return set(),set()
        for key in keys:self.contigs.pop(key)
        for rid in ids:self.reads.pop(rid)
        self.invalidate();return ids,keys

    def edit_read_bases(self,rid,positions,value):
        """Edit original-orientation read bases and recall all linked alignments.

        Used by both the standalone read view and the aligned F/R view. This
        deliberately leaves raw signal/Phred immutable and preserves unrelated
        consensus edits. Already stale alignments stay stale until rebuilt.
        """
        if not isinstance(value,str) or (value and (len(value)!=1 or value not in ALPHABET)):
            raise ValueError('Enter one DNA/IUPAC base.')
        read=self.reads[rid];trace=self.traces[rid]
        positions=sorted(set(positions))
        if any(not isinstance(raw,int) or not 0<=raw<len(trace.sequence) for raw in positions):
            raise ValueError('Invalid source read position.')
        changed={raw for raw in positions if read.edits.get(str(raw),trace.sequence[raw])!=value}
        if not changed:return False
        for raw in changed:
            read.edits[str(raw)]=value
            read.black_marks.pop(str(raw),None)
        for co in self.contigs.values():
            if co.sources:
                from assembly import recall, sync_legacy
                affected=set()
                for source in co.sources:
                    if source.read_id!=rid:continue
                    display=COMPLEMENT[value] if source.reverse and value else value
                    for i,b in enumerate(source.mapping):
                        if b is not None and b.raw in changed:
                            source.mapping[i]=Base(display or '-',b.raw,None,True);affected.add(i)
                for pos in affected:
                    col=co.columns[pos]
                    col.call,col.reason,col.quality=recall(co.sources,pos,self.settings['min_quality'])
                    co.overrides.pop(str(pos),None)
                    co.black_marks.pop(str(pos),None)
                    co.clear_review(pos)
                self._call_double_peaks(co, affected)
                sync_legacy(co)
                if not co.stale:co.signature=self.signature(co.read_ids())
                continue
            if rid not in (co.f_id,co.r_id):continue
            direction='F' if rid==co.f_id else 'R'
            bases=co.f_bases if direction=='F' else co.r_bases
            display=COMPLEMENT[value] if direction=='R' and value else value
            affected=set()
            for index,b in enumerate(bases):
                if b.raw in changed:
                    bases[index]=Base(display or '-',b.raw,None,True);affected.add(index)
            recalled = []
            for pos,col in enumerate(co.columns):
                source=col.f if direction=='F' else col.r
                if source not in affected:continue
                f=co.f_bases[col.f] if col.f is not None else None
                r=co.r_bases[col.r] if col.r is not None else None
                removed=any(b is not None and b.manual and b.base=='-' for b in (f,r))
                if f is not None and f.base=='-':f=None
                if r is not None and r.base=='-':r=None
                col.call,col.reason,col.quality=choose_call(f,r,self.settings['min_quality'],col.reason=='indel' and not removed)
                co.overrides.pop(str(pos),None)
                co.black_marks.pop(str(pos),None)
                co.clear_review(pos)
                recalled.append(pos)
            self._call_double_peaks(co, recalled)
            if not co.stale:co.signature=self.signature(self.groups()[self.key(co.sample,co.marker)])
        self.invalidate()
        return True

    def edit_aligned_read(self,key,direction,positions,value):
        """Edit source evidence in the current alignment, then recall affected sites.

        Keep alignment coordinates and unrelated consensus edits stable. A deleted
        source base no longer contributes evidence; it is not a new biological
        indel. With no remaining evidence the consensus becomes N, not a silently
        deleted consensus base. Original trace data/Phred values remain immutable.
        """
        if direction not in ('F','R'):raise ValueError('Select an F or R read.')
        if value and (len(value)!=1 or value not in ALPHABET):raise ValueError('Enter one DNA/IUPAC base.')
        co=self.contigs[key]
        if co.stale:raise ValueError('Inputs have changed. Rebuild this contig before editing.')
        rid=co.f_id if direction=='F' else co.r_id
        if not rid:raise ValueError('No source read at this position.')
        positions=sorted(set(positions));raw_positions=[]
        bases=co.f_bases if direction=='F' else co.r_bases
        for pos in positions:
            if not 0<=pos<len(co.columns):raise ValueError('Invalid alignment position.')
            index=co.columns[pos].f if direction=='F' else co.columns[pos].r
            if index is None:continue
            b=bases[index]
            if b.raw is None:raise ValueError('Use Insert to edit manually inserted source bases.')
            raw_positions.append(b.raw)
        raw_value=COMPLEMENT[value] if direction=='R' and value else value
        return self.edit_read_bases(rid,raw_positions,raw_value)

    def add(self, paths):
        added=[];errors=[]
        known={Path(r.path).resolve() for r in self.reads.values()}
        for path in paths:
            p=Path(path).resolve()
            if p in known:continue
            try:
                trace=Trace.load(p)
                read=Read.from_trace(trace)
                if self.trim_settings['enabled']:
                    read.start,read.end=mott_bounds(trace,10**(-self.trim_settings['quality']/10))
                    if read.end-read.start<self.trim_settings['min_length']:read.start=read.end=0
                self.reads[read.id]=read;self.traces[read.id]=trace
                known.add(p);added.append(read.id)
            except Exception as e:
                errors.append(f'{p.name}: {e}')
        # A newly imported mate (or duplicate direction) changes the assembly
        # inputs just as a trim/edit does; never export the old consensus.
        if added:self.invalidate()
        return added,errors

    def signature(self, ids, *, legacy_paths=False):
        """Identify assembly inputs by content and settings, independent of folder location."""
        rows=[]
        for rid in sorted(ids):
            row=asdict(self.reads[rid])
            for field in ('black_marks', 'name', 'reverse'):
                row.pop(field, None)
            if not legacy_paths:
                del row['path']
            rows.append(row)
        return hashlib.sha256(json.dumps(dict(reads=rows,
            settings=self.settings),sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def invalidate(self):
        for key,contig in self.contigs.items():
            ids=contig.read_ids() if contig.sources else self.groups().get(key,[])
            contig.stale=not ids or contig.signature != self.signature(ids)
        self.dirty=True

    def assemble_reads(self,ids,name,key=None):
        """Transactional selected-read assembly; no changes if assembly fails."""
        from assembly import assemble_sources, recall, sync_legacy
        fasta_text([(name,'A')])
        if any(k!=key and c.sequence_name==name for k,c in self.contigs.items()):raise ValueError('Another contig already has this name. Rename it or choose a different name.')
        ids=list(dict.fromkeys(ids));sources,overlap,identity=assemble_sources(self,ids)
        key=key or self.key(uuid.uuid4().hex,'manual')
        sample,marker=json.loads(key)
        columns=[Column(None,None,*recall(sources,i,self.settings['min_quality'])) for i in range(len(sources[0].mapping))]
        co=Contig(sample,marker,None,None,[],[],columns,self.signature(ids),overlap,identity,
                  'assembled' if len(ids)>1 else 'single_read',name=name,sources=sources)
        self._call_double_peaks(co, range(len(columns)))
        sync_legacy(co);self.contigs[key]=co;self.dirty=True
        self.normalize_orientation(key)
        return key

    def _call_double_peaks(self, contig, positions):
        if self.settings.get('call_double_peaks', False):
            from base_review import double_peak_code
            for pos in positions:
                code = double_peak_code(self, contig, pos)
                if code:
                    column = contig.columns[pos]
                    column.call, column.reason, column.quality = code, 'double_peak', None

    def normalize_orientation(self,key):
        """Match the first sufficiently homologous contig, never an unrelated locus.

        This establishes relative orientation, not a claim of annotated genomic
        forward strand. The first member of each overlap-connected locus is the
        reference; manual Reverse Complement remains available.
        """
        from assembly import orientation_match
        co=self.contigs[key]
        if co.stale:return None
        sequence=co.sequence()
        for other,reference in self.contigs.items():
            if other==key:break
            if reference.stale:continue
            match=orientation_match(reference.sequence(),sequence,self.settings)
            if match is None:continue
            if match:self.reverse_contig(key)
            return reference.sequence_name,match
        return None

    def edit_source(self,key,source_index,positions,value):
        if not isinstance(value,str) or (value and (len(value)!=1 or value not in ALPHABET)):
            raise ValueError('Enter one DNA/IUPAC base.')
        co=self.contigs[key]
        if co.stale:raise ValueError('Inputs have changed. Rebuild the contig first.')
        source=co.sources[source_index];positions=list(positions)
        if any(not 0<=i<len(co.columns) for i in positions):raise ValueError('Invalid alignment position.')
        bases=[source.mapping[i] for i in positions if source.mapping[i] is not None]
        if any(b.raw is None for b in bases):raise ValueError('Use Insert to edit manually inserted bases.')
        raw_value=COMPLEMENT[value] if source.reverse and value else value
        return self.edit_read_bases(source.read_id,[b.raw for b in bases],raw_value)

    def reverse_contig(self,key):
        from assembly import reverse_bases, sync_legacy
        co=self.contigs[key];n=len(co.columns)
        if co.stale:raise ValueError('Rebuild this contig before reversing it.')
        if not co.sources:
            co.sources=[Source(rid,reverse,[bases[getattr(col,attr)] if getattr(col,attr) is not None else None for col in co.columns])
                for rid,reverse,bases,attr in ((co.f_id,False,co.f_bases,'f'),(co.r_id,True,co.r_bases,'r')) if rid]
        for source in co.sources:
            source.reverse=not source.reverse
            source.mapping=[reverse_bases([b])[0] if b is not None else None for b in reversed(source.mapping)]
        co.columns.reverse()
        for col in co.columns:col.call=COMPLEMENT[col.call]
        co.overrides={str(n-1-int(i)):COMPLEMENT.get(value,value) for i,value in co.overrides.items()}
        co.insertions={str(n-int(i)):str(Seq(value).reverse_complement()) for i,value in co.insertions.items()}
        co.black_marks={str(n-1-int(i)):value for i,value in co.black_marks.items()}
        co.ai_review={str(n-1-int(i)):COMPLEMENT.get(value,value) for i,value in co.ai_review.items()}
        co.ai_notes={str(n-1-int(i)):value for i,value in co.ai_notes.items()}
        sync_legacy(co);co.signature=self.signature(co.read_ids());self.dirty=True

    def assemble(self, key):
        ids=self.groups().get(key,[])
        if not ids:raise ValueError('No reads in this group.')
        sample,marker=json.loads(key)
        chosen={}
        for rid in ids:
            read=self.reads[rid]
            if read.direction not in ('F','R'):
                raise ValueError(f'{Path(read.path).name}: Set the read direction to F or R.')
            if read.direction in chosen:
                raise ValueError(f'{sample} / {marker}: Multiple {read.direction} reads. Check sample, region and direction.')
            chosen[read.direction]=rid
        fb=bases_for_read(self.reads[chosen['F']],self.traces[chosen['F']]) if 'F' in chosen else []
        rb=bases_for_read(self.reads[chosen['R']],self.traces[chosen['R']]) if 'R' in chosen else []
        if not fb and not rb:raise ValueError('No retained bases. Check the cut boundaries.')
        if 'F' in chosen and 'R' in chosen and (not fb or not rb):
            raise ValueError('One read has no retained bases. Check the cut boundaries.')
        inds=[];overlap=0;identity=None;status='single_read'
        if fb and rb:
            aligner=Align.PairwiseAligner()
            aligner.mode='global';aligner.match_score=2;aligner.mismatch_score=-4
            aligner.open_gap_score=-10;aligner.extend_gap_score=-1;aligner.end_gap_score=0
            alignment=aligner.align(''.join(x.base for x in fb),''.join(x.base for x in rb))[0]
            inds=[(int(i) if i>=0 else None,int(j) if j>=0 else None) for i,j in alignment.indices.T]
            comparable=[(i,j) for i,j in inds if i is not None and j is not None and fb[i].base in 'ACGT' and rb[j].base in 'ACGT']
            overlap=len(comparable)
            identity=sum(fb[i].base==rb[j].base for i,j in comparable)/max(1,overlap)
            if overlap<self.settings['min_overlap'] or identity<self.settings['min_identity']:
                raise ValueError(f'Assembly stopped: overlap {overlap} bp, identity {identity:.1%}. Check the read pair and cut boundaries.')
            status='assembled'
        else:
            inds=[(i,None) for i in range(len(fb))] if fb else [(None,i) for i in range(len(rb))]
        paired=[k for k,(i,j) in enumerate(inds) if i is not None and j is not None]
        first,last=(paired[0],paired[-1]) if paired else (-1,-1)
        columns=[]
        for k,(i,j) in enumerate(inds):
            call,reason,q=choose_call(fb[i] if i is not None else None,
                rb[j] if j is not None else None,self.settings['min_quality'], first<k<last and (i is None or j is None))
            columns.append(Column(i,j,call,reason,q))
        contig=Contig(sample,marker,chosen.get('F'),chosen.get('R'),fb,rb,columns,
                      self.signature(ids),overlap,identity,status)
        self._call_double_peaks(contig, range(len(columns)))
        if key in self.contigs:contig.name=self.contigs[key].name
        self.contigs[key]=contig;self.dirty=True
        return contig

    def snapshot(self):
        return copy.deepcopy((self.reads,self.contigs,self.settings,self.trim_settings,self.appearance,self.find_settings))

    def restore(self,state):
        if isinstance(state,ScopedSnapshot):
            self.reads.update(copy.deepcopy(state.reads));self.contigs.update(copy.deepcopy(state.contigs))
        else:
            if len(state)==5:state=(*state,dict(identity=100))
            self.reads,self.contigs,self.settings,self.trim_settings,self.appearance,self.find_settings=copy.deepcopy(state)
        self.invalidate()

    def payload(self,directory):
        reads=[]
        for read in self.reads.values():
            row=asdict(read)
            try:
                # Forward slashes let a project and its AB1 folder move between
                # macOS and Windows without embedding OS-specific separators.
                row['path']=Path(os.path.relpath(read.path,directory)).as_posix()
            except ValueError:
                # Windows cannot make a relative path across different drives.
                row['path']=Path(read.path).resolve().as_posix()
            reads.append(row)
        contigs = {key: asdict(contig) for key, contig in self.contigs.items()}
        for row in contigs.values():
            for optional in ('ai_review', 'ai_notes'):
                if not row[optional]:
                    del row[optional]
        return dict(format='sanger-edit-project',version=1,settings=self.settings,trim_settings=self.trim_settings,appearance=self.appearance,find_settings=self.find_settings,
                     reads=reads,contigs=contigs)

    def export_project(self,path):
        """Write a copy without changing the current document path/dirty state."""
        path=self.validate_output_path(path)
        atomic_text(path,json.dumps(self.payload(path.parent),ensure_ascii=False,indent=2))

    def validate_output_path(self,path):
        """Resolve an output destination without permitting source AB1 replacement.

        Keep traces from removed reads protected too: Undo can still restore
        those reads. Existing aliases and hard links to any imported source are
        rejected even when the chosen output filename has a different suffix.
        """
        requested=Path(path)
        output=requested.resolve()
        if requested.suffix.lower()=='.ab1' or output.suffix.lower()=='.ab1':
            raise ValueError('AB1 files are read-only. Choose a JSON or FASTA output filename.')
        sources={Path(trace.path).resolve() for trace in self.traces.values()}
        sources.update(Path(read.path).resolve() for read in self.reads.values())
        for source in sources:
            if output==source:
                raise ValueError('The output would replace a source AB1. Choose another filename.')
            try:
                same_source=output.samefile(source)
            except FileNotFoundError:
                # New output paths cannot already be hard links to a source.
                continue
            except OSError as exc:
                raise ValueError(f'Cannot verify that this output is separate from the source AB1: {exc}') from exc
            if same_source:
                raise ValueError('The output is a link to a source AB1. Choose another filename.')
        return output

    def save(self,path):
        path=self.validate_output_path(path);self.export_project(path)
        self.path=path;self.dirty=False

    @classmethod
    def load(cls,path):
        path=Path(path).resolve()
        raw=json.loads(path.read_text(encoding='utf-8'))
        if raw.get('format')!='sanger-edit-project' or raw.get('version')!=1:
            raise ValueError('Unsupported project format or version.')
        project=cls();project.settings.update(raw['settings'])
        project.trim_settings.update(raw.get('trim_settings',{}));validate_trim_settings(project.trim_settings)
        project.appearance.update(raw.get('appearance',{}));validate_appearance(project.appearance)
        from sequence_find import validate_find_settings
        project.find_settings.update(raw.get('find_settings',{}));validate_find_settings(project.find_settings)
        validate_settings(project.settings)
        double_peaks = project.settings.get('call_double_peaks', False)
        project.settings={k:cast(project.settings[k]) for k,cast in
            [('min_quality',int),('min_overlap',int),('min_identity',float)]}
        if double_peaks:
            project.settings['call_double_peaks'] = True
        for row in raw['reads']:
            row=dict(row);row['path']=str((path.parent/row['path']).resolve())
            read=Read(**row);trace=Trace.load(read.path)
            if trace.sha256!=read.sha256:
                raise ValueError(f'{Path(read.path).name}: The source AB1 has changed since this project was saved.')
            validate_read(read,trace)
            if read.id in project.reads:raise ValueError('Duplicate read ID in project.')
            if read.name:fasta_text([(read.name,'A')])
            project.reads[read.id]=read;project.traces[read.id]=trace
        for key,data in raw.get('contigs',{}).items():
            data=dict(data)
            for k in ['f_bases','r_bases']:data[k]=[Base(**b) for b in data[k]]
            data['columns']=[Column(**c) for c in data['columns']]
            data['sources']=[Source(s['read_id'],bool(s['reverse']),[Base(**b) if b is not None else None for b in s['mapping']]) for s in data.get('sources',[])]
            contig=Contig(**data)
            for source in contig.sources:
                if source.read_id not in project.reads or len(source.mapping)!=len(contig.columns):raise ValueError('Invalid contig source mapping.')
                trace=project.traces[source.read_id]
                for b in source.mapping:
                    if b is not None and b.raw is not None and not 0<=b.raw<len(trace.sequence):raise ValueError('Invalid source coordinate.')
            if contig.name:fasta_text([(contig.name,'A')])
            for i,value in contig.overrides.items():
                if not 0<=int(i)<len(contig.columns) or len(value)>1:raise ValueError('Invalid consensus edit position.')
                base_text(value,empty=True)
            for i, value in contig.ai_review.items():
                if not 0 <= int(i) < len(contig.columns) or value not in ('A', 'C', 'G', 'T', 'N', ''):
                    raise ValueError('Invalid consensus review position or decision.')
            for i, value in contig.ai_notes.items():
                if (i not in contig.ai_review or not isinstance(value, str) or len(value) > 200
                        or re.search(r'[\x00-\x1f]', value)):
                    raise ValueError('Invalid consensus review note.')
            for i,value in contig.insertions.items():
                if not 0<=int(i)<=len(contig.columns):raise ValueError('Invalid consensus insertion position.')
                base_text(value)
            for col in contig.columns:
                base_text(col.call)
                if col.f is not None and not 0<=col.f<len(contig.f_bases):raise ValueError('Invalid forward read position.')
                if col.r is not None and not 0<=col.r<len(contig.r_bases):raise ValueError('Invalid reverse read position.')
            project.contigs[key]=contig
        # Old projects included absolute paths in the hash. Upgrade only hashes
        # that still match, so an outdated assembly is never treated as current.
        for key, contig in project.contigs.items():
            ids = contig.read_ids() if contig.sources else project.groups().get(key, [])
            if ids and contig.signature == project.signature(ids, legacy_paths=True):
                contig.signature = project.signature(ids)
        project.invalidate();project.path=path;project.dirty=False
        return project


def validate_read(read,trace):
    if not read.sample.strip() or not read.marker.strip():raise ValueError('Enter a sample ID and region.')
    if read.direction not in ('','F','R'):raise ValueError('Read direction must be F or R.')
    if not 0<=read.start<=read.end<=len(trace.sequence):raise ValueError('Invalid retained read range.')
    for i,value in read.edits.items():
        if not 0<=int(i)<len(trace.sequence):raise ValueError('Invalid read edit position.')
        if len(value)>1:raise ValueError('Replace one base at a time.')
        base_text(value,empty=True)
    for i,value in read.insertions.items():
        if not 0<=int(i)<=len(trace.sequence):raise ValueError('Invalid read insertion position.')
        base_text(value)


def validate_settings(settings):
    if type(settings.get('call_double_peaks', False)) is not bool:
        raise ValueError('Invalid double peak setting.')
    if not 0<=int(settings['min_quality'])<=93:raise ValueError('Minimum Phred Q must be between 0 and 93.')
    if not 1<=int(settings['min_overlap'])<=10000:raise ValueError('Minimum overlap must be between 1 and 10000 bp.')
    if not math.isfinite(float(settings['min_identity'])) or not 0<float(settings['min_identity'])<=1:
        raise ValueError('Minimum identity must be greater than 0% and at most 100%.')


def validate_trim_settings(settings):
    if type(settings['enabled']) is not bool:raise ValueError('Invalid automatic trimming setting.')
    if not 1<=int(settings['quality'])<=60:raise ValueError('Trimming Phred Q must be between 1 and 60.')
    if not 1<=int(settings['min_length'])<=10000:raise ValueError('Minimum retained length must be between 1 and 10000 bp.')


def validate_appearance(settings):
    if type(settings.get('smooth_trace', False)) is not bool:
        raise ValueError('Invalid chromatogram smoothing setting.')
    if not 8<=int(settings['font_size'])<=20:raise ValueError('Base font size must be between 8 and 20.')
    if set(settings['colors'])!=set('ACGT') or any(not re.fullmatch(r'#[0-9a-fA-F]{6}',v) for v in settings['colors'].values()):
        raise ValueError('Choose valid A, C, G and T colors.')


def fasta_text(records):
    seen=set();lines=[]
    for name,sequence in records:
        if not name or re.search(r'[\s>|\x00-\x1f]',name):
            raise ValueError('Sequence names cannot be empty or contain whitespace, control characters, > or |.')
        if name in seen:raise ValueError(f'Duplicate FASTA identifier: {name}')
        seen.add(name);base_text(sequence)
        lines.append(f'>{name}')
        lines.extend(sequence[i:i+80] for i in range(0,len(sequence),80))
    return '\n'.join(lines)+'\n'


def marker_filename(marker):
    clean=re.sub(r'[^A-Za-z0-9_.-]','_',marker).strip('._')
    if not clean:raise ValueError(f'Invalid region filename: {marker}')
    return clean+'.fasta'


def export_markers(project,path,*,allow_single=False):
    batches={};names={}
    for contig in project.contigs.values():
        if contig.stale:raise ValueError(f'{contig.sample}/{contig.marker}: Rebuild required after input changes.')
        if contig.status in ('single_read','片方向') and not allow_single:
            raise ValueError('Confirm export of single-read sequences.')
        name=marker_filename(contig.marker)
        if name in names and names[name]!=contig.marker:raise ValueError('Region names produce the same output filename.')
        names[name]=contig.marker
        batches.setdefault(name,[]).append((contig.sample,contig.sequence()))
    if not batches:raise ValueError('No contigs to export.')
    # Validate all records before creating any output files.
    payloads={name:fasta_text(rows) for name,rows in batches.items()}
    for name,text in payloads.items():atomic_text(Path(path)/name,text)
    return list(payloads)
