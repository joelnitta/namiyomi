"""Exercise startup, traces, and local AI Review with synthetic data.

This CI check exercises the Tk workflow without opening user files or calling a
provider. It is independent of the manual checks on a physical Windows PC.
"""
from __future__ import annotations

import json
from pathlib import Path
import platform
import random
import traceback
from tkinter import ttk

from Bio.Seq import Seq

from core import Project, Read, Trace, atomic_text
from llm_review import OFFLINE, ReviewSettings
from waveform_review import WaveformSite, waveform_evidence


RESOLVABLE = 122
UNRESOLVED = 158


def _add_pair(project, prefix, sequence, *, review_fixture=False):
    """Add opposite-strand reads with spaced synthetic fluorescence peaks."""
    for direction, bases in (('F', sequence[:200]),
                             ('R', str(Seq(sequence[80:]).reverse_complement()))):
        qualities = [50] * len(bases)
        if review_fixture and direction == 'R':
            calls = list(bases)
            for position in (RESOLVABLE, UNRESOLVED):
                raw = len(sequence) - 1 - position
                calls[raw] = 'A' if calls[raw] != 'A' else 'C'
            raw = len(sequence) - 1 - RESOLVABLE
            qualities[raw - 3:raw + 4] = [14] * 7
            bases = ''.join(calls)

        rid = f'{prefix}_{direction}'
        peaks = [24 + 12 * i for i in range(len(bases))]
        channels = {base: [0] * (peaks[-1] + 25) for base in 'ACGT'}
        for peak, base in zip(peaks, bases):
            channels[base][peak - 4:peak + 5] = [20, 100, 350, 800, 1000, 800, 350, 100, 20]
        trace = Trace(rid + '.ab1', bases, qualities, peaks, channels, 'synthetic-' + rid)
        project.traces[rid] = trace
        project.reads[rid] = Read(rid, trace.path, trace.sha256, prefix,
                                  'test', direction, 0, len(bases))


def synthetic_project():
    project = Project()
    generator = random.Random(7493)
    sequence = ''.join(generator.choice('ACGT') for _ in range(280))
    _add_pair(project, 'sample', sequence)
    key = project.assemble_reads(list(project.reads), 'Synthetic_contig')
    return project, key, sequence


def _widgets(parent, widget_type):
    for child in parent.winfo_children():
        if isinstance(child, widget_type):
            yield child
        yield from _widgets(child, widget_type)


def _invoke_review_start(app):
    menubar = app.nametowidget(app.cget('menu'))
    for index in range(menubar.index('end') + 1):
        if menubar.type(index) != 'cascade' or menubar.entrycget(index, 'label') != 'AI Review':
            continue
        menu = menubar.nametowidget(menubar.entrycget(index, 'menu'))
        for item in range(menu.index('end') + 1):
            if menu.entrycget(item, 'label') == 'Review Start…':
                menu.invoke(item)
                return
    raise AssertionError('AI Review > Review Start is missing.')


