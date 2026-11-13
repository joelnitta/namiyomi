"""Resolve flagged consensus columns from the chromatograms, on this device.

Phred scores describe one base call. A person reading a trace also looks at the
neighbourhood: is this read in a clean, evenly spaced stretch here, is the peak
isolated, do two calls sit on a single peak. The rules below encode those
checks. Each rule was measured against reads that were held out of the
assembly (see docs/TRACE_REVIEW.md and scripts/benchmark_trace_review.py), and
a rule only decides when its measured error rate is no worse than the error
rate of the Phred >= 20 calls the consensus already accepts.

A verdict is one of: a base, ``REMOVE`` (the column holds no base: a spurious
call or an alignment gap that the better read does not support) or
``UNRESOLVED``. Nothing here reads file names or guesses from sequence
patterns; every decision comes from measured signal.
"""
from dataclasses import dataclass
from statistics import median

from core import COMPLEMENT, Source

REMOVE = ''
UNRESOLVED = 'N'

ANCHOR_BASES = 3          # called bases required on each side of the column
ANCHOR_TIERS = (40, 30, 25)   # every one of them at or above the tier's Phred score
ANCHOR_SPACING = (0.6, 1.5)   # peak-to-peak distance as a fraction of the local spacing
CLEAR_HEIGHT = 0.2        # peak height relative to the neighbouring called peaks
CLEAR_OFFSET = 0.35       # distance between the call and its peak summit, in spacings
CLEAR_MAX_SPACING = 1.6
CONTEXT_Q = 40            # median Phred of the neighbouring calls that marks a good stretch
# (tallest co-located peak in another channel, closest neighbouring call in spacings,
#  fall of the signal towards each neighbouring call) for a good and a degraded stretch.
CLEAR_GOOD = (0.5, 0.5, 0.25)
CLEAR_DEGRADED = (0.33, 0.75, 0.4)
RUN_RESOLVED = 0.25       # every valley inside a run of one base falls at least this far
RUN_MERGED = 0.12         # some valley inside the run falls less than this: peaks have merged


@dataclass(frozen=True)
class Verdict:
    call: str
    rule: str
    detail: str = ''

    @property
    def resolved(self):
        return self.call != UNRESOLVED


@dataclass(frozen=True)
class PeakEvidence:
    """Measurements for one base call in the orientation of the AB1 file."""
    base: str
    has_peak: bool
    height: int
    relative_height: float
    offset: float
    rival_at_summit: float
    rival_peak: float
    left_spacing: float
    right_spacing: float
    left_drop: float
    right_drop: float
    spacing: float
    context_quality: float

    @property
    def clear(self):
        """An isolated, centred peak with no co-located peak in another channel.

        A low-quality call inside an otherwise good stretch is judged with
        looser limits than a call in a degraded stretch such as a read tail.
        """
        rival, spacing, drop = CLEAR_GOOD if self.context_quality >= CONTEXT_Q else CLEAR_DEGRADED
        return (self.has_peak and self.offset <= CLEAR_OFFSET
                and self.relative_height >= CLEAR_HEIGHT and self.rival_peak <= rival
                and min(self.left_spacing, self.right_spacing) >= spacing
                and max(self.left_spacing, self.right_spacing) <= CLEAR_MAX_SPACING
                and min(self.left_drop, self.right_drop) >= drop)

    @property
    def isolated(self):
        """A summit of its own, apart from its neighbours, whatever the stretch.

        Looser than ``clear``. On its own it is not enough to settle a call; a
        confident confirmation by the online model is required as well.
        """
        rival, spacing, drop = CLEAR_GOOD
        return (self.has_peak and self.offset <= CLEAR_OFFSET
                and self.relative_height >= CLEAR_HEIGHT and self.rival_peak <= rival
                and min(self.left_spacing, self.right_spacing) >= 0.6
                and min(self.left_drop, self.right_drop) >= drop)


