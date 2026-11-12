"""Read-only MCP quality summary for one explicitly selected project."""
import argparse
from collections import Counter
import json
import sys

from app_info import APP_VERSION
from auto_assembly import fully_trimmed
from batch import CLEAN, NEEDS_REVIEW, SINGLE_READ, triage
from core import Project

VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
MAX_ROWS = 50
TOOL = {
    'name': 'get_quality_summary',
    'description': 'Get anonymous Sanger quality metrics for the selected project. '
                   'Reports counts and at most 50 contigs in review priority order.',
    'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    'annotations': {'readOnlyHint': True, 'openWorldHint': False},
}


def quality_snapshot(project, made=(), failed=0):
    """Allowlist cloud fields; keep the full table and its names in the app."""
    rows = triage(project)
    counts = Counter(row['category'] for row in rows)
    assigned = {rid for contig in project.contigs.values() for rid in contig.read_ids()}
    used = {rid for key in made for rid in project.contigs[key].read_ids()} & project.reads.keys()
    unassembled = set(project.reads) - assigned
    summary = dict(reads=len(project.reads), reads_used=len(used), unassembled_reads=len(unassembled),
                   fully_trimmed_reads=sum(fully_trimmed(project.reads[rid]) for rid in unassembled),
                   unassembled_read_flags=sum(len(project.read_issues(rid)) for rid in unassembled),
                   contigs_total=len(rows), new_contigs=len(made), failed_groups=failed,
                   clean=counts[CLEAN], needs_review=counts[NEEDS_REVIEW],
                   single_read=counts[SINGLE_READ],
                   single_read_contigs=sum(r['status'] in ('single_read', '片方向') or len(r['reads']) == 1
                                           for r in rows),
                   review_flags=sum(len(r['issue_columns']) for r in rows),
                   flagged_contigs=sum(bool(r['issue_columns']) for r in rows),
                   stale_contigs=sum(r['stale'] for r in rows),
                   uncalled_bases=sum(r['n_calls'] for r in rows),
                   omitted_contigs=max(0, len(rows) - MAX_ROWS), contigs=[])
    ordered = sorted(enumerate(rows, 1), key=lambda item: (
        not bool(item[1]['issue_columns']),
        not item[1]['stale'], item[1]['category'] != NEEDS_REVIEW,
        item[1]['category'] != SINGLE_READ, -len(item[1]['issue_columns']), item[0]))
    local_rows = []
    for index, row in ordered:
        identifier = f'C{index:03}'
        metrics = dict(id=identifier, length_bp=row['length'],
            read_count=len(row['reads']), category=row['category'], stale=row['stale'],
            review_flags=len(row['issue_columns']), uncalled_bases=row['n_calls'],
            overlap_bp=row['overlap'], overlap_identity=row['identity'])
        if len(local_rows) < MAX_ROWS:
            summary['contigs'].append(metrics)
        local_rows.append(dict(metrics, key=row['key'], name=row['name']))
    return summary, local_rows


class QualityServer:
    """Small JSON-RPC server shared by the GUI and the stdio entry point."""

    def __init__(self, summary):
        self.summary = summary
        self.version = None
        self.ready = False

    def dispatch(self, message):
        def reply(**fields):
            return dict(jsonrpc='2.0', id=message.get('id'), **fields)

        def error(code, text):
            return reply(error=dict(code=code, message=text))

        if (not isinstance(message, dict) or message.get('jsonrpc') != '2.0'
                or not isinstance(message.get('method'), str)
                or ('id' in message and type(message['id']) not in (str, int))):
            return dict(jsonrpc='2.0', id=None, error=dict(code=-32600, message='Invalid request'))
        method = message['method']
        if 'id' not in message:
            if method == 'notifications/initialized' and self.version is not None:
                self.ready = True
            return None
        params = message.get('params', {})
        if not isinstance(params, dict):
            return error(-32602, 'Expected object params')
        if method == 'initialize' and self.version is None:
            version = params.get('protocolVersion')
            if not isinstance(version, str):
                return error(-32602, 'Missing protocol version')
            self.version = version if version in VERSIONS else VERSIONS[0]
            return reply(result=dict(protocolVersion=self.version, capabilities={'tools': {}},
                                     serverInfo=dict(name='namiyomi', version=APP_VERSION)))
        if method == 'ping':
            return reply(result={})
        if not self.ready:
            return error(-32000, 'Server has not initialized')
        if method == 'tools/list':
            return reply(result={'tools': [TOOL]})
        if method == 'tools/call':
            if params.get('name') != TOOL['name'] or params.get('arguments', {}) != {}:
                return error(-32602, 'Unknown tool or unexpected arguments')
            result = dict(content=[dict(type='text', text=json.dumps(self.summary, allow_nan=False))])
            if self.version in VERSIONS[:2]:
                result['structuredContent'] = self.summary
            return reply(result=result)
        return error(-32601, 'Method not found')


def read_quality(summary):
    """The GUI calls the same tool locally, without opening a listener."""
    dispatch = QualityServer(summary).dispatch

    def call(method, **params):
        return dispatch(dict(jsonrpc='2.0', id=1, method=method, params=params))['result']

    call('initialize', protocolVersion=VERSIONS[0], capabilities={},
         clientInfo=dict(name='namiyomi GUI', version=APP_VERSION))
    dispatch(dict(jsonrpc='2.0', method='notifications/initialized'))
    return call('tools/call', name=TOOL['name'], arguments={})['structuredContent']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, help='Saved namiyomi project to inspect')
    args = parser.parse_args()
    try:
        summary, _ = quality_snapshot(Project.load(args.project))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'Cannot load project: {exc}', file=sys.stderr)
        return 1
    server = QualityServer(summary)
    for line in sys.stdin.buffer:
        try:
            message = json.loads(line.decode('utf-8'))
        except (ValueError, RecursionError):
            response = dict(jsonrpc='2.0', id=None, error=dict(code=-32700, message='Parse error'))
        else:
            response = server.dispatch(message)
        if response is not None:
            # ASCII JSON is valid UTF-8 even on a non-UTF-8 Windows console.
            print(json.dumps(response, allow_nan=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
