"""Automated GUI startup/trace/search check using generated, non-biological data.

This is a CI smoke check, not a claim of hands-on Windows validation. It opens
no user projects and performs no source-file writes.
"""
from __future__ import annotations

import json
from pathlib import Path
import platform
import random
import traceback

from Bio.Seq import Seq

from core import Project, Read, Trace, atomic_text


def synthetic_project():
    project = Project()
    generator = random.Random(7493)
    sequence = ''.join(generator.choice('ACGT') for _ in range(280))
    for rid, bases in (('sample_F', sequence[:200]),
                       ('sample_R', str(Seq(sequence[80:]).reverse_complement()))):
        trace = Trace(rid+'.ab1', bases, [40]*len(bases),
                      list(range(1, len(bases)+1)),
                      {base: [0]+[100 if b == base else 3 for b in bases]+[0]
                       for base in 'ACGT'}, 'synthetic-'+rid)
        project.traces[rid] = trace
        project.reads[rid] = Read(rid, trace.path, trace.sha256, 'sample',
                                  'test', rid[-1], 0, len(bases))
    key = project.assemble_reads(list(project.reads), 'Synthetic_contig')
    return project, key, sequence


def run_smoke_test(editor_class, report_path):
    report = {'status': 'failed', 'platform': platform.platform(),
              'python': platform.python_version(), 'data': 'generated synthetic reads'}
    output = Project().validate_output_path(report_path)
    app = None
    try:
        project, key, sequence = synthetic_project()
        # Validate even this internal diagnostic output against AB1 replacement.
        output = project.validate_output_path(report_path)
        app = editor_class(project, allow_recovery=False)
        app.show_contig(key)
        app.find_query.set(sequence[40:56])

        def finish():
            try:
                app.update_idletasks()
                assert app.project.contigs[key].sequence() == sequence
                assert app.find_results, 'The generated query was not found.'
                assert app.f_pane.canvas.find_all(), 'The forward trace did not draw.'
                assert app.r_pane.canvas.find_all(), 'The reverse trace did not draw.'
                report.update(status='passed', tk=str(app.tk.call('info', 'patchlevel')),
                              windowing_system=app.tk.call('tk', 'windowingsystem'),
                              contig_bp=len(sequence), search_hits=len(app.find_results))
            except Exception:
                report['error'] = traceback.format_exc()
            finally:
                app.closing = True
                app.destroy()

        app.after(750, finish)
        app.mainloop()
    except Exception:
        report['error'] = traceback.format_exc()
        if app is not None:
            try: app.destroy()
            except Exception: pass
    # This report is intentionally independent from recovery/project documents.
    atomic_text(output, json.dumps(report, indent=2)+'\n')
    return 0 if report['status'] == 'passed' else 1
