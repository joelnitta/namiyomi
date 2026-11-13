"""Find columns that need review and describe their chromatograms as text.

The description is what an online model receives for a column the local rules
in ``trace_evidence`` could not settle: the aligned calls of every read and the
four-channel signal itself, sampled along the scan axis in consensus
orientation. It carries no file names, sample names or project paths.
"""
from dataclasses import dataclass

from core import ALPHABET, COMPLEMENT
from trace_evidence import contig_sources, local_spacing, peak_evidence

CONTEXT_COLUMNS = 6       # aligned calls shown on each side of the column
WINDOW_SPACINGS = 3.0     # signal shown on each side, in base spacings
WIDE_WINDOW_SPACINGS = 5.0    # for length disagreements, so a whole run is usually visible
MAX_SAMPLES = 61          # per channel


@dataclass(frozen=True)
class WaveformSite:
    contig: str
    column: int


def needs_review(contig, column, *, sources=None):
    """An open consensus flag eligible for automatic review."""
    key = str(column)
    original = contig.columns[column]
    if key in contig.overrides or not contig.black_marks.get(key, True):
        return False
    if original.reason in ('double_peak', 'manual_read', 'manual_read_conflict'):
        return False
    if contig.ai_review.get(key, 'N') != 'N':
        return False
    if not contig.auto_flagged(column):
        return False
    if sources is None:
        sources = contig_sources(contig)
    return not any(source.mapping[column] is not None and source.mapping[column].manual
                   for source in sources)


def review_sites(project):
    """Return eligible open flags in current contigs."""
    sites = []
    for key, contig in project.contigs.items():
        if contig.stale:
            continue
        sources = contig_sources(contig)
        sites.extend(WaveformSite(key, column) for column in range(len(contig.columns))
                     if needs_review(contig, column, sources=sources))
    return sites


def _coverage(mapping):
    covered = [i for i, base in enumerate(mapping) if base is not None and base.raw is not None]
    return (covered[0], covered[-1]) if covered else (1, 0)


def _aligned_call(mapping, coverage, column):
    if not 0 <= column < len(mapping):
        return '.'
    base = mapping[column]
    if base is None:
        return '-' if coverage[0] <= column <= coverage[1] else '.'
    label = base.base if base.base in ALPHABET or base.base == '-' else 'N'
    quality = '?' if base.quality is None else str(base.quality)
    return f'{label}{quality}' + ('*' if base.manual else '')


def _centre(trace, mapping, column):
    """Scan that corresponds to the column in this read, and whether a call sits there."""
    base = mapping[column]
    if base is not None and base.raw is not None and base.raw < len(trace.peaks):
        return trace.peaks[base.raw], base.raw, True
    scans = []
    for step in (-1, 1):
        k = column + step
        while 0 <= k < len(mapping) and abs(k - column) <= CONTEXT_COLUMNS:
            near = mapping[k]
            if near is not None and near.raw is not None and near.raw < len(trace.peaks):
                scans.append((trace.peaks[near.raw], near.raw))
                break
            k += step
    if len(scans) < 2:
        return None, None, False
    return round((scans[0][0] + scans[1][0]) / 2), min(scans[0][1], scans[1][1]), False


