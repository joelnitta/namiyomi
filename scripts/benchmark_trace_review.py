"""Measure the local trace rules against reads held out of the assembly.

For every group of two or more AB1 files (grouped like Build Contig AUTO, by
the text before the first underscore) this script assembles all reads, then
hides one read at a time. The remaining reads are re-called and their flagged
columns are judged by ``trace_evidence``. The hidden read supplies the answer
wherever it is in a clean stretch (three bases on each side at Phred >= 40 with
even peak spacing). Nothing is written next to the AB1 files.

    python scripts/benchmark_trace_review.py --input FOLDER [--jobs 8] [--tsv decisions.tsv]

The grouping rule must hold for the folder: one prefix is one specimen and one
locus. Reads of different specimens under one prefix make the "truth" wrong.
"""
import argparse
from collections import Counter, defaultdict
import csv
from multiprocessing import Pool
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from assembly import recall                      # noqa: E402
from core import Column, Contig, Project, Source  # noqa: E402
from trace_evidence import ANCHOR_TIERS, Reviewer  # noqa: E402

TRUTH_TIER = ANCHOR_TIERS[0]
PHRED_BANDS = ('below 10', '10 to 19', '20 to 29', '30 to 39', '40 and above')


def without(project, full, hidden):
    """The contig the remaining reads would give, in the columns they cover."""
    keep = [k for k in range(len(full.sources)) if k != hidden]
    columns = [c for c in range(len(full.columns)) if any(full.sources[k].mapping[c] is not None for k in keep)]
    sources = [Source(full.sources[k].read_id, full.sources[k].reverse,
                      [full.sources[k].mapping[c] for c in columns]) for k in keep]
    calls = [Column(None, None, *recall(sources, i, project.settings['min_quality'])) for i in range(len(columns))]
    return Contig('s', 'm', None, None, [], [], calls, '', 0, None, 'x', sources=sources), columns


def judge_group(item):
    prefix, paths = item
    project = Project()
    added, _ = project.add(paths)
    usable = [rid for rid in added if project.reads[rid].end > project.reads[rid].start]
    if len(usable) < 2:
        return []
    try:
        key = project.assemble_reads(usable, 'benchmark')
    except ValueError:
        return []
    full = project.contigs[key]
    whole = Reviewer(project, full)
    rows = []
    for hidden in range(len(full.sources)):
        partial, columns = without(project, full, hidden)
        reviewer = Reviewer(project, partial)
        start, end = whole.spans[hidden]
        trace = project.traces[full.sources[hidden].read_id]
        truth = {}
        for i, column in enumerate(columns):
            if start + 3 <= column <= end - 3 and whole._anchor(hidden, column, trace) >= TRUTH_TIER:
                base = full.sources[hidden].mapping[column]
                truth[i] = base.base if base is not None else ''
        verdicts = {}

        def final(i):
            column = partial.columns[i]
            flagged = column.call == 'N' or column.reason not in ('agree', 'single')
            if flagged and i not in verdicts:
                verdicts[i] = reviewer.verdict(i)
            verdict = verdicts.get(i)
            return (verdict.call if verdict is not None and verdict.resolved else column.call), verdict, flagged

        for i in truth:
            column = partial.columns[i]
            call, verdict, flagged = final(i)
            covering = [k for k, (a, b) in enumerate(reviewer.spans) if a <= i <= b]
            coverage = len(covering)
            if coverage == 1:
                # One read against the held-out answer: how often is a call of each Phred band wrong?
                base = partial.sources[covering[0]].mapping[i]
                if base is not None and not base.manual and base.quality is not None:
                    kind = 'right' if base.base == truth[i] else 'extra' if truth[i] == '' else 'other'
                    rows.append((prefix, i, 'read_call', PHRED_BANDS[min(4, base.quality // 10)], 1, '', kind))
            if not flagged:
                rows.append((prefix, i, 'accepted', column.reason, coverage, '',
                             'correct' if call == truth[i] else 'wrong'))
                continue
            window = [k for k in range(i - 3, i + 4) if k in truth]
            if not verdict.resolved:
                result = 'unresolved'
            elif (''.join(final(k)[0] for k in window) == ''.join(truth[k] for k in window)
                  or verdict.call == truth[i]):
                result = 'correct'
            else:
                result = 'wrong'
            rows.append((prefix, i, 'flagged', column.reason, coverage, verdict.rule, result))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--input', required=True, type=Path, help='Folder of .ab1 files (never modified)')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--tsv', type=Path, help='Also write one row per judged column')
    args = parser.parse_args(argv)
    groups = defaultdict(list)
    for path in sorted(args.input.iterdir()):
        if path.is_file() and path.suffix.lower() == '.ab1':
            groups[path.stem.split('_', 1)[0]].append(path)
    items = [(prefix, paths) for prefix, paths in groups.items() if len(paths) >= 2]
    if not items:
        print('No group has two or more AB1 files.', file=sys.stderr)
        return 2
    rows = []
    with Pool(max(1, args.jobs)) as pool:
        for part in pool.imap_unordered(judge_group, items, chunksize=4):
            rows.extend(part)
    if args.tsv:
        with open(args.tsv, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle, delimiter='\t')
            writer.writerow(('group', 'column', 'kind', 'flag', 'coverage', 'rule', 'result'))
            writer.writerows(sorted(rows))
    accepted = Counter((row[3], row[6]) for row in rows if row[2] == 'accepted')
    flagged = Counter((row[5], row[6]) for row in rows if row[2] == 'flagged')
    judged = sum(1 for row in rows if row[2] != 'read_call')
    print(f'{len(items)} groups, {judged} columns with a held-out answer')
    print('\nCalls the consensus accepts without a flag (for comparison):')
    for reason in sorted({key[0] for key in accepted}):
        right, wrong = accepted[(reason, 'correct')], accepted[(reason, 'wrong')]
        print(f'  {reason:<12} {right + wrong:>8} calls, {wrong} wrong ({100 * wrong / max(1, right + wrong):.3f}%)')
    calls = Counter((row[3], row[6]) for row in rows if row[2] == 'read_call')
    print('\nSingle-read calls by Phred score, against the held-out read:')
    for band in PHRED_BANDS:
        right, extra, other = (calls[(band, kind)] for kind in ('right', 'extra', 'other'))
        total = right + extra + other
        print(f'  {band:<12} {total:>8} calls, {extra + other} wrong ({100 * (extra + other) / max(1, total):.3f}%), '
              f'{extra} of them spurious extra bases')
    print('\nFlagged columns, by the rule that judged them:')
    for rule in sorted({key[0] for key in flagged}):
        right, wrong, open_ = (flagged[(rule, result)] for result in ('correct', 'wrong', 'unresolved'))
        if open_:
            print(f'  {rule:<26} {open_:>6} left unresolved')
        else:
            print(f'  {rule:<26} {right + wrong:>6} decided, {wrong} wrong ({100 * wrong / max(1, right + wrong):.2f}%)')
    decided = sum(count for (rule, result), count in flagged.items() if result != 'unresolved')
    total = sum(flagged.values())
    print(f'\n{decided} of {total} flagged columns decided ({100 * decided / max(1, total):.0f}%).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
