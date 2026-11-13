"""Review open flagged columns: local trace rules first, then an optional online model.

Both steps write the same record: ``Contig.ai_review`` holds the decision for a
column (a base, '' when the column holds no base, or 'N' when it is still
unresolved) and ``Contig.ai_notes`` says who decided. Callers pass a private
copy of the project; nothing is applied to the editor from here.
"""
from collections import Counter
from dataclasses import dataclass, field

from core import COMPLEMENT
from llm_review import OFFLINE, REMOVE as MODEL_REMOVE, WAVEFORM_BATCH_SIZE, review_waveforms
from trace_evidence import REMOVE, Reviewer, contig_sources, peak_evidence
from waveform_review import review_sites, waveform_evidence

RULE_NOTES = {
    'anchored_read': 'local rule: clean read',
    'resolved_run': 'local rule: resolved repeat peaks',
    'clear_peak': 'local rule: clear peak',
    'clear_peak_over_unclear': 'local rule: clear peak',
}
MODEL_PEAK_HEIGHT = 0.15   # a model's base needs a summit at least this tall, relative to nearby calls
# In a column covered by one read, a model's confirmation is applied this early
# in the read, or anywhere the call has an isolated summit of its own. Later in
# a read, held-out reads show that about 3% of the open low-Phred calls are
# wrong, mostly spurious extra calls on merged peaks, and the model confirms
# those as readily as the correct ones.
SINGLE_READ_LIMIT = 300
# Where one read has a base and another has a gap, an answer is applied only at
# these reasoning levels. On held-out reads gpt-6-luna was right in 25 of 25
# such answers at high effort and wrong in 3 of 26 at medium.
DEEP_EFFORTS = ('high', 'xhigh', 'max')


@dataclass
class ReviewOutcome:
    sites: int = 0                 # open flagged columns found
    local: Counter = field(default_factory=Counter)      # resolved locally, by rule
    removed: int = 0               # columns removed (by either step)
    sent: int = 0                  # columns sent to the online model
    replied: int = 0
    model_resolved: int = 0
    model_declined: int = 0        # model answered N or was not confident
    model_rejected: int = 0        # model's answer failed the local signal check
    model_suggested: int = 0       # confident answers kept as a suggestion, not applied
    completed: bool = True         # False when the online step stopped early
    changed: set = field(default_factory=set)            # contig keys with new decisions

    @property
    def local_resolved(self):
        return sum(self.local.values())

    @property
    def resolved(self):
        return self.local_resolved + self.model_resolved

    @property
    def unresolved(self):
        return self.sites - self.resolved

    def summary(self):
        return dict(sites=self.sites, local_resolved=self.local_resolved, local_rules=dict(self.local),
                    removed=self.removed, sent=self.sent, replied=self.replied,
                    model_resolved=self.model_resolved, model_declined=self.model_declined,
                    model_rejected=self.model_rejected, model_suggested=self.model_suggested,
                    unresolved=self.unresolved, completed=self.completed)


def _record(outcome, project, site, call, note):
    contig = project.contigs[site.contig]
    key = str(site.column)
    contig.ai_review[key] = call
    contig.ai_notes[key] = note
    outcome.changed.add(site.contig)
    if call == REMOVE:
        outcome.removed += 1


def local_review(project, outcome=None):
    """Apply the local trace rules to every open flagged column; return the columns left open."""
    outcome = outcome or ReviewOutcome()
    sites = review_sites(project)
    outcome.sites = len(sites)
    reviewers, remaining = {}, []
    for site in sites:
        contig = project.contigs[site.contig]
        reviewer = reviewers.get(site.contig)
        if reviewer is None:
            reviewer = reviewers[site.contig] = Reviewer(project, contig)
        verdict = reviewer.verdict(site.column)
        if verdict.resolved:
            outcome.local[verdict.rule] += 1
            _record(outcome, project, site, verdict.call, RULE_NOTES[verdict.rule])
        else:
            remaining.append(site)
    return outcome, remaining


def _covering(project, site):
    """(source, base or None) for every read whose aligned span includes the column."""
    states = []
    for source in contig_sources(project.contigs[site.contig]):
        mapping = source.mapping
        covered = [i for i, base in enumerate(mapping) if base is not None]
        if covered and covered[0] <= site.column <= covered[-1]:
            states.append((source, mapping[site.column]))
    return states


