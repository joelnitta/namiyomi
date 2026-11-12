"""Filename-based grouping for automatic contig assembly."""
from collections import Counter
import re
from pathlib import Path


def unassembled_reads(project):
    assigned = {rid for contig in project.contigs.values() for rid in contig.read_ids()}
    return (read for rid, read in project.reads.items() if rid not in assigned)


def unassembled_groups(project):
    groups = {}
    for read in unassembled_reads(project):
        prefix = Path(read.path).stem.split('_', 1)[0]
        groups.setdefault(prefix, []).append(read.id)
    return groups


# Match explicit well tokens or a PREMIX suffix before interpreting a numeric
# sample ID. The suffix is a primer label, not a strand assignment.
_WELL = r'[A-H][0-9]{2}'
_ABI_FILENAMES = re.compile(
    rf'^{_WELL}_(?P<well_first>.+)$|^(?P<well_last>.+?)(?:_[0-9]{{3}})?_{_WELL}$|^(?P<premix>.+)-PREMIX$', re.I)
_ABI_SAMPLE = re.compile(
    r'(?P<sample>[0-9]+(?:-[0-9]+)*?)-?(?P<primer>470f|1610f|af3|cr3|rrfq|ar|af|bf|bg|cr|f|r|a|b|c)', re.I)


def abi_sample_groups(project):
    """Group recognized sample/primer names within each source folder."""
    candidates = {}
    for read in unassembled_reads(project):
        path = Path(read.path)
        filename = _ABI_FILENAMES.fullmatch(path.stem)
        if filename is None:
            continue
        token = next(value for value in filename.groups() if value is not None)
        match = _ABI_SAMPLE.fullmatch(token)
        if match is not None:
            sample, primer = match.groups()
            candidates.setdefault((path.parent, sample), []).append((read.id, primer.casefold()))

    # Separate folders can contain the same sample ID. Give each result its own
    # name, and leave reads without a different primer in their folder ungrouped.
    groups, seen = {}, Counter()
    for (_, sample), reads in candidates.items():
        if len({primer for _, primer in reads}) < 2:
            continue
        seen[sample] += 1
        name = sample if seen[sample] == 1 else f'{sample}_{seen[sample]}'
        groups[name] = [rid for rid, _ in reads]
    return groups


def fully_trimmed(read):
    return read.start == read.end and not read.insertions.get(str(read.end))


def assemble_groups(project, groups):
    made, errors = [], []
    for prefix, ids in groups.items():
        usable = [rid for rid in ids if not fully_trimmed(project.reads[rid])]
        if not usable:
            errors.append(f'{prefix}: No retained bases after trimming.')
            continue
        key = project.key(prefix, 'auto')
        index = 2
        while key in project.contigs:
            key = project.key(prefix, f'auto_{index}')
            index += 1
        base = re.sub(r'[\s>|\x00-\x1f]', '_', prefix)
        name, index = base, 2
        names = {contig.sequence_name for contig in project.contigs.values()}
        while name in names:
            name = f'{base}_{index}'
            index += 1
        try:
            made.append(project.assemble_reads(usable, name, key))
        except Exception as exc:
            errors.append(f'{prefix}: {exc}')
    return made, errors
