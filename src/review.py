"""Read-only inspection and opt-in review edits for saved namiyomi projects.

Used by the MCP server, but has no MCP dependency so it can be tested directly.
No assembly or consensus logic lives here: edits use the same two project fields the
GUI uses (``Contig.overrides`` for a changed call, ``Contig.black_marks`` for
"Mark reviewed"), and always go to a NEW project file, never over the input.
"""
import io
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from batch import CLEAN, NEEDS_REVIEW, SINGLE_READ, triage
from core import COMPLEMENT, Project, atomic_text, base_text, export_markers
from sequence_find import consensus_coordinates, find_matches
from trace_plot import trace_samples

BASE_COLORS = {'A': '#008234', 'C': '#1559c5', 'G': '#161616', 'T': '#d1272c'}


def load_project(path):
    return Project.load(Path(path).expanduser())


def resolve_contig(project, ref):
    """Find a contig by display name (as in summary.json) or by its internal key."""
    for key, contig in project.contigs.items():
        if ref in (contig.sequence_name, key):
            return key, contig
    names = ', '.join(c.sequence_name for c in project.contigs.values())
    raise ValueError(f'No contig named {ref!r}. Contigs: {names}')


def list_contigs(project, category=None):
    rows = triage(project)
    if category:
        if category not in (CLEAN, NEEDS_REVIEW, SINGLE_READ):
            raise ValueError(f'Unknown filter {category!r}; use clean, needs_review or single_read.')
        rows = [r for r in rows if r['category'] == category]
    return rows


def contig_details(project, ref):
    """Flagged columns (0-based alignment columns) with every source read's call and Phred."""
    key, contig = resolve_contig(project, ref)
    row = next(r for r in triage(project) if r['key'] == key)
    sources = []
    for index, source in enumerate(contig.sources):
        read = project.reads[source.read_id]
        sources.append(dict(index=index, file=Path(read.path).name, reverse_complemented=source.reverse,
                            trim_start=read.start + 1, trim_end=read.end))
    flagged = []
    for col in contig.issues():
        reads = []
        for index, source in enumerate(contig.sources):
            b = source.mapping[col]
            reads.append(dict(source=index, base=None if b is None else b.base,
                              phred=None if b is None else b.quality,
                              raw_position=None if b is None or b.raw is None else b.raw + 1,
                              peak_heights=_peak_heights(project.traces[source.read_id], b, source.reverse)))
        column = contig.columns[col]
        flagged.append(dict(column=col, consensus=contig.call(col), automatic_call=column.call,
                            reason=column.reason, manual_override=str(col) in contig.overrides, reads=reads))
    return dict(name=contig.sequence_name, key=key, category=row['category'], status=contig.status,
                overlap=contig.overlap, identity=row['identity'], columns=len(contig.columns),
                min_quality=project.settings['min_quality'], sources=sources, flagged=flagged)


def _peak_heights(trace, base, reverse):
    """Raw height of each channel at the base's peak, named in contig orientation (None if no peak)."""
    if base is None or base.raw is None:
        return None
    x = trace.peaks[base.raw]
    return {COMPLEMENT[ch] if reverse else ch: values[x] for ch, values in sorted(trace.channels.items())}


def find_sequence(project, ref, query, identity=100):
    _, contig = resolve_contig(project, ref)
    sequence, columns = consensus_coordinates(contig)
    return [dict(start=m.start, end=m.end, strand=m.strand, identity=m.identity,
                 first_column=columns[m.start], last_column=columns[m.end - 1])
            for m in find_matches(sequence, query, identity)]


def render_trace(project, ref, column, window=15):
    """PNG (bytes) of every source read's chromatogram around one alignment column."""
    from matplotlib.figure import Figure
    key, contig = resolve_contig(project, ref)
    total = len(contig.columns)
    if not 0 <= column < total:
        raise ValueError(f'Column must be between 0 and {total - 1}.')
    window = max(3, min(int(window), 60))
    left, right = max(0, column - window), min(total, column + window + 1)
    fig = Figure(figsize=(10, 2.6 * len(contig.sources)), dpi=100)
    axes = fig.subplots(len(contig.sources), 1, sharex=True, squeeze=False)[:, 0]
    minq = project.settings['min_quality']
    for ax, source in zip(axes, contig.sources):
        read, trace = project.reads[source.read_id], project.traces[source.read_id]
        samples = trace_samples(trace, source.mapping, left, right, 1.0, source.reverse)
        xs = [(x - 82) + left for x, _ in samples]
        peak = max((trace.channels[c][s] for c in trace.channels for _, s in samples), default=1) or 1
        for channel, values in trace.channels.items():
            shown = COMPLEMENT[channel] if source.reverse else channel
            ax.plot(xs, [values[s] / peak for _, s in samples], color=BASE_COLORS[shown], lw=1.1, label=shown)
        ax.axvspan(column - .5, column + .5, color='#f0c040', alpha=.3)
        ax.axhline(0, color='#ddd', lw=.8)
        for col in range(left, right):
            b = source.mapping[col]
            if b is None:
                ax.text(col, 1.12, '-', ha='center', color='#777', fontsize=9)
                continue
            ax.text(col, 1.12, b.base or '-', ha='center', fontsize=10, fontweight='bold',
                    color=BASE_COLORS.get(b.base, '#555'))
            if b.quality is not None:
                ax.text(col, 1.0, str(b.quality), ha='center', fontsize=6,
                        color='#222' if b.quality >= minq else '#b00020')
        ax.set_ylim(-.05, 1.25)
        ax.set_yticks([])
        ax.set_xlim(left - .5, right - .5)
        strand = ' (reverse-complemented)' if source.reverse else ''
        ax.set_title(f'{Path(read.path).name}{strand}', loc='left', fontsize=9)
    axes[-1].set_xlabel(f"{contig.sequence_name}: alignment column {column} (0-based, highlighted). "
                        f"Top row = base call, then Phred (red < Q{minq}). Consensus = {contig.call(column)!r}")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    return buf.getvalue()