def local_spacing(peaks, index, span=8):
    """Median distance between neighbouring calls, in scans."""
    steps = [peaks[k + 1] - peaks[k] for k in range(max(0, index - span), min(len(peaks) - 1, index + span))]
    steps = [step for step in steps if step > 0]
    return float(median(steps)) if steps else 12.0


def _summit(values, low, high, near):
    """Highest true local maximum in [low, high]; ties go to the scan nearest the call."""
    best = None
    for x in range(low, high + 1):
        value = values[x]
        if value > 0 and value >= values[x - 1] and value >= values[x + 1] and (
                value > values[x - 1] or value > values[x + 1]):
            if best is None or value > values[best] or (value == values[best] and abs(x - near) < abs(best - near)):
                best = x
    return best


def peak_evidence(trace, index, base=None):
    """Measure the peak under one call. ``base`` tests another channel at the same call."""
    peaks, count = trace.peaks, len(trace.peaks)
    base = base or trace.sequence[index]
    if base not in trace.channels or not 0 <= index < count:
        return None
    length = min(len(values) for values in trace.channels.values())
    scan = peaks[index]
    if length < 3 or not 1 <= scan < length - 1:
        return None
    spacing = local_spacing(peaks, index)
    left = (scan - peaks[index - 1]) / spacing if index > 0 else 1.0
    right = (peaks[index + 1] - scan) / spacing if index < count - 1 else 1.0
    heights = [trace.channels[trace.sequence[k]][peaks[k]]
               for k in range(max(0, index - 12), min(count, index + 13))
               if k != index and trace.sequence[k] in trace.channels and 0 <= peaks[k] < length]
    scale = max(1.0, float(median(heights))) if heights else 1.0
    values = trace.channels[base]
    half = max(2, round(0.45 * spacing))
    summit = _summit(values, max(1, scan - half), min(length - 2, scan + half), scan)
    has_peak = summit is not None
    if summit is None:
        summit = scan
    height = max(1, values[summit])
    near = max(2, round(0.33 * spacing))
    rival_peak = 0.0
    for other, other_values in trace.channels.items():
        if other == base:
            continue
        top = _summit(other_values, max(1, summit - near), min(length - 2, summit + near), summit)
        if top is not None:
            rival_peak = max(rival_peak, other_values[top] / height)
    rival_at_summit = max(v[summit] for other, v in trace.channels.items() if other != base) / height
    drops = []
    for neighbour in (index - 1, index + 1):
        if not 0 <= neighbour < count or not 0 <= peaks[neighbour] < length:
            drops.append(1.0)
            continue
        start, end = sorted((summit, peaks[neighbour]))
        if trace.sequence[neighbour] == base:
            # Same base on both sides of a valley: measure the valley itself.
            lower = max(1, min(height, values[peaks[neighbour]]))
            drops.append(1 - min(values[start:end + 1]) / lower)
        else:
            drops.append(1 - values[peaks[neighbour]] / height)
    nearby = [trace.qualities[k] or 0 for k in range(max(0, index - 5), min(count, index + 6)) if k != index]
    context = float(median(nearby)) if nearby else 0.0
    return PeakEvidence(base, has_peak, height, height / scale, abs(summit - scan) / spacing,
                        rival_at_summit, rival_peak, left, right, drops[0], drops[1], spacing, context)


def run_valley(trace, index):
    """Shallowest valley between neighbouring calls in the run of one base around a call.

    1.0 for a call with no same-base neighbour. A deep valley between every
    pair means each base of the run has its own summit.
    """
    base = trace.sequence[index]
    if base not in trace.channels:
        return 0.0
    first = last = index
    while first > 0 and trace.sequence[first - 1] == base:
        first -= 1
    while last < len(trace.sequence) - 1 and trace.sequence[last + 1] == base:
        last += 1
    values, peaks = trace.channels[base], trace.peaks
    shallowest = 1.0
    for k in range(first, last):
        left, right = sorted((peaks[k], peaks[k + 1]))
        lower = min(values[left], values[right])
        if lower <= 0 or right >= len(values):
            return 0.0
        shallowest = min(shallowest, 1 - min(values[left:right + 1]) / lower)
    return shallowest


