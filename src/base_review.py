"""Detect aligned double peaks for optional IUPAC calls."""

from core import COMPLEMENT

DOUBLE_PEAK_RATIO = 0.65
IUPAC_PAIRS = {'AC': 'M', 'AG': 'R', 'AT': 'W', 'CG': 'S', 'CT': 'Y', 'GT': 'K'}


def _rank_peaks(trace, raw):
    if not 0 < raw < len(trace.peaks) - 1:
        return None
    left, center, right = trace.peaks[raw - 1:raw + 2]
    spacing = min(center - left, right - center)
    if spacing < 6:
        return None
    radius = min(4, spacing // 3)
    window = range(center - radius, center + radius + 1)
    ranked = []
    for base, channel in trace.channels.items():
        top = max(window, key=channel.__getitem__)
        ranked.append((channel[top], base, top))
    ranked.sort(reverse=True)
    flank_height = max(max(channel[left], channel[right]) for channel in trace.channels.values())
    return ranked, center, radius, flank_height


def _centered_peak(trace, peak, center, radius, flank_height):
    height, base, top = peak
    channel = trace.channels[base]
    if height < max(1, flank_height / 4) or abs(top - center) >= radius:
        return False
    # Sampled integer heights can tie across a narrow summit. Reject shoulders
    # and broad/clipped plateaus by requiring a fall on both sides of that summit.
    end = top
    while end < center + radius and channel[end + 1] == height:
        end += 1
    return (end - top < 3 and end < center + radius
            and channel[top - 1] < height and channel[end + 1] < height)


def _peak(trace, raw, reverse):
    """Return the dominant centered peak and its ratio to the next channel."""
    result = _rank_peaks(trace, raw)
    if result is None:
        return None
    ranked, center, radius, flank_height = result
    height, base, _ = ranked[0]
    ratio = height / max(1, ranked[1][0])
    if ratio < 4 or not _centered_peak(trace, ranked[0], center, radius, flank_height):
        return None
    return COMPLEMENT[base] if reverse else base, ratio


def _sources(contig, column):
    if contig.sources:
        return [(s.read_id, s.reverse, s.mapping[column]) for s in contig.sources]
    c = contig.columns[column]
    return [(rid, reverse, bases[index]) for rid, reverse, bases, index in
            ((contig.f_id, False, contig.f_bases, c.f), (contig.r_id, True, contig.r_bases, c.r))
            if rid is not None and index is not None]


def double_peak_code(project, contig, column):
    """Call two co-located channels in one trace, with clean flanking calls."""
    original = contig.columns[column]
    if original.reason in ('indel', 'no_support'):
        return None
    pair_calls, called = set(), set()
    for rid, reverse, base in _sources(contig, column):
        if base is None:
            continue
        if base.manual:
            return None
        if base.quality is not None and base.quality >= project.settings['min_quality'] and base.base in 'ACGT':
            called.add(base.base)
        trace = project.traces.get(rid)
        if trace is None or base.raw is None:
            continue
        raw = base.raw
        result = _rank_peaks(trace, raw)
        if result is None:
            continue
        peaks, center, radius, flank = result
        main, second, third = peaks[:3]
        if (second[0] < main[0] * DOUBLE_PEAK_RATIO or third[0] > main[0] / 4
                or abs(main[2] - second[2]) > 2
                or not all(_centered_peak(trace, peak, center, radius, flank) for peak in (main, second))
                or not all(_peak(trace, neighbor, False) for neighbor in (raw - 1, raw + 1))):
            continue
        bases = ''.join(sorted(COMPLEMENT[peak[1]] if reverse else peak[1] for peak in (main, second)))
        pair_calls.add(bases)
    if len(pair_calls) == 1:
        pair = next(iter(pair_calls))
        if called <= set(pair) and (original.call == 'N' or original.call in pair):
            return IUPAC_PAIRS[pair]
    return None