def _signal_block(trace, source, column, window=WINDOW_SPACINGS):
    """Sampled channels around the column, flipped and complemented for reverse reads."""
    mapping = source.mapping
    centre, raw, called = _centre(trace, mapping, column)
    if centre is None:
        return ['  signal unavailable at this column']
    length = min(len(values) for values in trace.channels.values())
    spacing = local_spacing(trace.peaks, raw)
    reach = round(window * spacing)
    step = max(1, -(-(2 * reach + 1) // MAX_SAMPLES))
    direction = -1 if source.reverse else 1
    offsets = list(range(-(reach // step) * step, reach + 1, step))
    heights = [trace.channels[trace.sequence[k]][trace.peaks[k]]
               for k in range(max(0, raw - 12), min(len(trace.peaks), raw + 13))
               if trace.sequence[k] in trace.channels and 0 <= trace.peaks[k] < length]
    scale = max(1.0, sorted(heights)[len(heights) // 2]) if heights else 1.0

    def value(base, offset):
        scan = centre + direction * offset
        channel = trace.channels[COMPLEMENT[base] if source.reverse else base]
        return round(100 * channel[scan] / scale) if 0 <= scan < length else 0

    calls = []
    for k in range(max(0, column - CONTEXT_COLUMNS), min(len(mapping), column + CONTEXT_COLUMNS + 1)):
        base = mapping[k]
        if base is None or base.raw is None or base.raw >= len(trace.peaks):
            continue
        offset = (trace.peaks[base.raw] - centre) * direction
        if abs(offset) <= reach:
            quality = '?' if base.quality is None else base.quality
            calls.append(f'{base.base}{quality}@{offset:+d}' + ('<-target' if k == column else ''))
    width = max(3, max(len(str(value(base, o))) for base in 'ACGT' for o in offsets))
    lines = [f'  spacing {spacing:.1f} scans per base; calls at x: ' + ' '.join(calls)]
    if not called:
        lines.append('  no call at this column: x=0 is midway between the two flanking calls')
    lines.append('  x ' + ' '.join(f'{o:>{width}d}' for o in offsets))
    for base in 'ACGT':
        lines.append(f'  {base} ' + ' '.join(f'{value(base, o):>{width}d}' for o in offsets))
    return lines


def _measurements(trace, source, column):
    base = source.mapping[column]
    if base is None or base.raw is None or base.manual or base.raw >= len(trace.peaks):
        return None
    if trace.sequence[base.raw] not in trace.channels:
        return 'the AB1 call here is N'
    evidence = peak_evidence(trace, base.raw)
    if evidence is None:
        return None
    summit = 'no summit of its own' if not evidence.has_peak else f'summit {evidence.offset:.2f} spacings from the call'
    return (f'called peak: {summit}, height {round(100 * evidence.relative_height)}, '
            f'tallest co-located peak in another channel {round(100 * evidence.rival_peak)}% of it, '
            f'neighbouring calls at {evidence.left_spacing:.2f} and {evidence.right_spacing:.2f} spacings, '
            f'median Phred of neighbours {evidence.context_quality:.0f}')


def waveform_evidence(project, site):
    """Describe one column for an online model, without file labels or an inferred base."""
    contig = project.contigs[site.contig]
    column = site.column
    if not 0 <= column < len(contig.columns):
        raise ValueError('Review column is outside the contig.')
    sources = contig_sources(contig)
    spans = [_coverage(source.mapping) for source in sources]
    gap = any(span[0] <= column <= span[1] and source.mapping[column] is None
              for source, span in zip(sources, spans))
    window = WIDE_WINDOW_SPACINGS if gap else WINDOW_SPACINGS
    lines = []
    for number, (source, coverage) in enumerate(zip(sources, spans), 1):
        mapping = source.mapping
        if not coverage[0] <= column <= coverage[1]:
            continue    # this read does not reach the column; it is not evidence either way
        context = ' '.join(_aligned_call(mapping, coverage, i)
                           for i in range(column - CONTEXT_COLUMNS, column + CONTEXT_COLUMNS + 1))
        lines.append(f'Read {number} ({"reverse strand, shown reverse-complemented" if source.reverse else "forward strand"})')
        lines.append(f'  aligned calls -{CONTEXT_COLUMNS}..+{CONTEXT_COLUMNS}: {context}')
        trace = project.traces.get(source.read_id)
        base = mapping[column]
        if base is not None and base.manual:
            lines.append('  the call in this column was edited by the user')
        if trace is None:
            lines.append('  signal unavailable')
            continue
        measured = _measurements(trace, source, column)
        if measured:
            lines.append('  ' + measured)
        lines.extend(_signal_block(trace, source, column, window))
    return '\n'.join(lines) if lines else 'No source chromatograms cover this column.'
