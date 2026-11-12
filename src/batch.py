"""Headless batch processing: import, trim, group, assemble, triage and export.

This module never imports tkinter and never changes the GUI. All assembly,
trimming and consensus work is done by ``core`` / ``assembly``; this file only
decides which reads belong together, collects per-group errors, classifies the
resulting contigs and writes outputs (always to a separate output directory).

Run from the command line::

    python src/batch.py --input DIR --output DIR [--sheet sheet.csv] [--dry-run]
"""
import argparse
import csv
import io
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from core import (Project, atomic_text, export_markers, fasta_text, marker_filename, validate_settings,
                  validate_trim_settings)

CLEAN, NEEDS_REVIEW, SINGLE_READ, FAILED = 'clean', 'needs_review', 'single_read', 'failed'
CATEGORIES = (CLEAN, NEEDS_REVIEW, SINGLE_READ, FAILED)
SHEET_COLUMNS = ('file', 'sample', 'marker')


@dataclass
class Group:
    sample: str
    marker: str
    paths: list = field(default_factory=list)

    @property
    def label(self):
        return f'{self.sample}/{self.marker}'


@dataclass
class Plan:
    mode: str                                   # 'prefix' or 'sheet'
    groups: list = field(default_factory=list)
    unassigned: list = field(default_factory=list)      # AB1 files not in the sheet
    sheet_missing: list = field(default_factory=list)   # sheet rows with no AB1 file

    def to_dict(self):
        return dict(mode=self.mode,
                    groups=[dict(sample=g.sample, marker=g.marker, files=[Path(p).name for p in g.paths])
                            for g in self.groups],
                    unassigned=list(self.unassigned), sheet_missing=list(self.sheet_missing))


def find_ab1(input_dir):
    """Sorted AB1 files directly inside ``input_dir`` (sorted for determinism)."""
    directory = Path(input_dir)
    if not directory.is_dir():
        raise ValueError(f'Input folder not found: {directory}')
    return sorted((p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == '.ab1'),
                  key=lambda p: p.name)


