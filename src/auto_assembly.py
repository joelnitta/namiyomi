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


def assemble_groups(project, groups):
    made, errors = [], []
    for prefix, ids in groups.items():
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
            made.append(project.assemble_reads(ids, name, key))
        except Exception as exc:
            errors.append(f'{prefix}: {exc}')
    return made, errors
