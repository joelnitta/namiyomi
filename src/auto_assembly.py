"""The filename-based AUTO scope shared by the Assembly and MCP menus."""
import re
from pathlib import Path


def unassembled_groups(project):
    assigned = {rid for contig in project.contigs.values() for rid in contig.read_ids()}
    groups = {}
    for rid, read in project.reads.items():
        if rid not in assigned:
            prefix = Path(read.path).stem.split('_', 1)[0]
            groups.setdefault(prefix, []).append(rid)
    return groups


def fully_trimmed(read):
    return read.start == read.end and not read.insertions.get(str(read.end))


def assemble_groups(project, groups, *, on_group_done=None):
    made, errors = [], []
    for completed, (prefix, ids) in enumerate(groups.items()):
        if completed and on_group_done is not None:
            on_group_done(completed, len(groups))
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
    if groups and on_group_done is not None:
        on_group_done(len(groups), len(groups))
    return made, errors