def run_smoke_test(editor_class, report_path):
    report = {'status': 'failed', 'platform': platform.platform(),
              'python': platform.python_version(), 'data': 'generated synthetic reads'}
    output = Project().validate_output_path(report_path)
    app = None
    try:
        project, key, sequence = synthetic_project()
        _add_pair(project, 'review', sequence, review_fixture=True)
        output = project.validate_output_path(report_path)
        app = editor_class(project, allow_recovery=False)
        # This check always takes the same offline workflow, regardless of any
        # saved provider configuration or credentials in the environment.
        app.mcp.config = ReviewSettings(provider=OFFLINE, model='', language='English')
        app.show_contig(key)
        app.find_query.set(sequence[40:56])

        def close():
            app.closing = True
            app.destroy()

        def check(step):
            try:
                step()
            except Exception:
                report['error'] = traceback.format_exc()
                close()

        def start_review():
            app.update_idletasks()
            assert app.project.contigs[key].sequence() == sequence
            assert app.find_results, 'The generated query was not found.'
            assert app.f_pane.canvas.find_all(), 'The forward trace did not draw.'
            assert app.r_pane.canvas.find_all(), 'The reverse trace did not draw.'
            evidence = waveform_evidence(project, WaveformSite(key, len(sequence) // 2))
            assert 'Read 1' in evidence and 'Read 2' in evidence and 'scans per base' in evidence
            report.update(tk=str(app.tk.call('info', 'patchlevel')),
                          windowing_system=app.tk.call('tk', 'windowingsystem'),
                          contig_bp=len(sequence), search_hits=len(app.find_results),
                          trace_review_evidence=True)
            _invoke_review_start(app)
            app.after_idle(lambda: check(confirm_review))

        def confirm_review():
            window = app.mcp.window
            assert window and window.winfo_exists(), 'The Review Start window did not open.'
            action = next((button for button in _widgets(window, ttk.Button)
                           if button.cget('text') == 'Review on This Device'), None)
            assert action is not None, 'The local-review confirmation is missing.'
            action.invoke()
            app.after(50, lambda: check(wait_for_results))

        def wait_for_results():
            window = app.mcp.results_window
            if window is None:
                app.after(50, lambda: check(wait_for_results))
                return
            assert window.winfo_exists() and window.title() == 'AI Review Report'
            assert window.grab_current() is None, 'The report blocks editing in the main window.'
            assert all(window.resizable()), 'The review report cannot be resized.'
            app.update_idletasks()
            width = window.winfo_width()
            window.geometry(f'{width + 72}x{window.winfo_height() + 48}')
            app.after(100, lambda: check(lambda: check_report(window, width)))

        def check_report(window, previous_width):
            assert window.winfo_width() > previous_width, 'The report did not grow when resized.'
            table = next(_widgets(window, ttk.Treeview))
            row = next(item for item in table.get_children() if table.set(item, 'name') == 'review')
            review_key = next(k for k, contig in project.contigs.items() if contig.sequence_name == 'review')
            contig = project.contigs[review_key]
            assert contig.call(RESOLVABLE) == sequence[RESOLVABLE], 'The local rule did not settle the flagged call.'
            assert contig.ai_notes[str(RESOLVABLE)].startswith('local rule:')
            assert contig.flag_color(RESOLVABLE) == 'black', 'Settled local review lost its historical flag.'
            assert contig.issues() == [UNRESOLVED], contig.issues()
            assert contig.flag_color(UNRESOLVED) == 'black', 'An unreviewed call was colored red.'
            assert str(table.set(row, 'flags')) == '1', 'The report has the wrong number of open flags.'
            table.see(row)
            app.update_idletasks()
            x, y, width, height = table.bbox(row, 'name')
            target = dict(x=x + width // 2, y=y + height // 2)
            table.event_generate('<ButtonPress-1>', **target)
            table.event_generate('<ButtonRelease-1>', **target)
            assert (app.group_key, app.selection) == (review_key, UNRESOLVED), 'The result row did not open its flag.'
            assert window.winfo_exists(), 'The report closed during navigation.'

            app.edit_base(sequence[UNRESOLVED])
            assert project.contigs[review_key].sequence() == sequence
            app.undo()
            assert project.contigs[review_key].issues() == [UNRESOLVED], 'Undo did not restore the manual edit.'
            app.undo()
            assert review_key not in project.contigs, 'Review Undo did not remove the new contig.'
            app.redo()
            assert project.contigs[review_key].ai_notes == contig.ai_notes, 'Redo did not restore the review.'
            app.redo()
            assert project.contigs[review_key].sequence() == sequence
            assert window.winfo_exists(), 'The report closed during editing.'
            report.update(status='passed', local_review_sites=2, local_review_resolved=1,
                          review_report_resize=True, review_flag_navigation=True, review_undo_redo=True)
            close()

        def timeout():
            report['error'] = 'Timed out waiting for the synthetic local Review Start workflow.'
            close()

        app.after(750, lambda: check(start_review))
        app.after(15000, timeout)
        app.mainloop()
    except Exception:
        report['error'] = traceback.format_exc()
        if app is not None:
            try:
                app.destroy()
            except Exception:
                pass
    # This report is intentionally independent from recovery/project documents.
    atomic_text(output, json.dumps(report, indent=2) + '\n')
    return 0 if report['status'] == 'passed' else 1
