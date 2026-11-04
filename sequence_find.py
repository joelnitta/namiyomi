"""Ungapped, literal DNA/IUPAC matching on the edited/exported consensus."""
from dataclasses import dataclass
from core import ALPHABET, COMPLEMENT


@dataclass(frozen=True)
class Match:
    start: int  # zero-based exported consensus coordinate
    end: int
    strand: str
    identity: float  # percentage, not a probability


def validate_find_settings(settings):
    value=settings.get('identity')
    if type(value) is not int or not 80<=value<=100:
        raise ValueError('Minimum identity must be an integer from 80 to 100%.')


def find_matches(sequence,query,identity=100):
    validate_find_settings(dict(identity=identity))
    query=''.join(query.upper().split());sequence=sequence.upper()
    if not query:return []
    if set(query)-ALPHABET:raise ValueError('Enter DNA/IUPAC letters only. Symbols match literally, not as wildcards.')
    size=len(query)
    if size>len(sequence):return []
    reverse=''.join(COMPLEMENT[base] for base in reversed(query))
    limit=(100-identity)*size//100
    found={}
    for strand,pattern in (('+',query),('-',reverse)):
        if limit==0:
            indices=[];start=sequence.find(pattern)
            while start>=0:
                indices.append((start,0));start=sequence.find(pattern,start+1)
        else:
            indices=[]
            for start in range(len(sequence)-size+1):
                differences=0
                for offset,base in enumerate(pattern):
                    differences+=sequence[start+offset]!=base
                    if differences>limit:break
                if differences<=limit:indices.append((start,differences))
        for start,differences in indices:
            key=(start,start+size);percent=100*(size-differences)/size
            old=found.get(key)
            found[key]=Match(*key,'±' if old else strand,max(old.identity,percent) if old else percent)
    return sorted(found.values(),key=lambda hit:(hit.start,hit.end))


def consensus_coordinates(contig):
    """Map every exported base to a display column, including manual insertions."""
    sequence=[];columns=[];length=len(contig.columns)
    for index in range(length+1):
        insertion=contig.insertions.get(str(index),'')
        sequence.extend(insertion);columns.extend([min(index,max(0,length-1))]*len(insertion))
        if index<length:
            call=contig.call(index)
            if call and call!='-':sequence.append(call);columns.append(index)
    return ''.join(sequence),tuple(columns)


def ordered_entries(ids,names,lengths,column=None,descending=False):
    """Sort presentation only; preserve project/reference insertion order."""
    ids=list(ids)
    if column=='name':return sorted(ids,key=lambda item:names[item].casefold(),reverse=descending)
    if column=='length':
        known=[item for item in ids if lengths[item] is not None]
        unknown=[item for item in ids if lengths[item] is None]
        return sorted(known,key=lambda item:(lengths[item],names[item].casefold()),reverse=descending)+unknown
    return ids