def read_sheet(csv_path):
    """Read a sample sheet with columns file,sample,marker -> {filename: (sample, marker)}."""
    rows = {}
    with open(csv_path, newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        header = [(h or '').strip().lower() for h in (reader.fieldnames or [])]
        if len(set(header)) != len(header) or not set(SHEET_COLUMNS) <= set(header):
            raise ValueError('The sample sheet needs the columns: file, sample, marker.')
        reader.fieldnames = header
        for number, row in enumerate(reader, start=2):
            name = (row['file'] or '').strip()
            sample = (row['sample'] or '').strip()
            marker = (row['marker'] or '').strip()
            if not (name or sample or marker):
                continue
            if not (name and sample and marker):
                raise ValueError(f'Sample sheet row {number}: file, sample and marker are all required.')
            try:
                fasta_text([(sample, 'A')])  # same identifier rule as the FASTA export
            except ValueError as exc:
                raise ValueError(f'Sample sheet row {number}: {exc}') from exc
            if name in rows:
                raise ValueError(f'Sample sheet row {number}: {name} is listed more than once.')
            rows[name] = (sample, marker)
    return rows


def plan_groups(paths, sheet=None):
    """Decide which files form one contig without reading any trace (dry run)."""
    paths = sorted((Path(p) for p in paths), key=lambda p: p.name)
    if sheet is None:
        # Same rule as the GUI's Build Contig (AUTO): text before the first underscore.
        groups = {}
        for path in paths:
            groups.setdefault(path.stem.split('_', 1)[0], []).append(path)
        return Plan('prefix', [Group(prefix, 'auto', ps) for prefix, ps in sorted(groups.items())])
    plan = Plan('sheet')
    groups = {}
    present = {p.name for p in paths}
    for path in paths:
        if path.name in sheet:
            groups.setdefault(sheet[path.name], []).append(path)
        else:
            plan.unassigned.append(path.name)
    plan.sheet_missing = sorted(name for name in sheet if name not in present)
    plan.groups = [Group(sample, marker, ps) for (sample, marker), ps in sorted(groups.items())]
    return plan


def _safe_name(text):
    return re.sub(r'[\s>|\x00-\x1f]', '_', text)


def build_project(plan, settings=None, trim=None):
    """Load every planned read and assemble each group; one bad group never aborts the batch.

    Returns ``(project, report)`` where report holds read-level problems and failed groups.
    """
    project = Project()
    project.settings.update(settings or {})
    project.trim_settings.update(trim or {})
    validate_settings(project.settings)
    validate_trim_settings(project.trim_settings)
    report = dict(read_errors=[], empty_after_trim=[], failed=[])

    all_paths = [p for g in plan.groups for p in g.paths]
    added, errors = project.add(all_paths)
    report['read_errors'] = sorted(errors)
    by_path = {Path(project.reads[rid].path).resolve(): rid for rid in added}

    used_names = set()
    for group in plan.groups:
        ids = []
        for path in group.paths:
            rid = by_path.get(Path(path).resolve())
            if rid is None:
                continue  # load error, already reported
            read = project.reads[rid]
            if read.start == read.end:
                report['empty_after_trim'].append(Path(path).name)
                continue
            read.sample, read.marker = group.sample, group.marker
            ids.append(rid)
        files = [Path(p).name for p in group.paths]
        if not ids:
            report['failed'].append(dict(group=group.label, files=files,
                                         error='No usable reads (load errors or trimmed to nothing).'))
            continue
        key = project.key(group.sample, group.marker)
        base = _safe_name(group.sample if group.marker == 'auto' else f'{group.sample}_{group.marker}')
        name, index = base, 2
        while name in used_names:
            name = f'{base}_{index}'
            index += 1
        try:
            project.assemble_reads(ids, name, key)
            used_names.add(name)
        except Exception as exc:  # collect, never abort the batch
            report['failed'].append(dict(group=group.label, files=files, error=str(exc)))
    project.invalidate()
    return project, report


def triage(project):
    """Classify each contig: clean / needs_review / single_read (failed groups are reported separately)."""
    rows = []
    for key, contig in project.contigs.items():
        issues = contig.issues()
        if contig.stale:
            category = NEEDS_REVIEW
        elif contig.status in ('single_read', '片方向') or len(contig.read_ids()) == 1:
            category = SINGLE_READ
        elif issues:
            category = NEEDS_REVIEW
        else:
            category = CLEAN
        seq = contig.sequence()
        rows.append(dict(key=key, name=contig.sequence_name, sample=contig.sample, marker=contig.marker,
                         category=category, status=contig.status, stale=contig.stale,
                         reads=[Path(project.reads[r].path).name for r in contig.read_ids() if r in project.reads],
                         length=len(seq), n_calls=seq.count('N'), overlap=contig.overlap,
                         identity=None if contig.identity is None else round(contig.identity, 4),
                         issue_columns=issues))
    return sorted(rows, key=lambda r: (r['sample'], r['marker']))


def _export(project, rows, output_dir, allow_single):
    """Write one FASTA per marker through core.export_markers; returns (files, single_skipped, invalid_id)."""
    skip = {r['key'] for r in rows if r['category'] == SINGLE_READ and not allow_single}
    skipped = sorted(r['name'] for r in rows if r['key'] in skip)
    invalid = []
    for key, contig in project.contigs.items():
        try:  # FASTA headers use the sample ID; whitespace etc. would abort the whole marker file
            fasta_text([(contig.sample, 'A')])
        except ValueError:
            invalid.append(contig.sequence_name)
            skip.add(key)
    keep = {k: c for k, c in project.contigs.items() if k not in skip}
    if not keep:
        return [], skipped, sorted(invalid)
    filenames = {}
    for contig in keep.values():
        name = marker_filename(contig.marker).casefold()
        if filenames.setdefault(name, contig.marker) != contig.marker:
            raise ValueError('Marker names produce the same FASTA filename (ignoring case).')
    return export_markers(SimpleNamespace(contigs=keep), output_dir, allow_single=True), skipped, sorted(invalid)


def summary_tsv(summary):
    output = io.StringIO()
    writer = csv.writer(output, delimiter='\t', lineterminator='\r\n')
    writer.writerow(('name', 'sample', 'marker', 'category', 'reads', 'length', 'overlap', 'identity', 'flagged_columns', 'error'))
    for r in summary['contigs']:
        writer.writerow((
            r['name'], r['sample'], r['marker'], r['category'], ','.join(r['reads']), r['length'],
            r['overlap'], '' if r['identity'] is None else r['identity'],
            ','.join(map(str, r['issue_columns'])), ''))
    for f in summary['failed']:
        sample, _, marker = f['group'].partition('/')
        writer.writerow((f['group'], sample, marker, FAILED, ','.join(f['files']), '', '', '', '', f['error']))
    return output.getvalue()


def run_batch(input_dir, output_dir, *, sheet=None, settings=None, trim=None, allow_single=False,
              dry_run=False):
    """Process a folder of AB1 files. Originals are never modified.

    ``sheet`` is a CSV path (or None for GUI-style prefix grouping). Returns a JSON-serializable summary.
    """
    input_dir = Path(input_dir).resolve()
    paths = find_ab1(input_dir)
    plan = plan_groups(paths, read_sheet(sheet) if sheet else None)
    if dry_run:
        return dict(dry_run=True, input=str(input_dir), ab1_files=len(paths), plan=plan.to_dict())
    if not paths:
        raise ValueError(f'No .ab1 files found in {input_dir}')
    if output_dir is None:
        raise ValueError('Choose a new output folder with --output.')
    output_dir = Path(output_dir)
    if output_dir.is_symlink() or output_dir.exists():
        raise ValueError('Choose a new output folder; existing paths are kept.')
    output_dir = output_dir.resolve()

    project, report = build_project(plan, settings, trim)
    rows = triage(project)
    project_path = output_dir / 'project.json'
    counts = {c: sum(r['category'] == c for r in rows) for c in CATEGORIES}
    counts[FAILED] = len(report['failed'])
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    # A sibling folder gives the staged project the same relative AB1 paths.
    # Publish only after all exports are validated and written successfully.
    with tempfile.TemporaryDirectory(prefix='.namiyomi-batch-', dir=output_dir.parent) as directory:
        stage = Path(directory)
        project.export_project(stage / 'project.json')
        fasta_files, skipped, invalid_ids = _export(project, rows, stage, allow_single)
        summary = dict(dry_run=False, input=str(input_dir), output=str(output_dir), mode=plan.mode,
                       ab1_files=len(paths), settings=project.settings, trim_settings=project.trim_settings,
                       counts=counts, contigs=rows, failed=report['failed'],
                       read_errors=report['read_errors'], empty_after_trim=sorted(report['empty_after_trim']),
                       unassigned=plan.unassigned, sheet_missing=plan.sheet_missing,
                       project=str(project_path), fasta=fasta_files, single_read_not_exported=skipped,
                       not_exported_invalid_sample_id=invalid_ids)
        atomic_text(stage / 'summary.json', json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
        atomic_text(stage / 'summary.tsv', summary_tsv(summary))
        if output_dir.is_symlink() or output_dir.exists():
            raise ValueError('The output folder now exists. Choose a new folder.')
        stage.rename(output_dir)
    return summary


def format_summary(summary):
    if summary['dry_run']:
        out = [f"Dry run: {summary['ab1_files']} AB1 files, {len(summary['plan']['groups'])} groups ({summary['plan']['mode']} mode)"]
        for g in summary['plan']['groups']:
            out.append(f"  {g['sample']}/{g['marker']}: {', '.join(g['files'])}")
        for key, title in (('unassigned', 'Not in sheet (skipped)'), ('sheet_missing', 'In sheet but no file')):
            if summary['plan'][key]:
                out.append(f'{title}: ' + ', '.join(summary['plan'][key]))
        return '\n'.join(out)
    c = summary['counts']
    out = [f"{summary['ab1_files']} AB1 files -> " + ', '.join(f'{c[k]} {k}' for k in CATEGORIES)]
    for r in summary['contigs']:
        extra = f"  flagged columns: {r['issue_columns']}" if r['issue_columns'] else ''
        out.append(f"  {r['category']:<12} {r['name']}  ({', '.join(r['reads'])}){extra}")
    for f in summary['failed']:
        out.append(f"  {FAILED:<12} {f['group']}: {f['error']}")
    for key, title in (('read_errors', 'Read errors'), ('empty_after_trim', 'Trimmed to nothing'),
                       ('unassigned', 'Not in sheet (skipped)'), ('sheet_missing', 'In sheet but no file'),
                       ('single_read_not_exported', 'Single-read, not exported (use --allow-single)'),
                       ('not_exported_invalid_sample_id', 'Not exported (sample ID has whitespace or > |; use a sample sheet)')):
        if summary[key]:
            out.append(f'{title}: ' + '; '.join(summary[key]))
    out.append(f"Wrote {summary['project']}, summary.json, summary.tsv and: {', '.join(summary['fasta']) or 'no FASTA'}")
    return '\n'.join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--input', required=True, type=Path, help='Folder of .ab1 files (never modified)')
    parser.add_argument('--output', type=Path, help='New output folder (required unless using --dry-run)')
    parser.add_argument('--sheet', type=Path, help='CSV with columns file,sample,marker (default: group by filename prefix like the GUI)')
    parser.add_argument('--allow-single', action='store_true', help='Also export single-read sequences')
    parser.add_argument('--dry-run', action='store_true', help='List the planned groups and write nothing')
    parser.add_argument('--min-quality', type=int)
    parser.add_argument('--min-overlap', type=int)
    parser.add_argument('--min-identity', type=float)
    parser.add_argument('--no-trim', action='store_true', help='Disable automatic Mott trimming')
    args = parser.parse_args(argv)
    settings = {k: v for k, v in (('min_quality', args.min_quality), ('min_overlap', args.min_overlap),
                                  ('min_identity', args.min_identity)) if v is not None}
    try:
        summary = run_batch(args.input, args.output, sheet=args.sheet, settings=settings,
                            trim=dict(enabled=False) if args.no_trim else None,
                            allow_single=args.allow_single, dry_run=args.dry_run)
    except (ValueError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    print(format_summary(summary))
    return 0


if __name__ == '__main__':
    sys.exit(main())
