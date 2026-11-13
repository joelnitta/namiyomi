"""Native Tk dialogs for the optional local assembly and MCP review workflow."""
from bisect import bisect_right
from copy import copy
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
import tkinter as tk
from tkinter import messagebox, ttk

from auto_assembly import assemble_groups, unassembled_groups
from auto_review import local_review, model_batches, model_review
from llm_review import (DEFAULT_EFFORT, EFFORTS, GEMINI, KEY_ENV, LANGUAGES, MODELS, OFFLINE,
                        PROVIDERS, WAVEFORM_WORKERS, ReviewError, ReviewPreferences, ReviewSettings,
                        format_report, list_models, test_connection)
from mcp_server import quality_snapshot
from review_progress import ReviewProgress

TITLE = 'Review Start'


def assemble_snapshot(project, groups, *, on_group_done=None):
    """Operate on a private project; only new contigs can be applied to the editor."""
    made, errors = assemble_groups(project, groups, on_group_done=on_group_done)
    summary, rows = quality_snapshot(project, made, len(errors))
    return {key: project.contigs[key] for key in made}, errors, summary, rows


def review_snapshot(project, groups, config, *, on_progress=None, is_cancelled=None):
    """Assemble, settle flagged columns with the local trace rules, then ask the model about the rest."""
    def report(stage, completed=0, total=1):
        if on_progress is not None:
            on_progress(stage, completed, total)

    made, errors, summary, rows = assemble_snapshot(
        project, groups, on_group_done=lambda done, total: report('assembly', done, total))
    if not rows:
        return made, errors, summary, rows, None

    report('screening')
    outcome, remaining = local_review(project)
    failure = None
    if config.provider != OFFLINE and remaining:
        report('approvals', 0, model_batches(remaining))
        try:
            model_review(project, remaining, config, outcome, is_cancelled=is_cancelled,
                         on_batch_done=lambda done, total, replied: report('approvals', done, total))
        except (ReviewError, ValueError) as exc:
            failure = str(exc)
    if is_cancelled is not None and is_cancelled():
        failure = failure or 'Review cancelled.'
    updated, updated_rows = quality_snapshot(project, made, len(errors))
    changes = {key: project.contigs[key] for key in outcome.changed}
    return {**made, **changes}, errors, dict(updated, review=outcome.summary()), updated_rows, failure