def signal_supports(project, site, call):
    """Check a model's answer against the trace before it is applied.

    A base needs a real summit in its channel under some read's call in this
    column. Removal needs a read that already has a gap here: a single read
    cannot show that one of its own calls is spurious (see docs/TRACE_REVIEW.md).
    """
    states = _covering(project, site)
    if call == REMOVE:
        return len(states) > 1 and any(base is None for _, base in states)
    for source, base in states:
        trace = project.traces.get(source.read_id)
        if base is None or base.raw is None or base.manual or trace is None or base.raw >= len(trace.peaks):
            continue
        evidence = peak_evidence(trace, base.raw, COMPLEMENT[call] if source.reverse else call)
        if evidence is not None and evidence.has_peak and evidence.relative_height >= MODEL_PEAK_HEIGHT:
            return True
    return False


def single_read_limit(project, site, call):
    """Why a confident, signal-supported answer is still not applied in a single-read column.

    With one read there is no second observation. Held-out reads show the model
    is reliable there when it confirms the read's own call early in the read or
    on an isolated summit. It never corrected a wrong call with confidence, and
    late in a read it confirms spurious extra calls on merged peaks as readily
    as correct ones.
    """
    states = _covering(project, site)
    if len(states) != 1:
        return None
    base = states[0][1]
    if base is None or base.raw is None or base.manual:
        return 'single read'
    if call != base.base:
        return 'differs from the only read'
    if base.raw >= SINGLE_READ_LIMIT:
        source = states[0][0]
        trace = project.traces.get(source.read_id)
        evidence = peak_evidence(trace, base.raw) if trace is not None and base.raw < len(trace.peaks) else None
        if evidence is None or not evidence.isolated:
            return f'single read past base {SINGLE_READ_LIMIT}, peak not isolated'
    return None


def length_disagreement(project, site):
    states = _covering(project, site)
    return len(states) > 1 and any(base is None for _, base in states)


def model_review(project, sites, config, outcome, *, on_batch_done=None, is_cancelled=None):
    """Ask the online model about the remaining columns and apply the answers that pass the check.

    Raises ``ReviewError`` if a request fails; decisions are only recorded once
    every batch has replied, so an interrupted review changes nothing.
    """
    outcome.sent = len(sites)
    if not sites or config.provider == OFFLINE:
        outcome.sent = 0
        return outcome
    outcome.completed = False

    def batch_done(done, total, replied):
        outcome.replied = replied
        if on_batch_done is not None:
            on_batch_done(done, total, replied)

    decisions = review_waveforms(config, len(sites), lambda i: waveform_evidence(project, sites[i]),
                                 on_batch_done=batch_done, is_cancelled=is_cancelled)
    outcome.replied = len(sites)
    outcome.completed = True
    label = config.model
    for index, site in enumerate(sites):
        decision = decisions[index]
        call = REMOVE if decision.call == MODEL_REMOVE else decision.call
        shown = 'no base' if call == REMOVE else call
        if call == 'N':
            outcome.model_declined += 1
            _record(outcome, project, site, 'N', f'{label}: not settled')
        elif not signal_supports(project, site, call):
            outcome.model_rejected += 1
            _record(outcome, project, site, 'N', f'{label}: answer {shown} not supported by the signal')
        elif decision.confidence != 'high':
            outcome.model_declined += 1
            _record(outcome, project, site, 'N', f'{label}: leans {shown}, low confidence, not applied')
        elif (limit := single_read_limit(project, site, call)) is not None:
            outcome.model_suggested += 1
            _record(outcome, project, site, 'N', f'{label}: suggests {shown}; {limit}, not applied')
        elif config.effort not in DEEP_EFFORTS and length_disagreement(project, site):
            outcome.model_suggested += 1
            _record(outcome, project, site, 'N', f'{label}: suggests {shown}; a length disagreement is '
                                                 'applied only at High reasoning effort')
        else:
            outcome.model_resolved += 1
            _record(outcome, project, site, call, f'{label}: high confidence')
    return outcome


def model_batches(sites):
    return (len(sites) + WAVEFORM_BATCH_SIZE - 1) // WAVEFORM_BATCH_SIZE