def clear_base(trace, index):
    """The one base with a clear peak at this call, or None. Used when the AB1 call is N."""
    found = [base for base in 'ACGT'
             if (evidence := peak_evidence(trace, index, base)) is not None and evidence.clear]
    return found[0] if len(found) == 1 else None


@dataclass
class _State:
    kind: str                 # 'base', 'gap' or 'none'
    base: str = ''            # consensus orientation
    quality: int | None = None
    raw: int | None = None
    manual: bool = False
    anchor: int = 0           # highest ANCHOR_TIERS score this read meets here, or 0
    evidence: PeakEvidence | None = None
    valley: float | None = None   # run_valley of the run this column belongs to in this read

    @property
    def version(self):
        return self.base if self.kind == 'base' else REMOVE


def contig_sources(contig):
    """Sources of a contig, including projects saved in the two-read layout."""
    if contig.sources:
        return contig.sources
    sources = []
    for read_id, reverse, side, bases in ((contig.f_id, False, 'f', contig.f_bases),
                                          (contig.r_id, True, 'r', contig.r_bases)):
        if read_id is None:
            continue
        indices = (getattr(column, side) for column in contig.columns)
        sources.append(Source(read_id, reverse, [bases[i] if i is not None else None for i in indices]))
    return sources


class Reviewer:
    """Evaluate columns of one contig. Create once per contig; it caches coverage."""

    def __init__(self, project, contig):
        self.project = project
        self.contig = contig
        self.sources = contig_sources(contig)
        self.spans = []
        for source in self.sources:
            covered = [i for i, base in enumerate(source.mapping) if base is not None]
            self.spans.append((covered[0], covered[-1]) if covered else (1, 0))

    def _flank(self, mapping, column, step):
        found, k = [], column + step
        while 0 <= k < len(mapping) and len(found) < ANCHOR_BASES and abs(k - column) <= ANCHOR_BASES + 3:
            if mapping[k] is not None:
                found.append(mapping[k])
            k += step
        return found

    def _anchor(self, number, column, trace):
        """Phred tier of the clean, evenly spaced stretch this read has around the column, or 0.

        The nearest three called bases on each side (and the base in the column)
        must be unedited, consecutive in the AB1 file and evenly spaced. For a
        gap this also means the two flanking calls leave no room for a base.
        """
        mapping = self.sources[number].mapping
        left, right = self._flank(mapping, column, -1), self._flank(mapping, column, 1)
        if trace is None or len(left) < ANCHOR_BASES or len(right) < ANCHOR_BASES:
            return 0
        centre = [mapping[column]] if mapping[column] is not None else []
        chain = left[::-1] + centre + right
        if any(base.manual or base.raw is None or base.quality is None or base.base not in 'ACGT'
               for base in chain):
            return 0
        lowest = min(base.quality for base in chain)
        tier = next((tier for tier in ANCHOR_TIERS if lowest >= tier), 0)
        if not tier:
            return 0
        raws = [base.raw for base in chain]
        step = raws[1] - raws[0]
        if abs(step) != 1 or any(b - a != step for a, b in zip(raws, raws[1:])):
            return 0    # an edited or missing source base inside the stretch
        low, high = min(raws), max(raws)
        if high >= len(trace.peaks):
            return 0
        spacing = local_spacing(trace.peaks, (low + high) // 2)
        even = all(ANCHOR_SPACING[0] <= (trace.peaks[k + 1] - trace.peaks[k]) / spacing <= ANCHOR_SPACING[1]
                   for k in range(low, high))
        return tier if even else 0

    def _state(self, number, column):
        source = self.sources[number]
        mapping = source.mapping
        start, end = self.spans[number]
        if not start <= column <= end:
            return _State('none')
        trace = self.project.traces.get(source.read_id)
        base = mapping[column]
        if base is None:
            state = _State('gap', anchor=self._anchor(number, column, trace))
            if trace is not None:
                # The run in question is the one next to the gap in this read.
                flanks = [found[0] for found in (self._flank(mapping, column, -1), self._flank(mapping, column, 1))
                          if found and found[0].raw is not None and found[0].raw < len(trace.peaks)]
                if flanks:
                    state.valley = min(run_valley(trace, flank.raw) for flank in flanks)
            return state
        if base.base in ('', '-'):
            return _State('gap', manual=base.manual)
        state = _State('base', base.base, base.quality, base.raw, base.manual)
        if base.manual or base.raw is None or trace is None or base.raw >= len(trace.peaks):
            return state
        state.anchor = self._anchor(number, column, trace)
        raw_call = trace.sequence[base.raw]
        if raw_call in trace.channels:
            state.evidence = peak_evidence(trace, base.raw)
            state.valley = run_valley(trace, base.raw)
        else:
            found = clear_base(trace, base.raw)
            if found:
                state.base = COMPLEMENT[found] if source.reverse else found
                state.evidence = peak_evidence(trace, base.raw, found)
        return state

    def states(self, column):
        return [self._state(number, column) for number in range(len(self.sources))]

    def verdict(self, column):
        """Decide one column from all reads that cover it."""
        if not 0 <= column < len(self.contig.columns):
            raise ValueError('Review column is outside the contig.')
        present = [state for state in self.states(column) if state.kind != 'none']
        if not present:
            return Verdict(UNRESOLVED, 'no_support')
        if any(state.manual for state in present):
            return Verdict(UNRESOLVED, 'manual_edit', 'A read was edited here; the edit is kept.')
        for tier in ANCHOR_TIERS:
            anchors = [state for state in present if state.anchor >= tier]
            versions = {state.version for state in anchors}
            if len(versions) > 1:
                return self._resolved_run(anchors) or Verdict(
                    UNRESOLVED, 'anchored_reads_disagree', 'Two reads are clean here and still differ.')
            if versions:
                others = len(present) - len(anchors)
                detail = (f'{len(anchors)} read(s) in a clean stretch (Phred >= {tier}, even spacing)'
                          + (f'; {others} other read(s) in a degraded stretch' if others else ''))
                return Verdict(next(iter(versions)), 'anchored_read', detail)
        bases = [state for state in present if state.kind == 'base']
        if len(bases) < len(present):
            return Verdict(UNRESOLVED, 'length_disagreement')
        return self._bases_only(bases)

    @staticmethod
    def _resolved_run(anchors):
        """Two clean reads count a run of one base differently: trust the read that resolves it."""
        versions = {state.version for state in anchors}
        bases = versions - {REMOVE}
        if REMOVE not in versions or len(bases) != 1 or any(state.valley is None for state in anchors):
            return None
        for version in versions:
            mine = [state for state in anchors if state.version == version]
            others = [state for state in anchors if state.version != version]
            if (all(state.valley >= RUN_RESOLVED for state in mine)
                    and all(state.valley < RUN_MERGED for state in others)):
                return Verdict(version, 'resolved_run',
                               'Both reads are clean, but only one shows a separate summit for every base of the run.')
        return None

    @staticmethod
    def _bases_only(bases):
        calls = {state.base for state in bases}
        clear = [state for state in bases if state.evidence is not None and state.evidence.clear]
        if len(calls) == 1:
            base = next(iter(calls))
            if base in 'ACGT' and clear:
                return Verdict(base, 'clear_peak', 'An isolated, centred peak with no competing peak.')
            return Verdict(UNRESOLVED, 'unclear_peak')
        clear_calls = {state.base for state in clear}
        if len(clear_calls) == 1 and len(clear) < len(bases):
            base = next(iter(clear_calls))
            rivals = [state for state in bases if state.base != base]
            # The dissenting reads must show no usable peak of their own.
            if all(state.evidence is None or not state.evidence.has_peak or state.evidence.rival_peak > 0.6
                   for state in rivals):
                return Verdict(base, 'clear_peak_over_unclear',
                               'One read has an isolated peak; the other read has no distinct peak.')
        return Verdict(UNRESOLVED, 'reads_disagree')