class McpDialogs:
    def __init__(self, editor):
        self.editor = editor
        self.preferences = ReviewPreferences()
        self.config = self.preferences.load()
        self.session_settings = {self.config.provider: self.config}
        self.window = None
        self.results_window = None

    def _open(self, title):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return None, None
        window, body = self.editor.panel(title)
        self.window = window
        window.bind('<Escape>', lambda _: window.destroy())
        return window, body

    def _center(self, window):
        window.update_idletasks()
        parent = self.editor
        x = max(0, parent.winfo_rootx() + (parent.winfo_width() - window.winfo_width()) // 2)
        y = max(28, parent.winfo_rooty() + (parent.winfo_height() - window.winfo_height()) // 2)
        window.geometry(f'+{x}+{y}')

    @staticmethod
    def _label(parent, text, *, strong=False, width=500):
        options = {'font': 'TkHeadingFont'} if strong else {}
        return ttk.Label(parent, text=text, wraplength=width, justify='left', **options)

    def _background(self, window, task, done, *, on_wait=None):
        results = Queue()

        def work():
            try:
                results.put((task(), None))
            except (ValueError, ReviewError) as exc:
                results.put((None, str(exc)))
            except Exception:
                results.put((None, 'An unexpected error interrupted this step.'))

        def poll():
            if not window.winfo_exists():
                return
            try:
                result, error = results.get_nowait()
            except Empty:
                if on_wait is not None:
                    on_wait()
                self.editor.after(70, poll)
            else:
                done(result, error)

        Thread(target=work, daemon=True).start()
        self.editor.after(70, poll)

    def settings(self):
        window, body = self._open('AI Review Settings')
        if window is None:
            return
        self._label(body, 'Choose where to review flags', strong=True).pack(anchor='w')
        self._label(body, 'Local trace rules run first. To review remaining columns with an online model, '
                    'choose a provider and enter an API key with access to that model.').pack(anchor='w', pady=(5, 12))
        form = ttk.Frame(body)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        provider = tk.StringVar(window, value=self.config.provider)
        model = tk.StringVar(window, value=self.config.model)
        key = tk.StringVar(window, value=self.config.key)
        language = tk.StringVar(window, value=self.config.language)
        effort = tk.StringVar(window, value=self.config.effort)
        notice = tk.StringVar(window)
        status = tk.StringVar(window)
        combo = ttk.Combobox(form, textvariable=provider, values=PROVIDERS, state='readonly', width=28)
        model_row = ttk.Frame(form)
        model_entry = ttk.Combobox(model_row, textvariable=model, width=23)
        model_entry.pack(side='left', fill='x', expand=True)
        key_entry = ttk.Entry(form, textvariable=key, show='•', width=33)
        language_box = ttk.Combobox(form, textvariable=language, values=LANGUAGES,
                                    state='readonly', width=15)
        effort_box = ttk.Combobox(form, textvariable=effort, state='readonly', width=15)
        for index, (label, widget) in enumerate((('Provider', combo), ('Model ID', model_row),
                                                  ('Reasoning effort', effort_box),
                                                  ('API key', key_entry), ('Report language', language_box))):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky='w', pady=4, padx=(0, 16))
            widget.grid(row=index, column=1, sticky='ew', pady=4)
        ttk.Label(body, textvariable=notice, wraplength=500, justify='left').pack(anchor='w', pady=(10, 6))
        ttk.Label(body, textvariable=status, wraplength=500, justify='left').pack(anchor='w', pady=(0, 8))
        ttk.Separator(body).pack(fill='x', pady=(0, 10))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x')

        def values():
            choice = provider.get()
            config = ReviewSettings(choice, model.get().strip() if choice != OFFLINE else '',
                                    language.get(), key.get().strip() if choice != OFFLINE else '', effort.get())
            config.validate()
            return config

        def connect(refresh=False):
            try:
                config = (ReviewSettings(provider.get(), MODELS[provider.get()], language.get(), key.get().strip())
                          if refresh else values())
                config.require_key()
            except ValueError as exc:
                status.set(str(exc))
                return
            combo.configure(state='disabled')
            for widget in (model_entry, key_entry, language_box, effort_box, apply_button, test_button, refresh_button):
                widget.configure(state='disabled')
            status.set('Loading the provider’s model list…' if refresh else
                       'Testing with a short message. No project data is sent…')

            def done(result, error):
                combo.configure(state='readonly')
                language_box.configure(state='readonly')
                effort_box.configure(state='readonly')
                for widget in (model_entry, key_entry, apply_button, test_button, refresh_button):
                    widget.configure(state='normal')
                if error:
                    status.set(error)
                elif refresh:
                    model_entry.configure(values=result)
                    status.set(f'{len(result)} model IDs loaded. Choose one or type an ID, then test the connection.'
                               if result else 'No matching models returned. You can still type a model ID.')
                else:
                    status.set('Connected. The selected model responded.')

            self._background(window, lambda: (list_models if refresh else test_connection)(config), done)

        def apply():
            try:
                config = values()
                if config.provider != OFFLINE:
                    config.require_key()
                self.preferences.save(config)
            except ValueError as exc:
                status.set(str(exc))
                return
            except OSError:
                status.set('Could not save settings. Check write access and try again.')
                return
            self.config = config
            self.session_settings[config.provider] = config
            window.destroy()
            self.editor.status.set('AI review settings applied.')

        refresh_button = ttk.Button(model_row, text='Refresh Models', command=lambda: connect(refresh=True))
        refresh_button.pack(side='right', padx=(6, 0))
        test_button = ttk.Button(buttons, text='Test Connection', command=connect)
        test_button.pack(side='left')
        apply_button = ttk.Button(buttons, text='Apply', command=apply)
        apply_button.pack(side='right')
        ttk.Button(buttons, text='Cancel', command=window.destroy).pack(side='right', padx=(0, 6))

        previous = self.config.provider
        drafts = {p: (c.model, c.key, c.effort) for p, c in self.session_settings.items()}

        def sync(event=None):
            nonlocal previous
            choice = provider.get()
            if event is not None:
                drafts[previous] = (model.get(), key.get(), effort.get())
                model_id, api_key, level = drafts.get(choice, (MODELS[choice], '', DEFAULT_EFFORT))
                model.set(model_id)
                key.set(api_key)
                effort.set(level)
                model_entry.configure(values=())
                status.set('')
                previous = choice
            offline = choice == OFFLINE
            effort_box.configure(values=EFFORTS[choice], state='disabled' if offline else 'readonly')
            for widget in (model_entry, key_entry, test_button, refresh_button):
                widget.configure(state='disabled' if offline else 'normal')
            notice.set('Checks flagged bases with local trace rules. No API key or internet connection is needed.' if offline else
                       f'Keys entered here last until the app closes. Leave blank to use {KEY_ENV[choice]}. '
                       'Effort levels vary by model; High read traces most accurately in our tests. '
                       'Test Connection checks the selected level and may incur API charges.')

        combo.bind('<<ComboboxSelected>>', sync)
        sync()
        combo.focus_set()
        self._center(window)

    def full_auto(self):
        app, config = self.editor, self.config
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return
        if self.results_window is not None and self.results_window.winfo_exists():
            self.results_window.deiconify()
            self.results_window.lift()
            return
        project = app.project
        if not project.reads and not project.contigs:
            messagebox.showinfo(TITLE, 'Import AB1 reads or open a project first.', parent=app)
            return
        if config.provider != OFFLINE:
            try:
                config.require_key()
            except ValueError:
                self.settings()
                return
        window, body = self._open(TITLE)
        groups = unassembled_groups(project)
        revision = app.revision
        self._label(body, 'Run automatic assembly and review', strong=True).pack(anchor='w')
        self._label(body, f'{len(project.reads)} reads  ·  {len(groups)} unassembled groups  ·  '
                    f'{len(project.contigs)} existing contigs').pack(anchor='w', pady=(5, 14))
        ttk.Separator(body).pack(fill='x')
        self._label(body, '1. Assemble unassigned reads on this Mac or PC', strong=True).pack(anchor='w', pady=(12, 4))
        self._label(body, 'Groups use the filename before the first underscore. Use this only if '
                    'each prefix is one specimen and one locus.'
                    if groups else 'All reads already belong to contigs. The report will use the current project.').pack(anchor='w')
        self._label(body, '2. Check flagged bases against the traces on this device', strong=True).pack(anchor='w', pady=(12, 4))
        self._label(body, 'Local rules settle a flagged base when one read is clean there or the peak is clear. '
                    'They can set a base or remove a column that the better read does not support. '
                    'Manual edits and IUPAC double-peak calls stay in place.').pack(anchor='w')
        if config.provider == OFFLINE:
            self._label(body, '3. No online review', strong=True).pack(anchor='w', pady=(12, 4))
            sharing = 'Columns the local rules cannot settle remain open for manual review. Nothing is sent over the internet.'
        else:
            self._label(body, f'3. Ask {config.provider} about the columns that remain', strong=True).pack(anchor='w', pady=(12, 4))
            sharing = (f'Send sampled trace signal, nearby base calls and Phred scores for those columns to '
                       f'{config.provider} ({config.model}), with anonymous read labels. File names and images stay on this device. '
                       'An answer is applied only when the model is confident and the signal supports it. '
                       'Red flags mark columns the model reviewed but did not settle. '
                       'All changes and the assembly can be undone together. API usage may be billed.')
            if config.provider == GEMINI:
                sharing += ' Google may use free-tier requests to improve its products.'
        self._label(body, sharing).pack(anchor='w')
        self._label(body, 'Review the results in namiyomi before exporting.', width=500).pack(anchor='w', pady=(10, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(16, 0))
        cancel = ttk.Button(buttons, text='Cancel', command=window.destroy)
        cancel.pack(side='right')
        ttk.Button(buttons, text='Review on This Device' if config.provider == OFFLINE else 'Review and Send Open Columns',
                   command=lambda: self._run(window, body, project, revision, groups, config)).pack(side='right', padx=6)
        cancel.focus_set()
        self._center(window)

    def _run(self, window, body, project, revision, groups, config):
        app = self.editor
        if app.project is not project or app.revision != revision:
            messagebox.showinfo(TITLE, 'The project changed. Open this command again to refresh the scope.', parent=window)
            window.destroy()
            return
        snapshot = copy(project)
        (snapshot.reads, snapshot.contigs, snapshot.settings, snapshot.trim_settings,
         snapshot.appearance, snapshot.find_settings) = project.snapshot()
        # Raw trace data are read-only and can be shared with the assembly worker.
        snapshot.traces = project.traces.copy()
        for widget in body.winfo_children():
            widget.destroy()
        japanese = config.language == 'Japanese'
        stages = {
            'assembly': 'リードをアセンブリ中…' if japanese else 'Assembling reads…',
            'screening': '波形ルールでフラッグを確認中…' if japanese else 'Checking flagged bases against the traces…',
            'approvals': 'AIが残りの波形を確認中…' if japanese else 'The model is reading the remaining traces…',
            'applying': '結果を反映中…' if japanese else 'Applying results…',
        }
        progress = tk.StringVar(window, value=stages['assembly'])
        percent = tk.IntVar(window, value=0)
        timing = tk.StringVar(window, value='0%')
        ttk.Label(body, textvariable=progress, width=62, wraplength=470).pack(anchor='w', pady=(8, 12))
        ttk.Progressbar(body, mode='determinate', maximum=100, variable=percent).pack(fill='x')
        ttk.Label(body, textvariable=timing).pack(anchor='w', pady=(5, 0))
        cancelled = Event()
        window.bind('<Destroy>', lambda event: cancelled.set() if event.widget is window else None, add='+')
        cancel = ttk.Button(body, text='Cancel', command=window.destroy)
        cancel.pack(anchor='e', pady=(12, 0))
        self._center(window)

        state = ReviewProgress(len(groups), config.provider != OFFLINE, config.effort, monotonic(),
                               parallel_requests=WAVEFORM_WORKERS)
        updates = Queue()

        def render(stage=None):
            while True:
                try:
                    update = updates.get_nowait()
                except Empty:
                    break
                state.update(*update)
            if stage is not None:
                state.update(stage, 0, 1, monotonic())
            value, remaining = state.estimate(monotonic())
            value = min(value, percent.get() + 8)
            percent.set(value)
            progress.set(stages[state.stage])
            if remaining is None:
                estimate = '応答待ち（残り時間は未確定）' if japanese else 'Waiting for response · remaining time unknown'
            elif japanese:
                minutes, seconds = divmod(remaining, 60)
                estimate = f'残り約{minutes}分{seconds}秒' if minutes else f'残り約{seconds}秒'
            else:
                minutes, seconds = divmod(remaining, 60)
                estimate = f'About {minutes}m {seconds:02}s remaining' if minutes else f'About {seconds}s remaining'
            timing.set(f'{value}%  ·  {estimate}')

        def finished(result, error):
            if error:
                progress.set(error)
                timing.set(f'{percent.get()}%')
                cancel.configure(text='Close')
                return
            if app.project is not project or app.revision != revision:
                progress.set('The project changed. No assembly result was applied.')
                timing.set(f'{percent.get()}%')
                cancel.configure(text='Close')
                return
            render('applying')
            contigs, errors, summary, rows, failure = result
            if contigs:
                entry = app.history_entry()
                project.contigs.update(contigs)
                project.dirty = True
                app.remember(entry)
                app.refresh_tree()
                app.show_contig(next(iter(contigs)))
                app.update_title()
                # Keep focus in the dialog while Tk's editor refreshes.
                window.focus_set()
            review = summary.get('review')
            app.status.set(f'Review: {summary["new_contigs"]} built | {len(errors)} groups failed'
                           + (f' | {review["local_resolved"] + review["model_resolved"]} of {review["sites"]} flags settled'
                              if review else ''))
            percent.set(100)
            progress.set('結果を表示します' if japanese else 'Results ready')
            timing.set('100%')

            def show_results():
                if window.winfo_exists():
                    self._results(window, body, project, config, summary, rows, failure)

            app.after(250, show_results)

        self._background(window, lambda: review_snapshot(
            snapshot, groups, config, on_progress=lambda *update: updates.put((*update, monotonic())),
            is_cancelled=cancelled.is_set),
            finished, on_wait=render)

    def _results(self, window, body, project, config, summary, rows, failure):
        app = self.editor
        for widget in body.winfo_children():
            widget.destroy()
        window.title('AI Review Report')
        window.grab_release()
        window.unbind('<Escape>')
        self.window = None
        self.results_window = window
        report = format_report(config, summary, failure=failure)
        report_label = self._label(body, report, width=545)
        report_label.pack(fill='x', pady=(0, 14) if rows else 0)
        table_frame = ttk.Frame(body)
        if rows:
            table_frame.pack(fill='both', expand=True)
        special_status = any(row['stale'] or row['category'] == 'single_read' for row in rows)
        columns = ('name', 'category', 'length', 'flags')
        visible_columns = columns if special_status else ('name', 'length', 'flags')
        table = ttk.Treeview(table_frame, show='headings', height=min(6, max(2, len(rows))), selectmode='browse',
                             columns=columns, displaycolumns=visible_columns)
        for col, name, width in (('name', 'Contig', 205), ('category', 'Status', 132),
                                 ('length', 'Length (bp)', 88), ('flags', 'Flags', 76)):
            table.heading(col, text=name)
            table.column(col, width=width, stretch=col == 'name', anchor='w' if col in ('name', 'category') else 'center')
        scroll = ttk.Scrollbar(table_frame, orient='vertical', command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        table.pack(side='left', fill='both', expand=True)
        keys = {}
        for row in rows:
            ident = row['id']
            keys[ident] = row['key']
            label = 'Stale' if row['stale'] else 'Single read' if row['category'] == 'single_read' else ''
            table.insert('', 'end', iid=ident,
                         values=(row['name'], label, row['length_bp'], row['review_flags']))
        if config.provider == OFFLINE:
            self._label(body, 'Reviewed on this device · no API request').pack(anchor='w', pady=(8, 0))
        last_contig = None
        last_red_flag = None

        def inspect(event=None):
            nonlocal last_contig, last_red_flag
            selected = (table.identify_row(event.y) if event is not None and event.type == tk.EventType.ButtonRelease
                        else next(iter(table.selection()), ''))
            if selected not in keys:
                return
            key = keys[selected]
            if app.project is not project or key not in project.contigs:
                messagebox.showinfo(TITLE, 'This contig is no longer in the current project.', parent=window)
                return
            app.show_contig(key)
            contig = project.contigs[key]
            flags = contig.issues()
            red_flags = [i for i in flags if contig.ai_unresolved(i)]
            previous = last_red_flag if key == last_contig else None
            last_contig, last_red_flag = key, None
            if red_flags:
                index = bisect_right(red_flags, previous) if previous is not None else 0
                last_red_flag = red_flags[index % len(red_flags)]
                app.selection = app.anchor = last_red_flag
            elif flags:
                app.selection = app.anchor = flags[0]
            if flags:
                app.ensure_visible()
                app.update_status()

        table.bind('<ButtonRelease-1>', inspect)
        table.bind('<Return>', inspect)
        if rows:
            first = table.get_children()[0]
            table.selection_set(first)
            table.focus(first)
            table.focus_set()
        self._center(window)
        window.geometry(f'{window.winfo_width()}x{window.winfo_height()}')
        window.minsize(500 if special_status else 390, 190 if rows else 100)
        window.resizable(True, True)

        def wrap_report(event):
            report_label.configure(wraplength=event.width)

        report_label.bind('<Configure>', wrap_report)