def apply_review_edits(project_path, output_path, edits, report_path=None, reviewer='Claude', unresolved=None):
    """Apply review verdicts to a COPY of the project and log each with its reason.

    Each edit: ``{contig, column, action: 'set'|'confirm', value (for set), reason}``.
    ``set`` changes the consensus call (like typing a base in the GUI); ``confirm`` marks the
    flag reviewed without changing the call. All edits are validated before any is applied.
    ``unresolved`` optionally gives ``{contig, column, reason}`` for flags deliberately left alone;
    every flag still open after the edits is listed in the report, with its reason if given.
    """
    source, output = Path(project_path).expanduser().resolve(), Path(output_path).expanduser().resolve()
    if source == output:
        raise ValueError('The reviewed project must be saved to a new file; the input is never overwritten.')
    edits, unresolved = list(edits or []), list(unresolved or [])
    if not edits and not unresolved:
        raise ValueError('No edits given.')
    project = load_project(source)
    plan = []
    for n, edit in enumerate(edits, start=1):
        try:
            key, contig = resolve_contig(project, edit['contig'])
            col, action = int(edit['column']), edit.get('action', 'set')
            reason = str(edit.get('reason', '')).strip()
            if contig.stale:
                raise ValueError('Inputs have changed; rebuild the contig first.')
            if not 0 <= col < len(contig.columns):
                raise ValueError(f'Column {col} is outside 0..{len(contig.columns) - 1}.')
            if action not in ('set', 'confirm'):
                raise ValueError("action must be 'set' or 'confirm'.")
            if not reason:
                raise ValueError('A reason is required so the change can be audited.')
            value = None
            if action == 'set':
                value = base_text(str(edit.get('value', '')))
                if len(value) != 1:
                    raise ValueError('value must be one DNA/IUPAC base.')
            plan.append((key, contig, col, action, value, reason))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'Edit {n}: {exc.args[0] if isinstance(exc, KeyError) else exc}') from exc
    notes = {}
    for n, item in enumerate(unresolved, start=1):
        try:
            _, contig = resolve_contig(project, item['contig'])
            notes[(contig.sequence_name, int(item['column']))] = str(item.get('reason', '')).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'Unresolved item {n}: {exc.args[0] if isinstance(exc, KeyError) else exc}') from exc
    lines = []
    for key, contig, col, action, value, reason in plan:
        before = contig.call(col)
        if action == 'set':
            contig.overrides[str(col)] = value
            contig.black_marks.pop(str(col), None)  # same as the GUI's consensus edit
            lines.append(f'- **{contig.sequence_name}** column {col}: {before} -> **{value}**. {reason}')
        else:
            contig.black_marks[str(col)] = False    # same as the GUI's "Mark reviewed"
            lines.append(f'- **{contig.sequence_name}** column {col}: confirmed {before}. {reason}')
    project.dirty = True
    project.save(output)
    remaining = [r for r in triage(project) if r['category'] == NEEDS_REVIEW]
    open_lines = []
    for r in remaining:
        for col in r['issue_columns']:
            note = notes.get((r['name'], col))
            open_lines.append(f'- **{r["name"]}** column {col}' + (f': {note}' if note else ''))
    body = '\n'.join(lines) if lines else '_No edits._'
    body += '\n\n### Left unresolved (check these in the app)\n\n' + ('\n'.join(open_lines) or '_None._')
    report = Path(report_path).expanduser() if report_path else output.with_name('review_report.md')
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    old = report.read_text(encoding='utf-8') if report.exists() else '# Review report\n'
    atomic_text(report, old.rstrip('\n') + f'\n\n## {reviewer}, {stamp}\n'
                f'Source project: `{source.name}` (unchanged). Reviewed copy: `{output.name}`.\n\n'
                + body + '\n')
    return dict(output=str(output), report=str(report), applied=len(plan),
                still_needs_review=[dict(name=r['name'], columns=r['issue_columns']) for r in remaining])


def export_fasta(project_path, output_dir, allow_single=False, suffix=None):
    """One FASTA per marker from a saved project (stale contigs and, unless allowed, single reads are refused).

    Files are named ``<marker><suffix>.fasta``. By default the suffix is ``.reviewed`` when the project file
    is a reviewed copy (``*.reviewed.json``), so the batch run's unedited FASTA is never overwritten.
    """
    project_path = Path(project_path).expanduser()
    if suffix is None:
        suffix = '.reviewed' if project_path.stem.endswith('.reviewed') else ''
    project = load_project(project_path)
    rows = triage(project)
    skip = {r['key'] for r in rows if r['category'] == SINGLE_READ and not allow_single}
    keep = {k: c for k, c in project.contigs.items() if k not in skip}
    if not keep:
        raise ValueError('Nothing to export (single-read contigs need allow_single).')
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        names = export_markers(SimpleNamespace(contigs=keep), Path(tmp), allow_single=True)
        files = []
        for name in names:
            final = name[:-len('.fasta')] + suffix + '.fasta'
            shutil.move(str(Path(tmp) / name), str(output_dir / final))
            files.append(final)
    return dict(fasta=files, single_read_not_exported=sorted(r['name'] for r in rows if r['key'] in skip))
