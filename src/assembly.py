"""Selected-read assembly without filename/direction assumptions.

Greedy overlap assembly, not a genome assembler. Every added read must pass
overlap and identity thresholds. Conflicts and internal gaps remain review flags.
"""
from Bio import Align


def reverse_bases(bases):
    from core import Base, COMPLEMENT
    return [Base(COMPLEMENT.get(b.base,b.base),b.raw,b.quality,b.manual) for b in reversed(bases)]


def orientation_match(reference,sequence,settings):
    """Return reverse/forward only when a qualifying, unambiguous overlap exists."""
    from Bio.Seq import Seq
    if not reference or not sequence:return None
    aligner=Align.PairwiseAligner();aligner.mode='global'
    aligner.match_score=2;aligner.mismatch_score=-4
    aligner.open_gap_score=-10;aligner.extend_gap_score=-1;aligner.end_gap_score=0
    candidates=[]
    for reverse,query in ((False,sequence),(True,str(Seq(sequence).reverse_complement()))):
        alignment=aligner.align(reference,query)[0]
        pairs=[(i,j) for i,j in alignment.indices.T if i>=0 and j>=0 and reference[i] in 'ACGT' and query[j] in 'ACGT']
        overlap=len(pairs);identity=sum(reference[i]==query[j] for i,j in pairs)/max(1,overlap)
        if overlap>=settings['min_overlap'] and identity>=settings['min_identity']:
            candidates.append((alignment.score,reverse))
    candidates.sort(reverse=True)
    if not candidates:return None
    # Repeats/palindromes can make both directions plausible: do not guess.
    if len(candidates)>1 and abs(candidates[0][0]-candidates[1][0])<max(4,abs(candidates[0][0])*.02):return None
    return candidates[0][1]


def read_bases(project,rid,reverse=False):
    import copy
    from core import bases_for_read
    read=copy.copy(project.reads[rid]);read.direction='F'
    bases=bases_for_read(read,project.traces[rid])
    return reverse_bases(bases) if reverse else bases


def guide_for_sources(sources):
    result=[]
    for i in range(len(sources[0].mapping)):
        bases=[s.mapping[i] for s in sources if s.mapping[i] is not None and s.mapping[i].base in 'ACGT']
        result.append(max(bases,key=lambda b:b.quality if b.quality is not None else -1).base if bases else 'N')
    return ''.join(result)


def recall(sources,pos,minq):
    bases=[];internal_gap=False
    for source in sources:
        b=source.mapping[pos]
        if b is None:
            covered=[i for i,x in enumerate(source.mapping) if x is not None]
            internal_gap |= bool(covered and covered[0]<pos<covered[-1])
        elif b.base not in ('','-'):bases.append(b)
    if not bases:return 'N','no_support',None
    if internal_gap:return 'N','indel',None
    if any(b.manual for b in bases):
        calls={b.base for b in bases}
        return (next(iter(calls)),'manual_read',None) if len(calls)==1 else ('N','manual_read_conflict',None)
    usable=[b for b in bases if b.base in 'ACGT' and b.quality is not None and b.quality>=minq]
    calls={b.base for b in usable}
    if not calls:return 'N','low_quality',None
    if len(calls)>1:return 'N','conflict',None
    call=next(iter(calls));q=max(b.quality for b in usable if b.base==call)
    reason=('single' if len(bases)==1 else 'agree') if all(b.base==call for b in bases) else 'quality'
    return call,reason,q


def assemble_sources(project,ids):
    from core import Source
    ids=list(dict.fromkeys(ids))
    if not ids or any(rid not in project.reads for rid in ids):raise ValueError('Select AB1 reads to assemble.')
    raw={rid:read_bases(project,rid) for rid in ids}
    if any(not bases for bases in raw.values()):raise ValueError('A selected read has no retained bases after trimming.')
    # Anchor the longest read; use its displayed orientation. Other reads are
    # oriented by sequence overlap, never by their filename or guessed F/R tag.
    anchor=max(ids,key=lambda rid:len(raw[rid]))
    reverse=project.reads[anchor].reverse
    sources=[Source(anchor,reverse,reverse_bases(raw[anchor]) if reverse else raw[anchor])]
    remaining=[rid for rid in ids if rid!=anchor]
    aligner=Align.PairwiseAligner();aligner.mode='global'
    aligner.match_score=2;aligner.mismatch_score=-4
    aligner.open_gap_score=-10;aligner.extend_gap_score=-1;aligner.end_gap_score=0
    overlaps=[];identities=[]
    while remaining:
        guide=guide_for_sources(sources);best=None
        for rid in remaining:
            for reverse in (False,True):
                bases=reverse_bases(raw[rid]) if reverse else raw[rid]
                alignment=aligner.align(guide,''.join(b.base for b in bases))[0]
                indices=[(int(i) if i>=0 else None,int(j) if j>=0 else None) for i,j in alignment.indices.T]
                compared=[(i,j) for i,j in indices if i is not None and j is not None and guide[i] in 'ACGT' and bases[j].base in 'ACGT']
                overlap=len(compared)
                identity=sum(guide[i]==bases[j].base for i,j in compared)/max(1,overlap)
                if overlap<project.settings['min_overlap'] or identity<project.settings['min_identity']:continue
                rank=(alignment.score,identity,overlap)
                if best is None or rank>best[0]:best=(rank,rid,reverse,bases,indices,overlap,identity)
        if best is None:
            names=', '.join(project.reads[rid].sequence_name for rid in remaining)
            raise ValueError('No sufficient overlap for: '+names+'. Check the selected locus, cuts and Assembly Settings.')
        _,rid,reverse,bases,indices,overlap,identity=best
        for source in sources:source.mapping=[source.mapping[i] if i is not None else None for i,j in indices]
        sources.append(Source(rid,reverse,[bases[j] if j is not None else None for i,j in indices]))
        remaining.remove(rid);overlaps.append(overlap);identities.append(identity)
    return sources,min(overlaps,default=0),min(identities,default=None)


def sync_legacy(contig):
    """Keep old two-source fields readable for project compatibility."""
    contig.f_id=contig.sources[0].read_id
    contig.r_id=contig.sources[1].read_id if len(contig.sources)>1 else None
    for number,base_attr,col_attr in ((0,'f_bases','f'),(1,'r_bases','r')):
        mapping=contig.sources[number].mapping if number<len(contig.sources) else [None]*len(contig.columns)
        bases=[]
        for col,b in zip(contig.columns,mapping):
            setattr(col,col_attr,len(bases) if b is not None else None)
            if b is not None:bases.append(b)
        setattr(contig,base_attr,bases)
