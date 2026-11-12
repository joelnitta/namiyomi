"""Native Tk dialogs for the optional local assembly and MCP review workflow."""
from copy import copy
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from auto_assembly import assemble_groups, unassembled_groups
from llm_review import (GEMINI, KEY_ENV, LANGUAGES, MODELS, OFFLINE, PROVIDERS, ReviewError,
                        ReviewPreferences, ReviewSettings, format_report, list_models, ordered_contigs,
                        prioritize_contigs, test_connection)
from mcp_server import quality_snapshot, read_quality

TITLE = 'Full auto mode by LLM'


def assemble_snapshot(project, groups):
    """Operate on a private project; only new contigs can be applied to the editor."""
    made, errors = assemble_groups(project, groups)
    summary, rows = quality_snapshot(project, made, len(errors))
    return {key: project.contigs[key] for key in made}, errors, read_quality(summary), rows


class McpDialogs:
    def __init__(self, editor):
        self.editor = editor
        self.preferences = ReviewPreferences()
        self.config = self.preferences.load()
        self.session_settings = {self.config.provider: self.config}
        self.window = None

    def _open(self, title):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return None, None
        self.window, body = self.editor.panel(title)
        self.window.bind('<Escape>', lambda _: self.window.destroy())
        return self.window, body

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

    def _background(self, window, task, done):
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
                self.editor.after(70, poll)
            else:
                done(result, error)

        Thread(target=work, daemon=True).start()
        self.editor.after(70, poll)

    def settings(self):
        window, body = self._open('MCP Settings')
        if window is None:
            return
        self._label(body, 'Connect a review provider', strong=True).pack(anchor='w')
        self._label(body, 'Choose which service writes the quality report. Online providers '
                    'require an API key with access to the selected model.').pack(anchor='w', pady=(5, 12))
        form = ttk.Frame(body)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        provider = tk.StringVar(window, value=self.config.provider)
        model = tk.StringVar(window, value=self.config.model)
        key = tk.StringVar(window, value=self.config.key)
        language = tk.StringVar(window, value=self.config.language)
        notice = tk.StringVar(window)
        status = tk.StringVar(window)
        combo = ttk.Combobox(form, textvariable=provider, values=PROVIDERS, state='readonly', width=28)
        model_row = ttk.Frame(form)
        model_entry = ttk.Combobox(model_row, textvariable=model, width=23)
        model_entry.pack(side='left', fill='x', expand=True)
        key_entry = ttk.Entry(form, textvariable=key, show='•', width=33)
        language_box = ttk.Combobox(form, textvariable=language, values=LANGUAGES,
                                    state='readonly', width=15)
        for index, (label, widget) in enumerate((('Provider', combo), ('Model ID', model_row),
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
                                    language.get(), key.get().strip() if choice != OFFLINE else '')
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
            for widget in (model_entry, key_entry, language_box, apply_button, test_button, refresh_button):
                widget.configure(state='disabled')
            status.set('Loading the provider’s model list…' if refresh else
                       'Testing with a short message. No project data is sent…')

            def done(result, error):
                combo.configure(state='readonly')
                language_box.configure(state='readonly')
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
            self.editor.status.set('MCP settings applied.')

        refresh_button = ttk.Button(model_row, text='Refresh Models', command=lambda: connect(refresh=True))
        refresh_button.pack(side='right', padx=(6, 0))
        test_button = ttk.Button(buttons, text='Test Connection', command=connect)
        test_button.pack(side='left')
        apply_button = ttk.Button(buttons, text='Apply', command=apply)
        apply_button.pack(side='right')
        ttk.Button(buttons, text='Cancel', command=window.destroy).pack(side='right', padx=(0, 6))

        previous = self.config.provider
        drafts = {p: (c.model, c.key) for p, c in self.session_settings.items()}

        def sync(event=None):
            nonlocal previous
            choice = provider.get()
            if event is not None:
                drafts[previous] = (model.get(), key.get())
                model_id, api_key = drafts.get(choice, (MODELS[choice], ''))
                model.set(model_id)
                key.set(api_key)
                model_entry.configure(values=())
                status.set('')
                previous = choice
            offline = choice == OFFLINE
            for widget in (model_entry, key_entry, test_button, refresh_button):
                widget.configure(state='disabled' if offline else 'normal')
            notice.set('Uses a built-in report. No API key or internet connection is needed.' if offline else
                       f'Keys entered here last until the app closes. Leave blank to use {KEY_ENV[choice]}. '
                       'A connection test may incur API charges.')

        combo.bind('<<ComboboxSelected>>', sync)
        sync()
        combo.focus_set()
        self._center(window)

    def full_auto(self):
        app, config = self.editor, self.config
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
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
                    'each prefix is one specimen and one locus. Existing contigs keep their edits and flags.'
                    if groups else 'All reads already belong to contigs. The report will use the current project.').pack(anchor='w')
        self._label(body, f'2. Review quality with {config.provider}', strong=True).pack(anchor='w', pady=(12, 4))
        if config.provider == OFFLINE:
            sharing = 'Use a built-in report to try the workflow. Nothing is sent over the internet.'
        else:
            sharing = (f'Send quality counts and anonymous contig IDs to {config.provider}. '
                       'Sequences, traces, names and paths stay on this device. API usage may be billed.')
            if config.provider == GEMINI:
                sharing += ' Google may use free-tier requests to improve its products.'
        self._label(body, sharing).pack(anchor='w')
        self._label(body, 'Review the results in namiyomi before exporting.', width=500).pack(anchor='w', pady=(10, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(16, 0))
        cancel = ttk.Button(buttons, text='Cancel', command=window.destroy)
        cancel.pack(side='right')
        ttk.Button(buttons, text='OK',
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
        self._label(body, 'Full auto analysis', strong=True).pack(anchor='w')
        progress = tk.StringVar(window, value='Assembling and checking quality on this device…')
        ttk.Label(body, textvariable=progress, width=62, wraplength=470).pack(anchor='w', pady=(8, 12))
        bar = ttk.Progressbar(body, mode='indeterminate')
        bar.pack(fill='x')
        bar.start(14)
        ttk.Button(body, text='Close', command=window.destroy).pack(anchor='e', pady=(12, 0))
        self._center(window)

        def assembled(result, error):
            if error:
                bar.stop()
                progress.set(error)
                return
            if app.project is not project or app.revision != revision:
                bar.stop()
                progress.set('The project changed. No assembly result was applied.')
                return
            contigs, errors, summary, rows = result
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
            result_revision = app.revision
            app.status.set(f'Full auto: {len(contigs)} built | {len(errors)} groups failed')
            if config.provider == OFFLINE or not summary['contigs_total']:
                self._results(window, body, project, result_revision, config, summary, rows, errors, (), None)
                return
            progress.set(f'Checking review priorities with {config.provider}…')
            self._background(window, lambda: prioritize_contigs(config, summary),
                             lambda priority, failure: self._results(window, body, project, result_revision,
                                                                      config, summary, rows, errors,
                                                                      priority or (), failure))

        self._background(window, lambda: assemble_snapshot(snapshot, groups), assembled)

    def _results(self, window, body, project, revision, config, summary, rows, errors, priority, failure):
        app = self.editor
        for widget in body.winfo_children():
            widget.destroy()
        window.title('Full auto analysis · Results')
        self._label(body, 'Analysis results', strong=True).pack(anchor='w')
        report = format_report(config, summary, priority, failure=bool(failure))
        self._label(body, report, width=545).pack(anchor='w', pady=(8, 14))
        table_frame = ttk.Frame(body)
        if rows:
            table_frame.pack(fill='x')
        table = ttk.Treeview(table_frame, show='headings', height=min(6, max(2, len(rows))), selectmode='browse',
                             columns=('id', 'name', 'category', 'length', 'flags'))
        for col, name, width in (('id', 'ID', 55), ('name', 'Contig', 205), ('category', 'Status', 132),
                                 ('length', 'Length (bp)', 88), ('flags', 'Flags', 54)):
            table.heading(col, text=name)
            table.column(col, width=width, stretch=col == 'name', anchor='w' if col in ('name', 'category') else 'center')
        scroll = ttk.Scrollbar(table_frame, orient='vertical', command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        table.pack(side='left', fill='x', expand=True)
        keys = {}
        for row in ordered_contigs(rows, priority):
            ident = row['id']
            keys[ident] = row['key']
            label = {'clean': 'No flags', 'needs_review': 'Needs review', 'single_read': 'Single read'}[row['category']]
            table.insert('', 'end', iid=ident, values=(ident, row['name'],
                         'Stale' if row['stale'] else label, row['length_bp'], row['review_flags']))
        if config.provider == OFFLINE:
            self._label(body, 'Offline preview · no API request').pack(anchor='w', pady=(8, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(10, 0))

        def inspect(event=None):
            if not table.selection():
                return
            if app.project is not project or app.revision != revision:
                messagebox.showinfo(TITLE, 'The project changed after this report was created.', parent=window)
                return
            key = keys[table.selection()[0]]
            window.destroy()
            app.show_contig(key)
            flags = project.contigs[key].issues()
            if flags:
                app.selection = app.anchor = flags[0]
                app.ensure_visible()
                app.update_status()

        def details():
            sections = [failure] if failure else []
            if errors:
                groups = '\n'.join(errors[:12])
                if len(errors) > 12:
                    groups += f'\n… {len(errors) - 12} more'
                sections.append('Groups not assembled:\n' + groups)
            messagebox.showinfo('Analysis details', '\n\n'.join(sections), parent=window)

        ttk.Button(buttons, text='Copy Report', command=lambda: app.put_clipboard(report, 'Copied quality report.')).pack(side='left')
        if failure or errors:
            ttk.Button(buttons, text='Details…', command=details).pack(side='left', padx=6)
        view = ttk.Button(buttons, text='Open Contig', command=inspect, state='disabled')
        if rows:
            view.pack(side='left', padx=6)
        table.bind('<<TreeviewSelect>>', lambda _: view.configure(state='normal' if table.selection() else 'disabled'))
        table.bind('<Double-1>', inspect)
        table.bind('<Return>', inspect)
        close = ttk.Button(buttons, text='Close', command=window.destroy)
        close.pack(side='right')
        if table.get_children():
            table.selection_set(table.get_children()[0])
        close.focus_set()
        self._center(window)
