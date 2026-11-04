"""Generate deterministic, entirely synthetic AB1 traces for documentation/tests.

No biological sample, instrument run, personal information or real quality
estimate is represented. ABIF records contain only the tags needed by the viewer.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import random
import struct
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import Project
from Bio.Seq import Seq


def abif_bytes(sequence: str, seed: int, sample: str) -> bytes:
    rng=random.Random(seed);count=len(sequence);peaks=[20+i*12 for i in range(count)]
    samples=peaks[-1]+30
    channels={base:[rng.randrange(0,9) for _ in range(samples)] for base in 'GATC'}
    qualities=[8 if i<12 or i>=count-14 else 32+rng.randrange(15) for i in range(count)]
    for i,base in enumerate(sequence):
        amplitude=rng.randrange(650,1100);sigma=2.0+rng.random()*.5
        for point in range(max(0,peaks[i]-10),min(samples,peaks[i]+11)):
            channels[base][point]+=round(amplitude*math.exp(-.5*((point-peaks[i])/sigma)**2))
        if 230<=i<=233:qualities[i]=16
    rows=[(b'PBAS',2,2,1,sequence.encode('ascii')),
          (b'PCON',2,2,1,bytes(qualities)),
          (b'PLOC',2,4,2,struct.pack('>'+str(count)+'h',*peaks)),
          (b'FWO_',1,2,1,b'GATC'),
          (b'SMPL',1,19,1,(sample+' (synthetic)').encode('ascii')+b'\0')]
    rows.extend((b'DATA',number,4,2,struct.pack('>'+str(samples)+'h',*channels[base]))
                for number,base in enumerate('GATC',9))
    offset=34+28*len(rows);directory=[];payload=[]
    for tag,number,kind,size,data in rows:
        location=int.from_bytes(data.ljust(4,b'\0'),'big') if len(data)<=4 else offset
        directory.append(struct.pack('>4sIHHIIII',tag,number,kind,size,len(data)//size,len(data),location,0))
        if len(data)>4:payload.append(data);offset+=len(data)
    header=b'ABIF'+struct.pack('>H4sIHHIIII',101,b'tdir',1,1023,28,len(rows),28*len(rows),34,0)
    return header+b''.join(directory)+b''.join(payload)


def create_demo(directory: Path):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    names=[f'Demo{number:02}_{direction}.ab1' for number in (1,2) for direction in ('F','R')]
    targets=[directory/name for name in names]+[directory/'Demo.json']
    if any(path.exists() for path in targets):raise FileExistsError('Demo files already exist; use a new output directory.')
    project=Project()
    for number,length,end in ((1,720,520),(2,650,470)):
        rng=random.Random(4700+number);sequence=''.join(rng.choices('ACGT',k=length))
        sequence=sequence[:235]+'AGGGAACGAGA'+sequence[246:]
        pair=(sequence[:end],str(Seq(sequence[length-end:]).reverse_complement()))
        paths=[]
        for index,(direction,bases) in enumerate(zip(('F','R'),pair)):
            if number==1 and direction=='F':
                bases=bases[:260]+next(base for base in 'ACGT' if base!=bases[260])+bases[261:]
            path=directory/f'Demo{number:02}_{direction}.ab1'
            path.write_bytes(abif_bytes(bases,number*10+index,f'Demo{number:02}_{direction}'));paths.append(path)
        added,errors=project.add(paths)
        if errors:raise ValueError('; '.join(errors))
        project.assemble_reads(added,f'Demo{number:02}')
    project.save(directory/'Demo.json')
    return targets


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);args=parser.parse_args()
    for path in create_demo(args.directory):print(path.name)
