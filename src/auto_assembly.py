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


# Strip run decorations before parsing the sample and primer. Preserve
# separators inside sample IDs, such as the underscore in 660_2.
_WELL = r'[A-H][0-9]{2}'
_SEPARATOR = r'[_ -]'
_PREMIX = re.compile(rf'{_SEPARATOR}+PREMIX$', re.I)
_WELL_FIRST = re.compile(rf'^{_WELL}{_SEPARATOR}+', re.I)
_WELL_LAST = re.compile(rf'(?:{_SEPARATOR}+[0-9]{{3}})?{_SEPARATOR}+{_WELL}$', re.I)
_PRIMER = r'470f|1610f|af3|cr3|rrfq|ar|af|bf|bg|cr|f|r|a|b|c'
_ABI_SAMPLE = re.compile(
    rf'(?P<sample>[0-9]+(?:[-_][0-9]+)*?s?){_SEPARATOR}*(?P<primer>{_PRIMER})', re.I)
_RBCL_SAMPLE = re.compile(
    rf'(?P<sample>[A-Z0-9]+(?:[-_][A-Z0-9]+)*?){_SEPARATOR}+rbcL{_SEPARATOR}*(?P<primer>{_PRIMER})', re.I)


def _abi_sample_and_primer(stem):
    stem, premix = _PREMIX.subn('', stem)
    without_trailing_well, trailing_well = _WELL_LAST.subn('', stem)
    tagged = _RBCL_SAMPLE.fullmatch(without_trailing_well)
    if tagged:
        return tagged['sample'] + '_rbcL', tagged['primer'].casefold()
    stem, leading_well = _WELL_FIRST.subn('', without_trailing_well)
    numeric = _ABI_SAMPLE.fullmatch(stem)
    if numeric:
        primer = numeric['primer'].casefold()
        if premix or leading_well or trailing_well or primer in {'af', 'af3', 'bf', 'cr', 'cr3'}:
            return numeric['sample'], primer
    return None


def abi_sample_groups(project):
    """Group recognized sample/primer names within each source folder."""
    candidates = {}
    for read in unassembled_reads(project):
        path = Path(read.path)
        label = _abi_sample_and_primer(path.stem)
        if label is not None:
            sample, primer = label
            candidates.setdefault((path.parent, sample), []).append((read.id, primer))

    # Separate folders can contain the same sample ID. Give each result its own
    # name, and leave reads without a different primer in their folder ungrouped.
    groups, seen = {}, Counter()
    reserved = {sample for _, sample in candidates}
    for (_, sample), reads in candidates.items():
        if len({primer for _, primer in reads}) < 2:
            continue
        seen[sample] += 1
        name = sample
        if seen[sample] > 1:
            while f'{sample}_{seen[sample]}' in reserved:
                seen[sample] += 1
            name = f'{sample}_{seen[sample]}'
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
