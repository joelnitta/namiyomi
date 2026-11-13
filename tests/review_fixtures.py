"""Synthetic traces with real peak shapes for the review tests."""
from assembly import recall
from core import Base, Column, COMPLEMENT, Contig, Project, Read, Source, Trace

SPACING = 12
SHAPE = (20, 100, 350, 800, 1000, 800, 350, 100, 20)   # one resolved peak, summit in the middle


def make_trace(project, name, sequence, qualities=None, *, peaks=None, scale=None):
    """Add a read whose every call has an isolated peak in its own channel."""
    count = len(sequence)
    peaks = list(peaks) if peaks is not None else [30 + SPACING * i for i in range(count)]
    channels = {base: [0] * (peaks[-1] + 40) for base in 'ACGT'}
    for index, (base, scan) in enumerate(zip(sequence, peaks)):
        if base not in channels:
            continue
        height = (scale or {}).get(index, 1.0)
        for offset, value in enumerate(SHAPE, -(len(SHAPE) // 2)):
            channels[base][scan + offset] = max(channels[base][scan + offset], round(value * height))
    qualities = list(qualities) if qualities is not None else [50] * count
    trace = Trace(f'/private/{name}.ab1', sequence, qualities, peaks, channels, f'digest-{name}')
    read = Read(name, trace.path, trace.sha256, 'specimen', 'locus', '', 0, count)
    project.reads[name] = read
    project.traces[name] = trace
    return trace


def add_peak(trace, base, scan, height=1.0):
    for offset, value in enumerate(SHAPE, -(len(SHAPE) // 2)):
        trace.channels[base][scan + offset] = max(trace.channels[base][scan + offset], round(value * height))


def forward_source(trace, name, layout):
    """``layout`` lists, per column, the AB1 index of the call or None for no call."""
    return Source(name, False, [None if raw is None else Base(trace.sequence[raw], raw, trace.qualities[raw])
                                for raw in layout])


def reverse_source(trace, name, layout):
    return Source(name, True, [None if raw is None else Base(COMPLEMENT[trace.sequence[raw]], raw, trace.qualities[raw])
                               for raw in layout])


def make_contig(project, sources, key='contig'):
    columns = [Column(None, None, *recall(sources, i, project.settings['min_quality']))
               for i in range(len(sources[0].mapping))]
    contig = Contig('specimen', 'locus', None, None, [], [], columns,
                    project.signature([source.read_id for source in sources]), 0, None, 'assembled',
                    name='Example', sources=sources)
    project.contigs[key] = contig
    return contig
