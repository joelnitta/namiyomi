"""Small native Tk editor: reads, aligned chromatograms, consensus, FASTA."""
from __future__ import annotations
import argparse
import bisect
import copy
import json
import sys
import re
import os
from datetime import datetime,timedelta,timezone
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from tkinterdnd2 import DND_FILES, COPY, REFUSE_DROP, TkinterDnD

from core import (ALPHABET, COMPLEMENT, Base, Project, ScopedSnapshot, atomic_text, base_text,
                  bases_for_read, classify_import_paths, fasta_text, validate_read, validate_settings,
                  validate_trim_settings,validate_appearance)
from assembly import reverse_bases
import native_menus
from trace_plot import trace_samples
from app_info import APP_NAME, APP_VERSION, DEVELOPER, LOCATION
from sequence_find import find_matches,consensus_coordinates,ordered_entries,validate_find_settings
from preferences import ConfirmationPreferences
from platform_support import context_menu_bindings,menu_accelerator,shortcut_label,resource_root

COLORS={'A':'#008234','C':'#1559c5','G':'#161616','T':'#d1272c'}
MONO_FAMILY='Consolas' if sys.platform=='win32' else 'Menlo'
UI_FAMILY='Segoe UI' if sys.platform=='win32' else 'Helvetica'
MONO=(MONO_FAMILY,12)


def default_filename(extension,now=None):
    # Preserve the requested Japan-date prefix without requiring an IANA tzdata
    # install (Windows Python does not include that database by default).
    now=now or datetime.now(timezone(timedelta(hours=9)))
    return now.strftime('%Y%m%d_')+'.'+extension.lstrip('.')


class TracePane(ttk.Frame):
    def __init__(self,parent,app,track):
        super().__init__(parent)
        self.app=app;self.track=track;self.read_id=None;self.mapping=[];self.reverse=False
        self.label=ttk.Label(self,text='',anchor='w',padding=(6,2))
        self.label.pack(fill='x')
        self.canvas=tk.Canvas(self,bg='white',height=218,highlightthickness=1,highlightbackground='#b8b8b8',highlightcolor='#7597bc',takefocus=True)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Button-1>',lambda e:app.select_at(e,self.track))
        self.canvas.bind('<B1-Motion>',lambda e:app.drag_at(e,self.track))
        self.canvas.bind('<Configure>',lambda e:app.schedule_draw())
        self.canvas.bind('<MouseWheel>',app.wheel)
        self.canvas.bind('<FocusIn>',lambda e:app.set_active_track(self.track))

    def draw(self):
        c=self.canvas;c.delete('all');app=self.app
        width,height=c.winfo_width(),c.winfo_height()
        if not self.read_id:
            c.create_text(15,35,anchor='w',text='No read',fill='#777')
            return
        read=app.project.reads[self.read_id];trace=app.project.traces[self.read_id]
        self.label.config(text=read.sequence_name)
        left=app.offset;right=min(len(self.mapping),left+app.visible_count())
        selection=sorted((app.selection,app.anchor))
        samples=trace_samples(trace,self.mapping,left,right,app.cell,self.reverse)
        sample_max=max((values[sample] for values in trace.channels.values() for x,sample in samples),default=1)
        sample_max=max(1,sample_max)
        covered=[i for i,b in enumerate(self.mapping) if b is not None and b.raw is not None]
        top,baseline=51,max(80,height-57)
        for col in range(left,right):
            x=app.x(col);b=self.mapping[col]
            if selection[0]<=col<=selection[1]:
                c.create_rectangle(x-app.cell/2,0,x+app.cell/2,height,fill='#dbe7f6',outline='')
            if b is None:
                if covered and covered[0]<=col<=covered[-1]:
                    c.create_text(x,12,text='-',font=app.base_font(),fill='#777')
                continue
            excluded=(b.raw is not None and not read.start<=b.raw<read.end) or b.base=='-'
            if excluded:
                c.create_rectangle(x-app.cell/2,0,x+app.cell/2,height,fill='#e4e4e4',outline='')
            c.create_text(x,12,text=b.base or '-',font=app.base_font(),fill=app.base_colors().get(b.base,'#555'))
            if b.manual:
                c.create_line(x-5,22,x+5,22,fill='#b66b16',width=2)
            if b.raw is None:
                c.create_text(x,34,text='edit',font=(UI_FAMILY,8),fill='#9b621e')
                continue
            raw=b.raw
            if (raw+1)%10==0 or col==left:
                c.create_text(x,34,text=str(raw+1),font=(MONO_FAMILY,8),fill='#555')
            # These are instrument Phred scores at the original raw base, not
            # recomputed confidence for an edited consensus call.
            q=trace.qualities[raw]
            if q is not None:
                bar=min(60,q)/60*32
                color='#dddddd' if excluded else ('#666666' if q>=app.project.settings['min_quality'] else '#b9b9b9')
                c.create_rectangle(x-3,height-10-bar,x+3,height-10,fill=color,outline='')
            if app.view_kind=='read' and app.is_marked(col):
                c.create_rectangle(x-4,23,x+4,27,fill='black',outline='')
        for channel,values in trace.channels.items():
            points=[]
            for x,sample in samples:
                y=baseline-min(1,values[sample]/sample_max*app.trace_gain)*(baseline-top)
                points.extend((x,y))
            if len(points)>=4:
                display_channel=COMPLEMENT[channel] if self.reverse else channel
                c.create_line(*points,fill=app.base_colors()[display_channel],width=1.2)
        qy=height-10-app.project.settings['min_quality']/60*32
        c.create_line(62,qy,width,qy,fill='#b7b7b7',dash=(2,3))
        c.create_text(6,height-28,anchor='w',text='Phred Q',font=(UI_FAMILY,9),fill='black')
        c.create_text(6,qy,anchor='w',text=f'Q{app.project.settings["min_quality"]}',font=(UI_FAMILY,8),fill='black')
        c.create_line(62,baseline,width,baseline,fill='#ddd')


class Editor(TkinterDnD.Tk):
    def __init__(self,project=None,allow_recovery=True):
        super().__init__()
        if sys.platform=='win32':
            icon=resource_root()/'assets'/'namiyomi.ico'
            if icon.is_file():self.iconbitmap(default=str(icon))
        self.title(f'{APP_NAME} ver {APP_VERSION}');self.geometry('1250x810');self.minsize(1000,650)
        self.project=project or Project();self.undo_stack=[];self.redo_stack=[]
        self.view_kind='read';self.read_id=None;self.group_key=None
        self.selection=0;self.anchor=0;self.offset=0;self.cell=19
        self.trace_gain=1.0
        self.active_track='F';self.draw_job=None;self.busy=False
        self.closing=False
        self._ignore_tree=False
        self.read_sort_reverse=None
        self.sort_column=None;self.sort_reverse=False
        self.find_query=tk.StringVar();self.find_count=tk.StringVar(value='')
        self.find_job=None;self.find_cache=None;self.find_results=[];self.find_index=-1;self.find_map=()
        self.status=tk.StringVar(value='')
        self.confirmations=ConfirmationPreferences()
        self.revision=0
        from recovery import RecoveryStore
        self.recovery=RecoveryStore()
        self.meta_sample=tk.StringVar();self.meta_marker=tk.StringVar();self.meta_direction=tk.StringVar()
        self.trim_left=tk.StringVar();self.trim_right=tk.StringVar()
        self.heading=tk.StringVar(value='')
        if sys.platform=='darwin':self.createcommand('::tk::mac::ShowPreferences',self.preferences_dialog)
        self.make_menu();self.make_widgets();self.bind_keys()
        native_menus.configure();self.after_idle(native_menus.prune)
        self.register_drop_targets()
        if sys.platform=='darwin':self.createcommand('::tk::mac::OpenDocument',lambda *paths:self.accept_paths(paths))
        if sys.platform=='darwin':self.createcommand('tk::mac::Quit',self.close_project)
        self.protocol('WM_DELETE_WINDOW',self.close_project)
        self.refresh_tree();self.refresh_view()
        self.after(30000,self.autosave_recovery)
        # Aqua cannot present a native sheet before its parent NSWindow exists.
        if allow_recovery:self.after(500,self.offer_recovery)

    def make_menu(self):
        bar=tk.Menu(self)
        menus=[('File',[
            ('New Project',self.new_project,'⌘N'),('Import AB1…',self.import_files,'⌘I'),
            ('Import Previous Project…',self.open_project,'⌘O'),('Save Project',self.save_project,'⌘S'),
            ('Save Project As…',self.save_project_as,'⇧⌘S'),('Export Project Copy…',lambda:self.save_project(True),''),('Export FASTA…',self.export_current,'⇧⌘F'),('Recover Unsaved Project…',self.recover_project,'')]),
          ('Edit',[('Undo',self.undo,'⌘Z'),('Redo',self.redo,'⇧⌘Z'),
            ('Copy Sequence',self.copy_sequence,'⌘C'),('Copy FASTA',self.copy_fasta,'⇧⌘C'),
            ('Find Sequence',self.focus_find,'⌘F'),('Find Next',self.find_next,'⌘G'),('Find Previous',lambda:self.find_next(-1),'⇧⌘G'),
            ('Rename…',self.rename_selected,'F2'),('Delete Bases',self.delete_base,'Backspace'),
            ('Insert Bases…',self.insert_base,''),('Reverse Complement',self.reverse_complement,''),
            ('Cut Left',lambda:self.cut('L'),'⌘L'),('Cut Right',lambda:self.cut('R'),'⌘R')]),
          ('Review',[('Next Flag',self.next_issue,'⌘E'),('Previous Flag',lambda:self.next_issue(-1),'⇧⌘E'),('Mark Reviewed',self.mark_reviewed,'⌘Return'),('Toggle Review Flag',self.toggle_mark,'⌘M')]),
          ('View',[('First Read',lambda:self.open_read_direction('F'),'⌘1'),('Second Read',lambda:self.open_read_direction('R'),'⌘2'),('Contig',self.open_contig_view,'⌘3')]),
          ('Assembly',[('Build Contig (manual)',self.build_manual,'⌘B'),('Build Contig (AUTO)',self.build_auto,''),('Return Reads to AB1',self.return_reads_to_ab1,''),('Unify Contig Orientation',self.unify_orientations,'')]),
          ('Settings',[('Trimming Settings…',self.trimming_dialog,''),('Assembly Settings…',self.settings_dialog,''),('Find Settings…',self.find_settings_dialog,''),('Appearance…',self.appearance_dialog,''),('Confirmations…',self.confirmations_dialog,'')]),
          ('Help',[('How To',self.help_dialog,''),('Developer',self.developer_dialog,''),('License and Citation',self.license_dialog,'')])]
        for title,items in menus:
            menu=tk.Menu(bar,tearoff=False,postcommand=native_menus.prune)
            for label,command,accel in items:
                # Aqua parses these as native key equivalents, not just labels.
                # Unicode shortcut glyphs look correct but are not Tk's syntax.
                accel=menu_accelerator(accel)
                menu.add_command(label=label,command=Editor.menu_action(self,command),accelerator=accel)
            bar.add_cascade(label=title,menu=menu)
        self.config(menu=bar)

    def menu_action(self,command):
        # Return from AppKit's menu invocation before opening a modal Tk window.
        # A nested tkwait inside an AX/native menu callback stalls macOS UI work.
        return lambda:self.after_idle(command)

    def make_widgets(self):
        self.setting_vars={}
        for key,scale in [('min_quality',1),('min_overlap',1),('min_identity',100)]:
            var=tk.StringVar(value=f'{self.project.settings[key]*scale:g}')
            self.setting_vars[key]=(var,scale)
        self.status_label=ttk.Label(self,textvariable=self.status,anchor='w',padding=(6,3),foreground='black')
        self.status_label.pack(side='bottom',fill='x')
        self.status_label.bind('<Configure>',lambda e:self.status_label.configure(wraplength=max(200,e.width-12)))
        panes=ttk.Panedwindow(self,orient='horizontal');panes.pack(fill='both',expand=True)
        sidebar=ttk.Frame(panes);panes.add(sidebar,weight=0)
        self.tree=ttk.Treeview(sidebar,columns=('length',),show='tree headings',selectmode='extended',height=25)
        self.tree.heading('#0',text='Sequences');self.tree.column('#0',width=210,minwidth=130)
        self.tree.heading('length',text='Length (bp)');self.tree.column('length',width=80,minwidth=80,stretch=False,anchor='e')
        scroll=ttk.Scrollbar(sidebar,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.tree_select)
        self.tree.bind('<Double-1>',self.tree_double_click)
        for sequence in context_menu_bindings():
            self.tree.bind(sequence,self.tree_context_menu)
        self.tree.bind('<Shift-F10>',self.tree_keyboard_context_menu)
        self.tree.bind('<Shift-Up>',lambda e:self.extend_read_selection(-1))
        self.tree.bind('<Shift-Down>',lambda e:self.extend_read_selection(1))
        for sequence in ('<Button-1>','<Up>','<Down>'):
            self.tree.bind(sequence,lambda e:setattr(self,'_range_anchor',None),add='+')
        container=ttk.Frame(panes);panes.add(container,weight=1)
        self.workspace=main=ttk.Frame(container);main.pack(fill='both',expand=True)
        header=ttk.Frame(main);header.pack(fill='x')
        header.columnconfigure(2,weight=1)
        ttk.Label(header,textvariable=self.heading,anchor='w',width=25,padding=(6,3)).grid(row=0,column=0,sticky='w')
        self.search_controls=ttk.Frame(header,padding=(4,2));self.search_controls.grid(row=0,column=1,sticky='w')
        ttk.Label(self.search_controls,text='Find').pack(side='left',padx=(0,5))
        self.find_entry=ttk.Entry(self.search_controls,textvariable=self.find_query,width=23)
        self.find_entry.pack(side='left')
        ttk.Button(self.search_controls,text='×',width=2,command=self.clear_find).pack(side='left',padx=2)
        self.find_buttons=[]
        for label,direction in (('‹',-1),('›',1)):
            button=ttk.Button(self.search_controls,text=label,width=2,state='disabled',command=lambda d=direction:self.find_next(d))
            button.pack(side='left',padx=2);self.find_buttons.append(button)
        ttk.Label(self.search_controls,textvariable=self.find_count,width=10,anchor='e').pack(side='left',padx=(2,5))
        self.find_query.trace_add('write',lambda *args:self.search_changed())
        self.find_entry.bind('<Return>',lambda e:self.shortcut(self.find_next))
        self.find_entry.bind('<Shift-Return>',lambda e:self.shortcut(lambda:self.find_next(-1)))
        self.find_entry.bind('<Escape>',lambda e:self.shortcut(self.clear_find))
        self.sequence_canvas=tk.Canvas(main,height=104,bg='white',highlightthickness=1,highlightbackground='#b8b8b8',highlightcolor='#7597bc',takefocus=True)
        self.sequence_canvas.pack(fill='x')
        self.sequence_canvas.bind('<Button-1>',lambda e:self.select_at(e,'consensus'))
        self.sequence_canvas.bind('<B1-Motion>',lambda e:self.drag_at(e,'consensus'))
        self.sequence_canvas.bind('<MouseWheel>',self.wheel)
        self.sequence_canvas.bind('<FocusIn>',lambda e:self.set_active_track('consensus'))
        trace_frame=ttk.Frame(main);trace_frame.pack(fill='both',expand=True)
        self.trace_scroll=tk.Canvas(trace_frame,highlightthickness=0)
        vertical=ttk.Scrollbar(trace_frame,orient='vertical',command=self.trace_scroll.yview)
        self.trace_scroll.configure(yscrollcommand=vertical.set)
        vertical.pack(side='right',fill='y');self.trace_scroll.pack(side='left',fill='both',expand=True)
        self.trace_body=ttk.Frame(self.trace_scroll)
        self.trace_window=self.trace_scroll.create_window(0,0,window=self.trace_body,anchor='nw')
        self.trace_scroll.bind('<Configure>',lambda e:self.trace_scroll.itemconfigure(self.trace_window,width=e.width))
        self.trace_body.bind('<Configure>',lambda e:self.trace_scroll.configure(scrollregion=self.trace_scroll.bbox('all')))
        self.f_pane=TracePane(self.trace_body,self,'F');self.f_pane.pack(fill='x',pady=(3,0))
        self.r_pane=TracePane(self.trace_body,self,'R');self.r_pane.pack(fill='x',pady=(3,0))
        self.trace_panes=[self.f_pane,self.r_pane]
        self.scrollbar=ttk.Scrollbar(main,orient='horizontal',command=self.scroll);self.scrollbar.pack(fill='x')
        controls=ttk.Frame(main,padding=(3,2));controls.pack(fill='x')
        right=ttk.Frame(controls);right.pack(side='right')
        ttk.Label(right,text='Base spacing').pack(side='left',padx=(8,4))
        self.zoom=ttk.Scale(right,from_=12,to=40,length=110,command=self.change_zoom)
        self.zoom.set(self.cell);self.zoom.pack(side='left')
        self.spacing_label=ttk.Label(right,text=f'{self.cell} px',width=5);self.spacing_label.pack(side='left',padx=4)
        ttk.Label(right,text='Trace height').pack(side='left',padx=(14,4))
        self.height_scale=ttk.Scale(right,from_=0.25,to=2.5,length=110,command=self.change_trace_height)
        self.height_scale.set(self.trace_gain);self.height_scale.pack(side='left')
        self.height_label=ttk.Label(right,text=f'{self.trace_gain:.0%}',width=5);self.height_label.pack(side='left',padx=4)

    def bind_keys(self):
        platform='Command' if sys.platform=='darwin' else 'Control'
        mapping={'s':self.save_project,'o':self.open_project,'n':self.new_project,'i':self.import_files,
                 'e':self.next_issue,'l':lambda:self.cut('L'),'r':lambda:self.cut('R'),
                 'b':self.build_manual,'m':self.toggle_mark}
        for key,command in mapping.items():
            self.bind(f'<{platform}-{key}>',lambda e,c=command:self.shortcut(c),add='+')
        self.bind(f'<{platform}-Shift-S>',lambda e:self.shortcut(self.save_project_as))
        self.bind(f'<{platform}-f>',lambda e:self.shortcut(self.focus_find))
        self.bind(f'<{platform}-g>',lambda e:self.shortcut(self.find_next))
        self.bind(f'<{platform}-Shift-G>',lambda e:self.shortcut(lambda:self.find_next(-1)))
        self.bind(f'<{platform}-Shift-E>',lambda e:self.shortcut(lambda:self.next_issue(-1)))
        self.bind(f'<{platform}-Shift-F>',lambda e:self.shortcut(self.export_current))
        self.bind(f'<{platform}-comma>',lambda e:self.shortcut(self.preferences_dialog))
        self.bind(f'<{platform}-Return>',lambda e:self.shortcut(self.mark_reviewed))
        for key,command in [('1',lambda:self.open_read_direction('F')),('2',lambda:self.open_read_direction('R')),('3',self.open_contig_view)]:
            self.bind(f'<{platform}-Key-{key}>',lambda e,c=command:self.shortcut(c))
        self.bind('<KeyPress>',self.key_edit,add='+')
        self.bind('<BackSpace>',lambda e:self.shortcut(self.delete_base) if self.canvas_focus() else None)
        self.bind('<Delete>',lambda e:self.shortcut(self.delete_base) if self.canvas_focus() else None)
        self.bind('<Left>',lambda e:self.arrow(-1) if self.canvas_focus() else None)
        self.bind('<Right>',lambda e:self.arrow(1) if self.canvas_focus() else None)
        self.bind('<Home>',lambda e:self.jump_edge(False) if self.canvas_focus() else None)
        self.bind('<End>',lambda e:self.jump_edge(True) if self.canvas_focus() else None)
        self.bind('<F2>',lambda e:self.shortcut(self.rename_selected))
        # Text entry widgets retain their native Copy bindings.
        for widget in (self.tree,self.sequence_canvas,self.f_pane.canvas,self.r_pane.canvas):
            widget.bind(f'<{platform}-c>',lambda e:self.shortcut(self.copy_sequence))
            widget.bind(f'<{platform}-Shift-C>',lambda e:self.shortcut(self.copy_fasta))
        # Handle history before Canvas/Treeview class and Aqua virtual bindings.
        # One handler per event prevents a native <<Undo>> binding swallowing Z
        # or a physical + virtual binding executing the same operation twice.
        history_tag=self.history_tag=f'{APP_NAME}History:{self._w}'
        for widget in (self,self.tree,self.sequence_canvas,self.f_pane.canvas,self.r_pane.canvas):
            widget.bindtags((history_tag,)+widget.bindtags())
        for sequence,command in ((f'<{platform}-z>',self.undo),
                                 (f'<{platform}-Z>',self.undo),
                                 (f'<{platform}-Shift-z>',self.redo),
                                 (f'<{platform}-Shift-Z>',self.redo),
                                 ('<<Undo>>',self.undo),('<<Redo>>',self.redo)):
            self.bind_class(history_tag,sequence,lambda e,c=command:self.shortcut(c))

    def base_font(self):return (MONO_FAMILY,int(self.project.appearance['font_size']))
    def base_colors(self):return self.project.appearance['colors']

    def pane_for_track(self,track):
        return next((p for p in self.trace_panes if p.track==track),self.f_pane)

    def update_trace_panes(self,count):
        while len(self.trace_panes)<max(2,count):
            pane=TracePane(self.trace_body,self,f'source:{len(self.trace_panes)}')
            pane.canvas.bindtags((self.history_tag,)+pane.canvas.bindtags())
            platform='Command' if sys.platform=='darwin' else 'Control'
            pane.canvas.bind(f'<{platform}-c>',lambda e:self.shortcut(self.copy_sequence))
            pane.canvas.bind(f'<{platform}-Shift-C>',lambda e:self.shortcut(self.copy_fasta))
            for widget in (pane,pane.canvas,pane.label):
                widget.drop_target_register(DND_FILES);widget.dnd_bind('<<Drop>>',self.on_drop)
            self.trace_panes.append(pane)
        for i,pane in enumerate(self.trace_panes):
            if i<count:pane.pack(fill='x',pady=(3,0))
            else:pane.pack_forget()

    def tree_double_click(self,event):
        row=self.tree.identify_row(event.y)
        region=self.tree.identify_region(event.x,event.y)
        if row in ('section:ab1','section:contigs') or region=='heading':
            column='length' if region=='heading' and self.tree.identify_column(event.x)=='#1' else 'name'
            self.sort_reverse=not self.sort_reverse if getattr(self,'sort_column',None)==column else False
            self.sort_column=column;self.read_sort_reverse=self.sort_reverse if column=='name' else None
            self.refresh_tree();return 'break'
        return self.rename_from_tree(event)

    def extend_read_selection(self,delta):
        ids=list(self.tree.get_children('section:ab1'));current=self.tree.focus()
        if not ids:return 'break'
        index=ids.index(current) if current in ids else (0 if delta>0 else len(ids)-1)
        anchor=getattr(self,'_range_anchor',None)
        if anchor not in ids:anchor=ids[index];self._range_anchor=anchor
        target=max(0,min(len(ids)-1,index+delta));lo,hi=sorted((ids.index(anchor),target))
        self.tree.selection_set(ids[lo:hi+1]);self.tree.focus(ids[target]);self.tree.see(ids[target])
        return 'break'

    def tree_context_menu(self,event):
        item=self.tree.identify_row(event.y)
        if not item or item.startswith('section:'):return 'break'
        if item not in self.tree.selection():self.tree.selection_set(item)
        self.tree.focus(item);self.tree.focus_set()
        selected=self.tree.selection()
        valid=[i for i in selected if self.resolve_read(i) in self.project.reads or i in self.tree_groups]
        menu=self.context_menu=tk.Menu(self,tearoff=False)
        menu.add_command(label='Build Contig (manual)',command=Editor.menu_action(self,self.build_manual),
            state='normal' if len(self.selected_reads())>=2 else 'disabled')
        menu.add_command(label='Return Reads to AB1',command=Editor.menu_action(self,lambda:self.return_reads_to_ab1()),
            state='normal' if any(i in self.tree_groups for i in selected) else 'disabled')
        menu.add_command(label='Rename…',command=Editor.menu_action(self,lambda:self.rename_selected(valid[0])),
            state='normal' if len(valid)==1 else 'disabled')
        menu.add_separator()
        menu.add_command(label='Copy Sequence',command=Editor.menu_action(self,self.copy_selected_sequences),state='normal' if valid else 'disabled')
        menu.add_command(label='Copy FASTA',command=Editor.menu_action(self,lambda:self.copy_fasta(True)),state='normal' if valid else 'disabled')
        menu.add_command(label='Export FASTA…',command=Editor.menu_action(self,self.export_selected),state='normal' if valid else 'disabled')
        menu.add_separator()
        menu.add_command(label='Delete',command=Editor.menu_action(self,self.delete_sequences),state='normal' if valid else 'disabled')
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()
        return 'break'

    def tree_keyboard_context_menu(self,event=None):
        item=self.tree.focus()
        if not item or item.startswith('section:'):return 'break'
        self.tree.see(item);self.update_idletasks()
        bounds=self.tree.bbox(item)
        if not bounds:return 'break'
        x,y,width,height=bounds
        event=tk.Event();event.y=y+height//2
        event.x_root=self.tree.winfo_rootx()+x+min(width,120)
        event.y_root=self.tree.winfo_rooty()+event.y
        return self.tree_context_menu(event)

    def delete_sequences(self):
        selected=self.tree.selection()
        ids=list(dict.fromkeys(self.resolve_read(i) for i in selected if self.resolve_read(i) in self.project.reads))
        keys=[self.tree_groups[i] for i in selected if i in self.tree_groups]
        if not ids and not keys:return
        linked={key for key,co in self.project.contigs.items() if set(ids).intersection(co.read_ids())}
        message=f'Remove {len(ids)+len(keys)} selected sequence(s) from this project?'
        extra=linked-set(keys)
        if extra:message+=f'\n{len(extra)} linked contig(s) will also be removed.'
        message+=shortcut_label('\n\nOriginal AB1 files are not deleted. Cmd+Z can undo this operation.')
        if not self.confirm_action('delete_sequences','Delete Sequences',message,'Delete'):return
        self.remember();self.project.remove_sequences(ids,keys)
        self.refresh_tree()
        if self.view_kind=='contig' and self.group_key in self.project.contigs:self.refresh_view()
        elif self.view_kind=='read' and self.read_id in self.project.reads:self.refresh_view()
        elif self.project.contigs:self.show_contig(next(iter(self.project.contigs)))
        elif self.project.reads:self.show_read(next(iter(self.project.reads)))
        else:
            self.read_id=None;self.group_key=None;self.view_kind='read';self.selection=self.anchor=self.offset=0
            self.refresh_view()

    def return_reads_to_ab1(self):
        """Dissolve selected contigs, preserving source reads and their edits/cuts."""
        keys=list(dict.fromkeys(self.tree_groups[i] for i in self.tree.selection() if i in self.tree_groups))
        if not keys:
            self.status.set('Select one or more contigs to return their reads to AB1 files.');return
        ids=list(dict.fromkeys(rid for key in keys for rid in self.project.contigs[key].read_ids()))
        remaining={rid for key,co in self.project.contigs.items() if key not in keys for rid in co.read_ids()}
        message=(f'Remove {len(keys)} contig(s) and return their source reads to AB1 files?\n\n'
            'Source edits and trimming are kept. Consensus-only edits and review flags are removed with the contig.\n'
            'Cmd+Z restores the complete contig. Original AB1 files are unchanged.')
        message=shortcut_label(message)
        if remaining.intersection(ids):message+='\n\nReads shared with another contig stay under that contig.'
        if not self.confirm_action('return_to_ab1','Return Reads to AB1',message,'Return Reads'):return
        self.remember();self.project.remove_sequences(contig_keys=keys)
        self.refresh_tree()
        available=[rid for rid in ids if rid not in remaining]
        if available:self.show_read(available[0])
        elif self.project.contigs:self.show_contig(next(iter(self.project.contigs)))
        elif self.project.reads:self.show_read(next(iter(self.project.reads)))
        else:
            self.read_id=None;self.group_key=None;self.view_kind='read';self.refresh_view()
        # Highlight all returned reads, so they can immediately be reassembled.
        if available:
            self.tree.selection_set(available);self.tree.focus(available[0]);self.tree.see(available[0]);self.tree.focus_set()
        self.status.set(shortcut_label(f'Returned {len(available)} read(s) to AB1 files; removed {len(keys)} contig(s). Cmd+Z restores the contigs.'))

    def register_drop_targets(self):
        def register(widget):
            if isinstance(widget,tk.Menu):return
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>',self.on_drop)
            for child in widget.winfo_children():register(child)
        register(self)

    def on_drop(self,event):
        try:
            paths=self.tk.splitlist(event.data)
            classify_import_paths(paths)
        except (ValueError,tk.TclError) as exc:
            message=str(exc);self.status.set(message)
            self.after_idle(lambda:messagebox.showerror('Import',message))
            return REFUSE_DROP
        # Leave the native drag session before displaying save/error dialogs.
        self.after_idle(lambda:self.accept_paths(paths))
        return COPY

    def accept_paths(self,paths):
        paths=list(dict.fromkeys(paths));total=len(paths)
        try:kind,paths=classify_import_paths(paths)
        except ValueError as exc:messagebox.showerror('Import',str(exc));return
        if kind=='project':self.load_project_path(paths[0])
        else:
            self.import_paths(paths)
            ignored=total-len(paths)
            if ignored:self.status.set(self.status.get()+f' | Ignored {ignored} accompanying FASTA file(s).')

    def rename_from_tree(self,event):
        item=self.tree.identify_row(event.y)
        if not item:return 'break'
        self.tree.selection_set(item);self.tree_select()
        self.rename_selected(item)
        return 'break'

    def rename_selected(self,item=None):
        if item is None:
            if self.view_kind=='read':item=self.read_id
            else:item=next((i for i,k in self.tree_groups.items() if k==self.group_key),None)
        key=self.tree_groups.get(item)
        rid=self.resolve_read(item)
        if rid in self.project.reads:target=self.project.reads[rid]
        elif key in self.project.contigs:target=self.project.contigs[key]
        else:
            messagebox.showinfo('Rename','Build a contig first, or select a read.');return
        name=simpledialog.askstring('Rename Sequence','Sequence name (also used as the FASTA identifier):',initialvalue=target.sequence_name,parent=self)
        if name is None or name==target.sequence_name:return
        try:fasta_text([(name,'A')])
        except ValueError as exc:messagebox.showerror('Rename',str(exc));return
        entry=self.history_entry()
        try:
            if rid in self.project.reads:self.project.rename_read(rid,name)
            else:self.project.rename_contig(key,name)
        except ValueError as exc:messagebox.showerror('Rename',str(exc));return
        self.remember(entry)
        self.refresh_tree();self.refresh_view()

    @staticmethod
    def shortcut(command):
        command();return 'break'

    def canvas_focus(self):
        return self.focus_get() in (self.sequence_canvas,*(p.canvas for p in self.trace_panes))

    def editing_track(self):
        focused=self.focus_get()
        for track,canvas in (('consensus',self.sequence_canvas),*((p.track,p.canvas) for p in self.trace_panes)):
            if focused is canvas:return track
        return self.active_track

    def focus_track(self):
        if getattr(self,'closing',False):return
        canvas=self.sequence_canvas if self.active_track=='consensus' else self.pane_for_track(self.active_track).canvas
        self.focus_force()
        canvas.focus_set()

    def set_active_track(self,track):
        self.active_track=track;self.update_status()

    def key_edit(self,event):
        # Command/control combinations and text fields must not become edits.
        modifiers=0x04|0x08|(0x10 if sys.platform=='darwin' else 0)
        # On Windows 0x10 can be Num Lock, not Aqua's Command modifier.
        if not self.canvas_focus() or event.state & modifiers:return
        base=event.char.upper()
        if len(base)==1 and base in ALPHABET:
            self.edit_base(base);return 'break'

    def history_entry(self,scope=None):
        view={name:getattr(self,name) for name in ('view_kind','read_id','group_key','selection','anchor','offset','active_track')}
        return (self.project.snapshot_scope(scope) if scope is not None else self.project.snapshot()),view

    def edit_history_entry(self):
        if self.view_kind=='read':scope=self.project.edit_scope([self.read_id])
        elif self.active_track=='consensus':scope=self.project.edit_scope(contig_keys=[self.group_key])
        else:
            co=self.project.contigs[self.group_key]
            if co.sources:
                index=self.trace_panes.index(self.pane_for_track(self.active_track));rid=co.sources[index].read_id
            else:rid=co.f_id if self.active_track=='F' else co.r_id
            scope=self.project.edit_scope([rid] if rid else (),[self.group_key])
        return self.history_entry(scope)

    def remember(self,entry=None):
        self.undo_stack.append(entry if entry is not None else self.history_entry())
        self.undo_stack=self.undo_stack[-100:];self.redo_stack=[]
        self.revision=getattr(self,'revision',0)+1

    def restore_history(self,entry):
        state,view=entry;self.project.restore(state)
        for name,value in view.items():setattr(self,name,value)
        self.sync_settings();self.refresh_tree()
        if self.view_kind=='read' and self.read_id not in self.project.reads:
            if self.project.reads:self.show_read(next(iter(self.project.reads)))
            else:self.read_id=None;self.group_key=None;self.refresh_view()
        elif self.view_kind=='contig' and self.group_key not in self.project.contigs:
            if self.project.contigs:self.show_contig(next(iter(self.project.contigs)))
            elif self.project.reads:self.show_read(next(iter(self.project.reads)))
            else:self.read_id=None;self.group_key=None;self.view_kind='read';self.refresh_view()
        else:self.refresh_view()
        item=self.read_id if self.view_kind=='read' else next((i for i,k in self.tree_groups.items() if k==self.group_key),None)
        if self.view_kind=='read' and not self.tree.exists(item):
            item=next((i for i,rid in getattr(self,'tree_reads',{}).items() if rid==self.read_id),None)
        if item and self.tree.exists(item):self.tree.selection_set(item)
        self.ensure_visible();self.update_status();self.focus_track()

    def changed(self):
        self.project.invalidate();self.refresh_tree();self.refresh_view();self.update_title()

    def undo(self):
        if not self.undo_stack:return
        entry=self.undo_stack.pop();state=entry[0]
        self.redo_stack.append(self.history_entry(state.scope if isinstance(state,ScopedSnapshot) else None));self.restore_history(entry)
        self.revision=getattr(self,'revision',0)+1

    def redo(self):
        if not self.redo_stack:return
        entry=self.redo_stack.pop();state=entry[0]
        self.undo_stack.append(self.history_entry(state.scope if isinstance(state,ScopedSnapshot) else None));self.restore_history(entry)
        self.revision=getattr(self,'revision',0)+1

    def update_title(self):
        name=self.project.path.name if self.project.path else 'Untitled'
        self.title(f'{name}{" *" if self.project.dirty else ""} — {APP_NAME} ver {APP_VERSION}')

    def refresh_tree(self):
        self._ignore_tree=True
        selected=self.tree.selection()
        focused=self.tree.focus()
        opened={key for item,key in getattr(self,'tree_groups',{}).items() if self.tree.exists(item) and self.tree.item(item,'open')}
        self.tree.delete(*self.tree.get_children());self.tree_groups={};self.tree_reads={}
        self.tree.insert('','end',iid='section:ab1',text='AB1 files',values=('',),open=True)
        column=getattr(self,'sort_column',None);descending=getattr(self,'sort_reverse',False)
        if column is None and self.read_sort_reverse is not None:column='name';descending=self.read_sort_reverse
        self.tree.heading('#0',text='Sequences'+((' ▼' if descending else ' ▲') if column=='name' else ''))
        self.tree.heading('length',text='Length (bp)'+((' ▼' if descending else ' ▲') if column=='length' else ''))
        names={rid:read.sequence_name for rid,read in self.project.reads.items()}
        lengths={rid:len(bases_for_read(read,self.project.traces[rid])) for rid,read in self.project.reads.items()}
        ids=ordered_entries(self.project.reads,names,lengths,column,descending)
        assigned={rid for co in self.project.contigs.values() for rid in co.read_ids()}
        for rid in ids:
            if rid in assigned:continue
            read=self.project.reads[rid]
            self.tree.insert('section:ab1','end',iid=rid,text=read.sequence_name,values=(lengths[rid],))
        self.tree.insert('','end',iid='section:contigs',text='Contigs',values=('',),open=True)
        contig_names={key:co.sequence_name for key,co in self.project.contigs.items()}
        contig_lengths={key:len(co.sequence()) if not co.stale else None for key,co in self.project.contigs.items()}
        for key in ordered_entries(self.project.contigs,contig_names,contig_lengths,column,descending):
            contig=self.project.contigs[key]
            gid=f'contig:{key}';self.tree_groups[gid]=key
            length=contig_lengths[key] if contig_lengths[key] is not None else '—'
            self.tree.insert('section:contigs','end',iid=gid,text=contig.sequence_name,values=(length,),open=key in opened)
            for rid in ordered_entries(contig.read_ids(),names,lengths,column,descending):
                item=f'{gid}:read:{rid}';self.tree_reads[item]=rid
                read=self.project.reads[rid]
                self.tree.insert(gid,'end',iid=item,text=read.sequence_name,values=(lengths[rid],))
        selected=[item for item in selected if self.tree.exists(item)]
        if selected:
            self.tree.selection_set(selected)
            self.tree.focus(focused if focused in selected else selected[-1])
        else:self.tree.focus('section:ab1')
        self.after_idle(lambda:setattr(self,'_ignore_tree',False))

    def select_view_item(self,kind,identifier):
        selected=self.tree.selection()
        candidates=([item for item,key in self.tree_groups.items() if key==identifier] if kind=='contig'
            else [identifier]+[item for item,rid in self.tree_reads.items() if rid==identifier])
        candidates=[item for item in candidates if self.tree.exists(item)]
        if not candidates:return
        item=next((item for item in candidates if item in selected),candidates[0])
        # Never collapse a user's Shift/Cmd multi-selection while previewing it.
        if item not in selected:
            if self.focus_get() is self.tree:return
            self.tree.selection_set(item)
        self.tree.focus(item);self.tree.see(item)

    def tree_select(self,event=None):
        if self._ignore_tree:return
        selected=self.tree.selection()
        if not selected:return
        keep_tree_focus=self.focus_get() is self.tree
        item=self.tree.focus() if self.tree.focus() in selected else selected[-1]
        if item in self.tree_groups:
            key=self.tree_groups[item]
            # Native selection notifications can arrive after a tree refresh's
            # idle guard has cleared. Re-selecting the already displayed item
            # must not reset the edit cursor/focus (especially during Undo).
            if self.view_kind=='contig' and self.group_key==key:return
            self.group_key=key
            self.show_contig(key)
        elif self.resolve_read(item) in self.project.reads:
            rid=self.resolve_read(item)
            if self.view_kind=='read' and self.read_id==rid:return
            self.show_read(rid)
            parent=self.tree.parent(item)
            if parent in self.tree_groups:self.group_key=self.tree_groups[parent]
        if keep_tree_focus:self.tree.focus_set()

    def show_read(self,rid):
        self.read_id=rid;read=self.project.reads[rid]
        self.group_key=next((key for key,c in self.project.contigs.items() if rid in c.read_ids()),self.project.key(read.sample,read.marker));self.view_kind='read'
        self.selection=self.anchor=max(0,read.start);self.offset=max(0,read.start-5)
        self.active_track='F';self.refresh_view()
        if read.reverse:
            self.selection=self.anchor=next((i for i,b in enumerate(self.f_pane.mapping) if b.raw is not None and read.start<=b.raw<read.end),0)
            self.offset=max(0,self.selection-5)
        self.select_view_item('read',rid)
        self.f_pane.canvas.focus_set()

    def open_read_direction(self,direction):
        co=self.project.contigs.get(self.group_key)
        if co and co.sources:
            index=0 if direction=='F' else 1
            if index<len(co.sources):self.show_read(co.sources[index].read_id)
            return
        for rid in self.project.groups().get(self.group_key,[]):
            if self.project.reads[rid].direction==direction:
                self.show_read(rid);return
        self.status.set(f'No {direction} read in this group.')

    def open_contig_view(self):
        if self.group_key in self.project.contigs:self.show_contig(self.group_key)
        else:self.status.set('Build a contig first.')

    def show_contig(self,key):
        self.group_key=key;self.view_kind='contig';self.read_id=None
        self.selection=self.anchor=0;self.offset=0;self.active_track='consensus'
        self.refresh_view();self.select_view_item('contig',key);self.sequence_canvas.focus_set()

    def refresh_view(self):
        has_sequence=(self.view_kind=='read' and self.read_id in self.project.reads) or (self.view_kind=='contig' and self.group_key in self.project.contigs)
        if has_sequence:self.workspace.pack(fill='both',expand=True)
        else:self.workspace.pack_forget()
        if self.view_kind=='contig' and has_sequence:self.search_controls.grid()
        else:self.search_controls.grid_remove()
        if self.view_kind=='read' and self.read_id in self.project.reads:
            read=self.project.reads[self.read_id];trace=self.project.traces[self.read_id]
            self.update_trace_panes(1)
            mapping=bases_for_read(read,trace,raw_view=True)
            self.f_pane.read_id=read.id;self.f_pane.mapping=reverse_bases(mapping) if read.reverse else mapping;self.f_pane.reverse=read.reverse
            self.r_pane.read_id=None;self.r_pane.mapping=[];self.r_pane.label.config(text='')
            self.meta_sample.set(read.sample);self.meta_marker.set(read.marker);self.meta_direction.set(read.direction)
            self.trim_left.set(str(read.start+1));self.trim_right.set(str(read.end))
            self.heading.set(read.sequence_name)
        elif self.view_kind=='contig' and self.group_key in self.project.contigs:
            co=self.project.contigs[self.group_key]
            self.update_trace_panes(len(co.sources) if co.sources else 2)
            self.f_pane.read_id=co.f_id;self.r_pane.read_id=co.r_id
            self.f_pane.mapping=[co.f_bases[c.f] if c.f is not None else None for c in co.columns]
            self.r_pane.mapping=[co.r_bases[c.r] if c.r is not None else None for c in co.columns]
            self.f_pane.reverse=False;self.r_pane.reverse=True
            if co.sources:
                for pane,source in zip(self.trace_panes,co.sources):
                    pane.read_id=source.read_id;pane.mapping=source.mapping;pane.reverse=source.reverse
            self.meta_sample.set(co.sample);self.meta_marker.set(co.marker);self.meta_direction.set('')
            self.trim_left.set('');self.trim_right.set('')
            self.heading.set(co.sequence_name)
        else:
            self.update_trace_panes(0)
            self.f_pane.read_id=self.r_pane.read_id=None;self.f_pane.mapping=[];self.r_pane.mapping=[]
            self.f_pane.label.config(text='');self.r_pane.label.config(text='')
            for var in (self.meta_sample,self.meta_marker,self.meta_direction,self.trim_left,self.trim_right,self.heading):var.set('')
        self.schedule_draw();self.update_title()

    def length(self):
        if self.view_kind=='contig' and self.group_key in self.project.contigs:
            return len(self.project.contigs[self.group_key].columns)
        return len(self.f_pane.mapping)

    def visible_count(self):return max(5,int((self.sequence_canvas.winfo_width()-90)/self.cell))
    def x(self,col):return 82+(col-self.offset)*self.cell
    def col_at(self,x):return max(0,min(max(0,self.length()-1),self.offset+int((x-82+self.cell/2)//self.cell)))

    def select_at(self,event,track):
        event.widget.focus_set();col=self.col_at(event.x)
        if not event.state & 1:self.anchor=col
        self.selection=col;self.active_track=track;self.schedule_draw();self.update_status()

    def drag_at(self,event,track):
        self.selection=self.col_at(event.x);self.active_track=track;self.schedule_draw();self.update_status()

    def arrow(self,delta):
        self.selection=max(0,min(self.length()-1,self.selection+delta));self.anchor=self.selection
        self.ensure_visible();self.update_status();return 'break'

    def jump_edge(self,end):
        self.selection=self.anchor=max(0,self.length()-1) if end else 0
        self.ensure_visible();self.update_status();return 'break'

    def update_status(self):
        if self.view_kind=='read' and self.read_id and self.selection<len(self.f_pane.mapping):
            b=self.f_pane.mapping[self.selection]
            q=self.project.traces[self.read_id].qualities[b.raw] if b.raw is not None else None
            self.status.set(f'AB1 position {b.raw+1 if b.raw is not None else "inserted"} | {b.base} | Phred {q if q is not None else "n/a"}')
        elif self.view_kind=='contig' and self.group_key in self.project.contigs:
            co=self.project.contigs[self.group_key];i=self.selection
            if i>=len(co.columns):return
            c=co.columns[i];coords=[]
            entries=[(self.project.reads[s.read_id].sequence_name,s.mapping[i]) for s in co.sources] if co.sources else [(d,bs[index] if index is not None else None) for d,bs,index in [('F',co.f_bases,c.f),('R',co.r_bases,c.r)]]
            for name,b in entries:
                if b is not None:
                    coords.append(f'{name}: AB1 {b.raw+1 if b.raw is not None else "inserted"}, Phred {b.quality if b.quality is not None else "n/a"}')
            q=c.quality if str(i) not in co.overrides else None
            active='Consensus' if self.active_track=='consensus' else 'Source read'
            self.status.set(f'{active} | Position {i+1} | Support Q {q if q is not None else "n/a"} | {" | ".join(coords)}')
        else:self.status.set('')

    def consensus_position(self,col):
        co=self.project.contigs[self.group_key]
        return sum(len(co.insertions.get(str(i),''))+bool(co.call(i)) for i in range(col+1))

    def is_marked(self,col):
        if self.view_kind=='contig' and self.group_key in self.project.contigs:
            return col in self.project.contigs[self.group_key].issues()
        if self.read_id and col<len(self.f_pane.mapping):
            b=self.f_pane.mapping[col]
            if b.raw is None:return False
            read=self.project.reads[self.read_id];q=self.project.traces[self.read_id].qualities[b.raw]
            auto=read.start<=b.raw<read.end and (q is None or q<self.project.settings['min_quality'] or b.base=='N')
            return read.black_marks.get(str(b.raw),auto)
        return False

    def next_issue(self,direction=1):
        marks=self.project.contigs[self.group_key].issues() if self.view_kind=='contig' and self.group_key in self.project.contigs else [i for i in range(self.length()) if self.is_marked(i)]
        if not marks:self.status.set('No review flags.');return
        choices=[i for i in marks if i>self.selection] if direction>0 else [i for i in marks if i<self.selection]
        self.selection=(choices[0] if choices else marks[0]) if direction>0 else (choices[-1] if choices else marks[-1])
        self.anchor=self.selection;self.ensure_visible();self.update_status()
        (self.sequence_canvas if self.view_kind=='contig' else self.f_pane.canvas).focus_set()

    def toggle_mark(self):
        self.review_selection(self.is_marked(self.selection) if self.length() else True)

    def mark_reviewed(self):self.review_selection(True)

    def review_selection(self,reviewed):
        if not self.length():return
        scope=self.project.edit_scope(contig_keys=[self.group_key]) if self.view_kind=='contig' else self.project.edit_scope([self.read_id])
        self.remember(self.history_entry(scope))
        lo,hi=sorted((self.selection,self.anchor))
        for i in range(lo,hi+1):
            if self.view_kind=='contig':self.project.contigs[self.group_key].black_marks[str(i)]=not reviewed
            else:
                b=self.f_pane.mapping[i]
                if b.raw is not None:self.project.reads[self.read_id].black_marks[str(b.raw)]=not reviewed
        self.project.dirty=True;self.schedule_draw();self.update_title()
        self.status.set('Selection marked reviewed.' if reviewed else 'Selection flagged for review.')

    def schedule_draw(self):
        if not self.draw_job:self.draw_job=self.after_idle(self.draw)

    def draw(self):
        self.draw_job=None
        if not self.workspace.winfo_manager():return
        self.offset=max(0,min(self.offset,max(0,self.length()-self.visible_count())))
        self.draw_sequence()
        for pane in self.trace_panes:
            if pane.winfo_manager():pane.draw()
        total=max(1,self.length());self.scrollbar.set(self.offset/total,min(1,(self.offset+self.visible_count())/total))

    def draw_sequence(self):
        c=self.sequence_canvas;c.delete('all')
        label='Consensus' if self.view_kind=='contig' else 'Sequence'
        c.create_text(6,43,text=label,anchor='w',font=(UI_FAMILY,10))
        if not self.length():
            c.create_text(82,43,anchor='w',text='Drop AB1 files or a project here.',fill='#777');return
        co=self.project.contigs.get(self.group_key) if self.view_kind=='contig' else None
        highlighted,active=self.search_highlights() if co else (set(),set())
        marked=set(co.issues()) if co else None
        lo,hi=sorted((self.anchor,self.selection))
        for i in range(self.offset,min(self.length(),self.offset+self.visible_count())):
            x=self.x(i)
            if lo<=i<=hi:c.create_rectangle(x-self.cell/2,0,x+self.cell/2,104,fill='#dbe7f6',outline='')
            if i in highlighted:c.create_rectangle(x-self.cell/2,31,x+self.cell/2,57,fill='#ffcc80' if i in active else '#ffe0ac',outline='',tags=('find_hit',))
            call=co.call(i) if co else self.f_pane.mapping[i].base
            if (i in marked if marked is not None else self.is_marked(i)):c.create_rectangle(x-4,6,x+4,12,fill='black',outline='')
            if (i+1)%10==0 or i==self.offset:c.create_text(x,24,text=str(i+1),font=(MONO_FAMILY,8),fill='#555')
            c.create_text(x,44,text=call or '-',font=self.base_font(),fill=self.base_colors().get(call,'#555'))
            manual=(str(i) in co.overrides) if co else self.f_pane.mapping[i].manual
            if manual:c.create_line(x-5,57,x+5,57,fill='#b66b16',width=2)
            if co and co.insertions.get(str(i)):
                self.draw_insertion(i,co.insertions[str(i)],i in highlighted)
            if co and i==len(co.columns)-1 and co.insertions.get(str(i+1)):
                self.draw_insertion(i,co.insertions[str(i+1)],i in highlighted,True)
            if co:
                quality=co.columns[i].quality if str(i) not in co.overrides else None
                if quality is not None and call:
                    c.create_rectangle(x-3,87-min(60,quality)/60*23,x+3,87,fill='#777',outline='')
        if co:c.create_text(6,77,text='Support Q',anchor='w',font=(UI_FAMILY,9),fill='black')

    def draw_insertion(self,column,text,highlighted,tail=False):
        canvas=self.sequence_canvas
        item=canvas.create_text(self.x(column)-8,97 if not tail else 91,text=('+' if not tail else '→+')+text,font=(MONO_FAMILY,8),fill='#9b621e',anchor='w')
        if highlighted:
            bounds=canvas.bbox(item)
            if bounds:
                background=canvas.create_rectangle(*bounds,fill='#ffd28a',outline='',tags=('find_hit',))
                canvas.tag_lower(background,item)

    def search_changed(self):
        if self.find_job:self.after_cancel(self.find_job)
        self.find_job=self.after(250,lambda:self.find_next(reset=True))

    def update_find_results(self):
        if self.view_kind!='contig' or self.group_key not in self.project.contigs:return False
        co=self.project.contigs[self.group_key];sequence,mapping=consensus_coordinates(co)
        query=self.find_query.get();identity=self.project.find_settings['identity']
        key=(self.group_key,sequence,mapping,query,identity)
        if key==self.find_cache:return False
        self.find_cache=key;self.find_map=mapping;self.find_index=-1;self.find_error=None
        try:self.find_results=find_matches(sequence,query,identity)
        except ValueError as exc:
            self.find_error=str(exc);self.find_results=[];self.find_count.set('Invalid DNA');self.status.set(str(exc))
            Editor.update_find_buttons(self);return True
        self.find_count.set(f'0/{len(self.find_results)}' if query.strip() else '')
        Editor.update_find_buttons(self)
        return True

    def update_find_buttons(self):
        for button in getattr(self,'find_buttons',()):button.configure(state='normal' if self.find_results else 'disabled')

    def search_highlights(self):
        self.update_find_results()
        highlighted={column for hit in self.find_results for column in self.find_map[hit.start:hit.end]}
        active=set()
        if 0<=self.find_index<len(self.find_results):
            hit=self.find_results[self.find_index];active=set(self.find_map[hit.start:hit.end])
        return highlighted,active

    def find_next(self,direction=1,reset=False):
        if self.find_job:self.after_cancel(self.find_job);self.find_job=None
        if self.view_kind!='contig' or self.group_key not in self.project.contigs:
            self.status.set('Select a contig to search.');return
        changed=self.update_find_results()
        if not self.find_results:
            if self.find_query.get().strip():self.status.set(getattr(self,'find_error',None) or 'No matching sequence. Search checks both strands without gaps.')
            self.schedule_draw();return
        if reset or changed or self.find_index<0:self.find_index=0 if direction>0 else len(self.find_results)-1
        else:self.find_index=(self.find_index+direction)%len(self.find_results)
        hit=self.find_results[self.find_index]
        self.selection=self.anchor=self.find_map[hit.start];self.active_track='consensus'
        self.find_count.set(f'{self.find_index+1}/{len(self.find_results)} ({hit.strand})')
        self.status.set(f'Match {self.find_index+1}/{len(self.find_results)} | Consensus {hit.start+1}–{hit.end} bp | Strand {hit.strand} | Identity {hit.identity:.1f}%')
        self.ensure_visible()

    def clear_find(self):
        self.find_query.set('');self.find_results=[];self.find_index=-1;self.find_cache=None;self.find_count.set('')
        Editor.update_find_buttons(self)
        self.schedule_draw()

    def focus_find(self):
        if self.view_kind!='contig' or self.group_key not in self.project.contigs:
            self.status.set('Select a contig to search.');return
        self.find_entry.focus_set();self.find_entry.selection_range(0,'end')

    def find_settings_dialog(self):
        window,body=self.panel('Find Settings');identity=tk.StringVar(value=str(self.project.find_settings['identity']))
        ttk.Label(body,text='Minimum identity (%)').grid(row=0,column=0,padx=(0,15),pady=8)
        ttk.Spinbox(body,from_=80,to=100,increment=1,textvariable=identity,width=7).grid(row=0,column=1)
        ttk.Label(body,text='100% = exact match. Both strands are searched.\nIdentity is matching letters / query length; no gaps.\nDNA/IUPAC symbols match literally, not as wildcards.').grid(row=1,column=0,columnspan=2,pady=10,sticky='w')
        def apply():
            try:value=dict(identity=int(identity.get()));validate_find_settings(value)
            except ValueError as exc:messagebox.showerror('Find Settings',str(exc),parent=window);return
            if value!=self.project.find_settings:
                self.remember();self.project.find_settings=value;self.project.dirty=True;self.update_title()
            window.destroy();self.find_next(reset=True)
        ttk.Button(body,text='Cancel',command=window.destroy).grid(row=2,column=0,sticky='w')
        ttk.Button(body,text='Apply',command=apply).grid(row=2,column=1)
        window.bind('<Return>',lambda e:apply());window.bind('<Escape>',lambda e:window.destroy())

    def ensure_visible(self):
        if self.selection<self.offset or self.selection>=self.offset+self.visible_count():
            self.offset=max(0,self.selection-self.visible_count()//2)
        self.schedule_draw()

    def pan(self,amount):self.offset=max(0,min(self.offset+amount,max(0,self.length()-self.visible_count())));self.schedule_draw()
    def wheel(self,event):self.pan(-int(event.delta/abs(event.delta))*3 if event.delta else 0);return 'break'
    def scroll(self,*args):
        if args[0]=='moveto':self.offset=int(float(args[1])*self.length())
        else:self.offset+=int(args[1])*(self.visible_count() if args[2]=='pages' else 3)
        self.schedule_draw()
    def change_zoom(self,value):
        self.cell=int(float(value))
        if hasattr(self,'spacing_label'):self.spacing_label.config(text=f'{self.cell} px')
        self.schedule_draw()

    def change_trace_height(self,value):
        self.trace_gain=float(value)
        if hasattr(self,'height_label'):self.height_label.config(text=f'{self.trace_gain:.0%}')
        self.schedule_draw()

    def panel(self,title):
        window=tk.Toplevel(self);window.title(title);window.transient(self)
        window.resizable(False,False);window.grab_set()
        body=ttk.Frame(window,padding=16);body.pack(fill='both',expand=True)
        window.bind('<Destroy>',lambda e:self.after_idle(self.focus_track) if e.widget is window else None)
        return window,body

    def ask_confirmation(self,title,message,action_label):
        window,body=self.panel(title)
        suppress=tk.BooleanVar(master=window,value=False);result=[False,False]
        ttk.Label(body,text=message,wraplength=430,justify='left').pack(anchor='w')
        ttk.Checkbutton(body,text="Don't ask again for this action",variable=suppress).pack(anchor='w',pady=(14,12))
        buttons=ttk.Frame(body);buttons.pack(fill='x')
        def finish(accepted):
            result[:]=[accepted,accepted and suppress.get()]
            window.destroy()
        cancel=ttk.Button(buttons,text='Cancel',command=lambda:finish(False));cancel.pack(side='left')
        ttk.Button(buttons,text=action_label,command=lambda:finish(True)).pack(side='right')
        window.protocol('WM_DELETE_WINDOW',lambda:finish(False))
        window.bind('<Escape>',lambda e:finish(False))
        # Enter must not silently accept a destructive operation.
        window.bind('<Return>',lambda e:finish(False));cancel.focus_set()
        window.update_idletasks()
        x=self.winfo_rootx()+max(0,(self.winfo_width()-window.winfo_width())//2)
        y=self.winfo_rooty()+max(0,(self.winfo_height()-window.winfo_height())//2)
        window.geometry(f'+{x}+{y}')
        self.wait_window(window)
        return tuple(result)

    def confirm_action(self,key,title,message,action_label):
        if not self.confirmations.should_ask(key):return True
        accepted,suppress=self.ask_confirmation(title,message,action_label)
        if accepted and suppress:
            try:self.confirmations.set_suppressed(key,True)
            except OSError as exc:
                messagebox.showerror('Confirmation Settings',f'The action will continue, but this preference could not be saved.\n{exc}',parent=self)
        return accepted

    def confirmations_dialog(self):
        window,body=self.panel('Confirmations');variables={}
        labels=(('delete_sequences','Ask before deleting project sequences'),
            ('return_to_ab1','Ask before returning contigs to AB1 files'),
            ('export_review_flags','Ask before exporting with review flags'))
        for key,label in labels:
            variable=tk.BooleanVar(master=window,value=self.confirmations.should_ask(key));variables[key]=variable
            ttk.Checkbutton(body,text=label,variable=variable).pack(anchor='w',pady=5)
        ttk.Label(body,text='Applies to this Mac, not the project.\nUnsaved changes and overwrite-of-edit warnings still ask.',
            justify='left').pack(anchor='w',pady=(10,14))
        def apply():
            try:
                for key,var in variables.items():self.confirmations.set_suppressed(key,not var.get())
            except OSError as exc:messagebox.showerror('Confirmation Settings',str(exc),parent=window);return
            window.destroy()
        buttons=ttk.Frame(body);buttons.pack(fill='x')
        ttk.Button(buttons,text='Cancel',command=window.destroy).pack(side='left')
        ttk.Button(buttons,text='Apply',command=apply).pack(side='right')
        window.bind('<Escape>',lambda e:window.destroy())

    def trimming_dialog(self):
        window,body=self.panel('Trimming Settings')
        settings=self.project.trim_settings
        enabled=tk.BooleanVar(value=settings['enabled'])
        quality=tk.StringVar(value=str(settings['quality']));minimum=tk.StringVar(value=str(settings['min_length']))
        ttk.Checkbutton(body,text='Automatically trim AB1 reads on import',variable=enabled).grid(row=0,column=0,columnspan=2,sticky='w',pady=6)
        for row,label,var in ((1,'Quality threshold (Phred Q)',quality),(2,'Minimum retained length (bp)',minimum)):
            ttk.Label(body,text=label).grid(row=row,column=0,sticky='w',padx=(0,18),pady=7)
            ttk.Entry(body,textvariable=var,width=9).grid(row=row,column=1)
        ttk.Label(body,text='Applies to newly imported AB1 files.\nExisting cuts and original AB1 files are unchanged.').grid(row=3,column=0,columnspan=2,sticky='w',pady=10)
        def apply():
            try:
                value=dict(enabled=enabled.get(),quality=int(quality.get()),min_length=int(minimum.get()))
                validate_trim_settings(value)
            except ValueError as exc:messagebox.showerror('Invalid Settings',str(exc));return
            if value!=self.project.trim_settings:
                self.remember();self.project.trim_settings=value;self.project.dirty=True;self.update_title()
            window.destroy()
        ttk.Button(body,text='Cancel',command=window.destroy).grid(row=4,column=0,sticky='w')
        ttk.Button(body,text='Apply',command=apply).grid(row=4,column=1,sticky='e')
        window.bind('<Return>',lambda e:apply());window.bind('<Escape>',lambda e:window.destroy())

    def appearance_dialog(self):
        window,body=self.panel('Appearance')
        appearance=copy.deepcopy(self.project.appearance);size=tk.StringVar(value=str(appearance['font_size']))
        ttk.Label(body,text='Base font size').grid(row=0,column=0,sticky='w',padx=(0,18),pady=8)
        ttk.Spinbox(body,from_=8,to=20,textvariable=size,width=7).grid(row=0,column=1)
        for row,base in enumerate('ACGT',1):
            ttk.Label(body,text=f'{base} / trace color').grid(row=row,column=0,sticky='w',pady=6)
            button=tk.Button(body,text=base,fg=appearance['colors'][base],font=(MONO_FAMILY,15,'bold'),width=5)
            def choose(b=base,widget=button):
                color=colorchooser.askcolor(color=appearance['colors'][b],title=f'{b} Color',parent=window)[1]
                if color:appearance['colors'][b]=color;widget.config(fg=color)
            button.config(command=choose);button.grid(row=row,column=1)
        def apply():
            try:appearance['font_size']=int(size.get());validate_appearance(appearance)
            except ValueError as exc:messagebox.showerror('Invalid Settings',str(exc));return
            if appearance!=self.project.appearance:
                self.remember();self.project.appearance=appearance;self.project.dirty=True
                self.zoom.set(max(self.cell,appearance['font_size']+6));self.schedule_draw();self.update_title()
            window.destroy()
        ttk.Button(body,text='Cancel',command=window.destroy).grid(row=5,column=0,sticky='w',pady=12)
        ttk.Button(body,text='Apply',command=apply).grid(row=5,column=1,sticky='e',pady=12)
        window.bind('<Return>',lambda e:apply());window.bind('<Escape>',lambda e:window.destroy())

    def settings_dialog(self):
        self.sync_settings();window,body=self.panel('Assembly Settings')
        labels=('Minimum Phred Q','Minimum overlap (bp)','Minimum identity (%)')
        for row,(label,(key,(var,scale))) in enumerate(zip(labels,self.setting_vars.items())):
            ttk.Label(body,text=label).grid(row=row,column=0,sticky='w',padx=(0,18),pady=7)
            ttk.Entry(body,textvariable=var,width=10).grid(row=row,column=1,pady=7)
        def apply():
            if self.apply_settings():window.destroy()
        ttk.Button(body,text='Apply',command=apply).grid(row=3,column=1,pady=(12,0))
        ttk.Button(body,text='Cancel',command=window.destroy).grid(row=3,column=0,sticky='w',pady=(12,0))
        window.bind('<Return>',lambda e:apply());window.bind('<Escape>',lambda e:window.destroy())

    def metadata_dialog(self):
        if self.view_kind!='read' or self.read_id not in self.project.reads:
            messagebox.showinfo('Read Assignment','Select an individual read first.');return
        self.refresh_view();window,body=self.panel('Read Assignment')
        for row,(label,var) in enumerate([('Sample',self.meta_sample),('Region',self.meta_marker)]):
            ttk.Label(body,text=label).grid(row=row,column=0,sticky='w',padx=(0,18),pady=7)
            ttk.Entry(body,textvariable=var,width=24).grid(row=row,column=1,pady=7)
        ttk.Label(body,text='Direction').grid(row=2,column=0,sticky='w',pady=7)
        ttk.Combobox(body,textvariable=self.meta_direction,values=('','F','R'),state='readonly',width=21).grid(row=2,column=1,pady=7)
        def apply():
            if self.apply_metadata():window.destroy()
        ttk.Button(body,text='Apply',command=apply).grid(row=3,column=1,sticky='e',pady=(12,0))
        ttk.Button(body,text='Cancel',command=window.destroy).grid(row=3,column=0,sticky='w',pady=(12,0))
        window.bind('<Return>',lambda e:apply());window.bind('<Escape>',lambda e:window.destroy())

    def edit_base(self,value):
        if not self.length():return
        self.active_track=self.editing_track()
        if self.view_kind=='contig' and self.active_track!='consensus':
            self.edit_source_bases(value);return
        if self.view_kind=='contig':
            co=self.project.contigs[self.group_key]
            if co.stale:messagebox.showinfo('Rebuild Required','Inputs have changed. Rebuild the contig before editing.');return
            if co.call(self.selection)==value:
                if self.is_marked(self.selection):self.mark_reviewed()
                self.arrow(1);return
            self.remember(self.edit_history_entry());co.overrides[str(self.selection)]=value
            co.black_marks.pop(str(self.selection),None)
            self.project.dirty=True
        elif self.read_id:
            b=self.f_pane.mapping[self.selection]
            if b.raw is None:
                messagebox.showinfo('Inserted Bases','Use Edit > Insert to change inserted bases.');return
            entry=self.edit_history_entry()
            if self.project.reads[self.read_id].reverse:value=COMPLEMENT[value]
            if self.project.edit_read_bases(self.read_id,[b.raw],value):self.remember(entry)
        self.refresh_tree();self.refresh_view()
        if value:self.selection=min(self.length()-1,self.selection+1);self.anchor=self.selection
        self.ensure_visible();self.update_status()

    def delete_base(self):
        if not self.length():return
        self.active_track=self.editing_track()
        if self.view_kind=='contig' and self.active_track!='consensus':
            self.edit_source_bases('');return
        positions=self.deletion_positions()
        if not positions:return
        entry=self.edit_history_entry()
        if self.view_kind=='contig':
            co=self.project.contigs[self.group_key]
            if co.stale:messagebox.showinfo('Rebuild Required','Rebuild the contig before editing.');return
            for i in positions:co.overrides[str(i)]=''
            self.project.dirty=True
        else:
            self.project.edit_read_bases(self.read_id,[self.f_pane.mapping[i].raw for i in positions],'')
        self.remember(entry)
        self.selection=self.anchor=min(positions)
        self.refresh_tree();self.refresh_view();self.ensure_visible();self.update_status()

    def deletion_positions(self):
        """Delete the selection; subsequent Backspace walks left over live bases.

        Keep the first deleted cell selected so typing can restore that position.
        A second Backspace skips the empty cell (and alignment gaps), rather than
        repeatedly deleting it and creating no-op Undo entries.
        """
        def live(i):
            if self.view_kind=='contig' and self.active_track=='consensus':
                return bool(self.project.contigs[self.group_key].call(i))
            pane=self.pane_for_track(self.active_track) if self.view_kind=='contig' else self.f_pane
            b=pane.mapping[i]
            return b is not None and b.raw is not None and b.base not in ('','-')
        lo,hi=sorted((self.selection,self.anchor))
        if lo!=hi:return [i for i in range(lo,hi+1) if live(i)]
        return next(([i] for i in range(lo,-1,-1) if live(i)),[])

    def edit_source_bases(self,value):
        positions=[self.selection] if value else self.deletion_positions()
        if not positions:return
        entry=self.edit_history_entry()
        try:
            co=self.project.contigs[self.group_key]
            if co.sources:
                index=self.trace_panes.index(self.pane_for_track(self.active_track))
                changed=self.project.edit_source(self.group_key,index,positions,value)
            else:changed=self.project.edit_aligned_read(self.group_key,self.active_track,positions,value)
        except ValueError as exc:messagebox.showinfo('Source Read Edit',str(exc));return
        if not changed:return
        self.remember(entry)
        self.refresh_tree();self.refresh_view()
        self.selection=self.anchor=min(self.length()-1,self.selection+1) if value else min(positions)
        self.ensure_visible();self.update_status()

    def insert_base(self):
        if not self.length():return
        if self.view_kind=='contig' and self.active_track!='consensus':
            messagebox.showinfo('Insert Bases','Select the consensus row before inserting bases.');return
        if self.view_kind=='contig':
            co=self.project.contigs[self.group_key]
            if co.stale:messagebox.showinfo('Rebuild Required','Rebuild the contig before editing.');return
            edits=co.insertions;index=str(self.selection)
        else:
            b=self.f_pane.mapping[self.selection]
            if b.raw is None:return
            edits=self.project.reads[self.read_id].insertions;index=str(b.raw)
        value=simpledialog.askstring('Insert Bases','Bases to insert before this position (leave empty to remove the insertion):',initialvalue=edits.get(index,''),parent=self)
        if value is None:return
        try:value=base_text(value,empty=True)
        except ValueError as exc:messagebox.showerror('Invalid Input',str(exc));return
        self.remember()
        if value:edits[index]=value
        else:edits.pop(index,None)
        if self.view_kind=='read':self.project.invalidate()
        else:self.project.dirty=True
        self.refresh_tree();self.refresh_view()

    def apply_metadata(self):
        if self.view_kind!='read' or not self.read_id:
            messagebox.showinfo('Select a Read','Select a read in the sequence list first.');return
        read=copy.deepcopy(self.project.reads[self.read_id])
        read.sample=self.meta_sample.get().strip();read.marker=self.meta_marker.get().strip();read.direction=self.meta_direction.get()
        try:validate_read(read,self.project.traces[read.id])
        except ValueError as e:messagebox.showerror('Invalid Input',str(e));return
        old=self.project.reads[read.id];old_key=self.project.key(old.sample,old.marker)
        new_key=self.project.key(read.sample,read.marker)
        orphan=old_key!=new_key and len(self.project.groups()[old_key])==1 and old_key in self.project.contigs
        if orphan and not messagebox.askyesno('Change Group','This will remove the previous group\'s contig.\nYou can restore it with Undo. Continue?'):return
        self.remember();self.project.reads[read.id]=read
        if orphan:self.project.contigs.pop(old_key)
        self.group_key=new_key;self.changed();return True

    def active_read(self):
        if self.view_kind=='read':return self.read_id
        pane=self.pane_for_track(self.active_track)
        return pane.read_id

    def reverse_complement(self):
        if not self.length():return
        if self.view_kind=='contig' and self.project.contigs[self.group_key].stale:
            messagebox.showinfo('Rebuild Required','Rebuild this contig before reversing it.');return
        self.remember()
        if self.view_kind=='contig':self.project.reverse_contig(self.group_key)
        else:
            read=self.project.reads[self.read_id];read.reverse=not read.reverse;self.project.dirty=True
        self.selection=self.length()-1-self.selection;self.anchor=self.selection
        self.refresh_tree();self.refresh_view();self.ensure_visible()

    def apply_trim(self):
        rid=self.active_read()
        if not rid:return
        try:
            start=int(self.trim_left.get())-1;end=int(self.trim_right.get())
            self.set_trim(rid,start,end)
        except ValueError as e:messagebox.showerror('Invalid Range',str(e))

    def set_trim(self,rid,start,end):
        read=copy.deepcopy(self.project.reads[rid]);read.start=start;read.end=end
        validate_read(read,self.project.traces[rid])
        if start==end and not messagebox.askyesno('Empty Read','This will exclude every base. Continue?'):return
        self.remember();self.project.reads[rid]=read;self.changed()

    def cut(self,side):
        if self.view_kind!='read' or not self.read_id:
            messagebox.showinfo('Select a Read','Select a read before cutting. F and R reads are cut in their original AB1 orientation.');return
        lo,hi=sorted((self.selection,self.anchor));mapping=self.f_pane.mapping
        positions=[b.raw for b in mapping[lo:hi+1] if b.raw is not None]
        if not positions:return
        read=self.project.reads[self.read_id]
        if read.reverse:side='R' if side=='L' else 'L'
        start=max(read.start,max(positions)+1) if side=='L' else read.start
        end=min(read.end,min(positions)) if side=='R' else read.end
        if start>end:
            self.status.set('Selection is outside the retained sequence.');return
        try:self.set_trim(read.id,start,end)
        except ValueError as e:messagebox.showerror('Invalid Range',str(e))

    def sync_settings(self):
        for key,(var,scale) in self.setting_vars.items():var.set(f'{self.project.settings[key]*scale:g}')

    def apply_settings(self):
        try:
            settings={key:(int(var.get()) if scale==1 else float(var.get())/scale) for key,(var,scale) in self.setting_vars.items()}
            validate_settings(settings)
        except (ValueError,TypeError) as exc:messagebox.showerror('Invalid Settings',str(exc));return
        if settings==self.project.settings:return True
        self.remember();self.project.settings=settings;self.changed()
        self.status.set('Settings applied. Rebuild existing contigs before export.')
        return True

    def import_files(self):
        paths=filedialog.askopenfilenames(title='Import AB1',filetypes=[('ABI chromatograms','*.ab1')])
        if paths:self.import_paths(paths)
        self.focus_track()

    def selected_reads(self):
        return list(dict.fromkeys(self.resolve_read(i) for i in self.tree.selection() if self.resolve_read(i) in self.project.reads))

    def resolve_read(self,item):return getattr(self,'tree_reads',{}).get(item,item)

    def selected_items(self):
        result=[]
        for item in self.tree.selection():
            rid=self.resolve_read(item)
            target=('read',rid) if rid in self.project.reads else ('contig',self.tree_groups[item]) if item in self.tree_groups else None
            if target is not None and target not in result:result.append(target)
        return result

    def selected_records(self):
        items=self.selected_items()
        if not items:raise ValueError('Select one or more reads or contigs in the sequence list.')
        return [self.project.fasta_record(kind,identifier) for kind,identifier in items]

    def put_clipboard(self,text,description):
        self.clipboard_clear();self.clipboard_append(text)
        self.status.set(description)

    def copy_selected_sequences(self):
        try:records=self.selected_records()
        except ValueError as exc:self.status.set(str(exc));return
        self.put_clipboard('\n'.join(seq for name,seq in records),f'Copied {len(records)} sequence(s).')

    def copy_sequence(self):
        if self.focus_get() is self.tree:self.copy_selected_sequences();return
        if not self.canvas_focus():
            widget=self.focus_get()
            if widget is not None and widget.winfo_class() in ('Entry','TEntry','Text','TSpinbox'):
                widget.event_generate('<<Copy>>');return
        if not self.length():self.status.set('Select a sequence first.');return
        lo,hi=sorted((self.selection,self.anchor));track=self.editing_track()
        if self.view_kind=='contig' and track=='consensus':
            co=self.project.contigs[self.group_key]
            text=''.join(co.insertions.get(str(i),'')+co.call(i) for i in range(lo,hi+1))
            if hi==len(co.columns)-1:text+=co.insertions.get(str(len(co.columns)),'')
        else:
            pane=self.pane_for_track(track) if self.view_kind=='contig' else self.f_pane
            text=''.join(b.base for b in pane.mapping[lo:hi+1] if b is not None and b.base not in ('','-'))
        text=text.replace('-','')
        self.put_clipboard(text,f'Copied {len(text)} selected base(s).')

    def copy_fasta(self,selected=None):
        try:
            if selected is None:selected=self.focus_get() is self.tree
            records=self.selected_records() if selected else [self.current_fasta()]
            text=fasta_text(records)
        except ValueError as exc:self.status.set(str(exc));messagebox.showinfo('Copy FASTA',str(exc));return
        self.put_clipboard(text,f'Copied {len(records)} FASTA record(s); displayed base calls are preserved.')

    def export_selected(self):
        try:records=self.selected_records();text=fasta_text(records)
        except ValueError as exc:messagebox.showinfo('Export FASTA',str(exc));return
        if not self.confirm_export_issues(self.selected_items()):return
        filters={} if sys.platform=='darwin' else {'filetypes':[('FASTA','*.fasta')]}
        name=records[0][0] if len(records)==1 else 'selected_sequences'
        path=filedialog.asksaveasfilename(title='Export Selected FASTA',initialfile=default_filename('fasta'),**filters)
        self.focus_track()
        if path:
            try:atomic_text(self.project.validate_output_path(path),text)
            except Exception as exc:messagebox.showerror('Export Failed',str(exc));return
            self.status.set(f'Exported {len(records)} selected sequence(s): {Path(path).name}')

    def confirm_export_issues(self,items):
        flagged=any(self.project.contigs[i].issues() if kind=='contig' else self.project.read_issues(i) for kind,i in items)
        return not flagged or self.confirm_action('export_review_flags','Review Flags',
            'Review flags remain. Export the displayed calls, including any low-quality or N bases?','Export')

    def build_manual(self):
        ids=self.selected_reads();key=None
        name=self.project.next_contig_name()
        if not ids and self.view_kind=='contig' and self.group_key in self.project.contigs:
            key=self.group_key;co=self.project.contigs[key];ids=co.read_ids();name=co.sequence_name
            if not self.confirm_rebuild([key]):return
        if len(ids)<2:
            messagebox.showinfo('Build Contig (manual)',shortcut_label('Shift-click two or more reads, then press Cmd+B.\nSelect reads from the same specimen and locus.'));return
        entry=self.history_entry();self.configure(cursor='watch');self.update_idletasks()
        try:key=self.project.assemble_reads(ids,name,key)
        except Exception as exc:messagebox.showerror('Assembly Failed',str(exc));return
        finally:self.configure(cursor='')
        self.remember(entry);self.refresh_tree();self.show_contig(key)
        item=next(i for i,k in self.tree_groups.items() if k==key)
        self.tree.selection_set(item);self.tree.see(item)

    def build_auto(self):
        # Match the unassembled AB1 section of the sidebar. A read remains
        # assigned even if its contig is stale or uses the legacy F/R fields;
        # AUTO must never rebuild an existing manual/automatic contig implicitly.
        assigned={rid for co in self.project.contigs.values() for rid in co.read_ids()}
        groups={}
        for rid,read in self.project.reads.items():
            if rid in assigned:continue
            prefix=Path(read.path).stem.split('_',1)[0]
            groups.setdefault(prefix,[]).append(rid)
        if not groups:
            self.status.set('No unassembled AB1 reads. Existing contigs were left unchanged.');return
        entry=self.history_entry();made=[];errors=[]
        self.configure(cursor='watch');self.update_idletasks()
        try:
            for prefix,ids in groups.items():
                # A newly imported group may reuse an earlier filename prefix.
                # Allocate a fresh key/name instead of replacing that contig.
                key=self.project.key(prefix,'auto');index=2
                while key in self.project.contigs:
                    key=self.project.key(prefix,f'auto_{index}');index+=1
                base=re.sub(r'[\s>|\x00-\x1f]','_',prefix);name=base;index=2
                names={co.sequence_name for co in self.project.contigs.values()}
                while name in names:name=f'{base}_{index}';index+=1
                try:made.append(self.project.assemble_reads(ids,name,key))
                except Exception as exc:errors.append(f'{prefix}: {exc}')
        finally:self.configure(cursor='')
        if made:self.remember(entry)
        self.refresh_tree()
        if made:self.show_contig(made[0])
        messagebox.showinfo('Build Contig (AUTO)',f'Built {len(made)} contigs from unassembled AB1 reads.\nGroups use the filename before the first underscore. Existing contigs were left unchanged.\n'+
            ('\nNot assembled:\n'+'\n'.join(errors[:12]) if errors else ''))

    def unify_orientations(self):
        if not self.project.contigs:self.status.set('Build a contig first.');return
        entry=self.history_entry();changed=0;matched=0
        self.configure(cursor='watch');self.update_idletasks()
        try:
            for key in self.project.contigs:
                result=self.project.normalize_orientation(key)
                if result:matched+=1;changed+=bool(result[1])
        finally:self.configure(cursor='')
        if changed:self.remember(entry)
        self.refresh_tree();self.refresh_view()
        self.status.set(f'Orientation: reversed {changed}; matched {matched}. Each locus follows its first matching contig; unrelated/ambiguous matches are not forced.')

    def preferences_dialog(self):
        window,body=self.panel('Settings')
        for row,(label,action) in enumerate((('Trimming Settings…',self.trimming_dialog),('Assembly Settings…',self.settings_dialog),('Find Settings…',self.find_settings_dialog),('Appearance…',self.appearance_dialog),('Confirmations…',self.confirmations_dialog))):
            def open_panel(command=action):window.destroy();command()
            ttk.Button(body,text=label,command=open_panel,width=25).grid(row=row,column=0,pady=5)
        ttk.Label(body,text='Recovery snapshots: every 30 seconds while edits are unsaved.\nOriginal projects are not overwritten.').grid(row=5,column=0,pady=12)

    def autosave_recovery(self):
        try:self.recovery.write(self.project,self.revision)
        except Exception as exc:self.status.set(f'Recovery snapshot failed: {exc}')
        self.after(30000,self.autosave_recovery)

    def offer_recovery(self):
        if not self.winfo_viewable():self.after(250,self.offer_recovery);return
        candidates=self.recovery.candidates()
        if not candidates:return
        _,path,metadata=candidates[0]
        name=Path(metadata['original_path']).name if metadata.get('original_path') else 'Untitled project'
        try:
            accepted=messagebox.askyesno('Recover Unsaved Project',f'Unsaved work was found: {name}.\nRecover the latest snapshot?\n\nChoosing No keeps it available under File > Recover Unsaved Project.',parent=self)
        except tk.TclError:
            self.status.set('Unsaved work was found. Use File > Recover Unsaved Project to restore it.');return
        if accepted:
            self.recover_project(path)

    def recover_project(self,path=None):
        if path is None:
            path=filedialog.askopenfilename(title='Recover Unsaved Project',initialdir=self.recovery.directory,filetypes=[('JSON','*.json')])
            if not path:return
        try:project=self.recovery.load(path)
        except Exception as exc:messagebox.showerror('Recovery Failed',str(exc));return
        if not self.confirm_unsaved():return
        try:self.recovery.adopt(path,project,self.revision+1)
        except Exception as exc:messagebox.showerror('Recovery Failed',str(exc));return
        self.revision+=1;self.project=project;self.undo_stack=[];self.redo_stack=[]
        self.view_kind='read';self.read_id=None;self.group_key=None
        self.sync_settings();self.refresh_tree()
        if project.contigs:self.show_contig(next(iter(project.contigs)))
        elif project.reads:self.show_read(next(iter(project.reads)))
        else:self.refresh_view()
        self.update_title();self.status.set(shortcut_label('Recovered unsaved work. Use Cmd+S to save the project. Undo history is not recovered.'))

    def clear_recovery(self):
        recovery=getattr(self,'recovery',None)
        if recovery is not None:
            try:recovery.clear()
            except OSError as exc:self.status.set(f'Could not remove the recovery snapshot: {exc}')

    def import_paths(self,paths):
        self.configure(cursor='watch');self.update_idletasks()
        entry=self.history_entry()
        added,errors=self.project.add(paths)
        if added:self.remember(entry)
        self.configure(cursor='');self.refresh_tree();self.update_title()
        if added:
            self.tree.selection_set(added[0]);self.tree.focus(added[0]);self.show_read(added[0])
        self.status.set(f'Imported {len(added)} reads | {len(self.project.reads)} total')
        if errors:messagebox.showwarning('Import Errors','\n'.join(errors[:12]))

    def confirm_rebuild(self,keys):
        edited=[self.project.contigs[k] for k in keys if k in self.project.contigs and (self.project.contigs[k].overrides or self.project.contigs[k].insertions or self.project.contigs[k].black_marks)]
        return not edited or messagebox.askyesno('Rebuild Contig','Rebuilding will replace consensus edits and review flags.\nYou can restore them with Undo. Continue?')

    def build_current(self):
        if not self.group_key:return
        if not self.confirm_rebuild([self.group_key]):return
        self.remember()
        try:co=self.project.assemble(self.group_key)
        except Exception as e:messagebox.showerror('Assembly Failed',str(e));return
        self.refresh_tree();self.show_contig(self.group_key);self.status.set(f'{co.sequence_name} | {len(co.issues())} review flags')

    def build_all(self):
        keys=list(self.project.groups())
        if not keys or not self.confirm_rebuild(keys):return
        self.remember();self.configure(cursor='watch');self.update_idletasks();errors=[];made=[]
        for key in keys:
            try:self.project.assemble(key);made.append(key)
            except Exception as e:errors.append(f'{" / ".join(json.loads(key))}: {e}')
        self.configure(cursor='');self.refresh_tree()
        if made:self.show_contig(made[0])
        self.status.set(f'Built {len(made)} contigs | {len(errors)} groups not assembled')
        if errors:messagebox.showwarning('Assembly Warnings','\n'.join(errors[:15]))

    def confirm_unsaved(self):
        if not self.project.dirty:return True
        result=messagebox.askyesnocancel('Unsaved Changes','Save changes to this project?')
        if result is None:return False
        return self.save_project() if result else True

    def new_project(self):
        if not self.confirm_unsaved():return
        self.clear_recovery()
        self.project=Project();self.undo_stack=[];self.redo_stack=[];self.read_id=None;self.group_key=None;self.view_kind='read'
        self.sync_settings();self.refresh_tree();self.refresh_view();self.status.set('Drop AB1 files or a project into this window.')

    def open_project(self):
        # Aqua Tk cannot safely resolve compound suffixes as macOS UTTypes.
        # Filter by the final JSON extension; validate the project on loading.
        path=filedialog.askopenfilename(title='Open Project',filetypes=[('JSON','*.json')])
        if path:self.load_project_path(path)
        self.focus_track()

    def load_project_path(self,path):
        try:project=Project.load(path)
        except Exception as e:messagebox.showerror('Cannot Open Project',str(e));return
        if not self.confirm_unsaved():return
        self.clear_recovery()
        self.project=project;self.undo_stack=[];self.redo_stack=[];self.read_id=None;self.group_key=None
        self.sync_settings();self.refresh_tree()
        if project.contigs:self.show_contig(next(iter(project.contigs)))
        elif project.reads:self.show_read(next(iter(project.reads)))
        else:self.refresh_view()
        self.update_title();self.status.set('Project opened.')

    def save_project_as(self):return self.save_project(save_as=True)

    def save_project(self,as_new=False,*,save_as=False):
        path=self.project.path
        if path is None or as_new or save_as:
            name=filedialog.asksaveasfilename(title='Export Project Copy' if as_new else 'Save Project As',defaultextension='.json',initialfile=default_filename('json'),filetypes=[('JSON','*.json')])
            self.focus_track()
            if not name:return False
            path=Path(name)
        try:
            if as_new:
                if self.project.path is not None and Path(path).resolve()==self.project.path.resolve():
                    raise ValueError('Choose a different filename for the exported copy.')
                self.project.export_project(path)
            else:self.project.save(path);self.clear_recovery()
        except Exception as e:messagebox.showerror('Save Failed',str(e));return False
        self.update_title();self.status.set(f'{"Exported copy" if as_new else "Saved"}: {Path(path).name}');return True

    def current_fasta(self):
        if self.view_kind=='contig' and self.group_key in self.project.contigs:
            return self.project.fasta_record('contig',self.group_key)
        if self.read_id:
            return self.project.fasta_record('read',self.read_id)
        raise ValueError('Select a read or contig to export.')

    def export_current(self):
        try:
            name,seq=self.current_fasta();text=fasta_text([(name,seq)])
        except ValueError as e:messagebox.showinfo('Export FASTA',str(e));return
        item=('contig',self.group_key) if self.view_kind=='contig' else ('read',self.read_id)
        if not self.confirm_export_issues([item]):return
        filters={} if sys.platform=='darwin' else {'filetypes':[('FASTA','*.fasta')]}
        path=filedialog.asksaveasfilename(title='Export FASTA',initialfile=default_filename('fasta'),**filters)
        self.focus_track()
        if not path:return
        try:atomic_text(self.project.validate_output_path(path),text)
        except Exception as e:messagebox.showerror('Export Failed',str(e));return
        self.status.set(f'Exported FASTA: {Path(path).name}')

    def help_dialog(self):
        instructions=(
            '(1) Import AB1\nFile > Import AB1, or drop AB1 files here. Low-quality ends are trimmed automatically. Change the rules in Settings > Trimming Settings. Gray bases are excluded.\n\n'
            '(2) Build a contig manually\nShift-click two or more reads from the SAME specimen and locus. Press Cmd+B. Both orientations are tested; insufficient overlaps stop assembly. Used reads move under the contig, not out of the project.\n\n'
            '(3) Build contigs automatically (optional)\nAssembly > Build Contig (AUTO) uses only unused reads in AB1 files. It groups filenames before the first underscore and uses that prefix as the contig name. Existing contigs and their edits are never rebuilt. Each prefix must contain only one specimen/locus. Import alone never groups reads.\n\n'
            '(4) Check and edit\nClick a base; type to replace it. Backspace deletes it; press again to continue left. Editing a source read updates the consensus immediately. Cmd+Z undoes one operation; Shift+Cmd+Z redoes it.\n\n'
            '(5) Review quality\nCmd+E: next flag. Shift+Cmd+E: previous flag. Inspect the trace, then Cmd+Return to mark the selected bases reviewed; Cmd+M toggles a flag. This does not improve or change quality scores. Phred Q is original AB1 quality; Support Q is not a calibrated combined probability.\n\n'
            '(6) Adjust the view and cuts\nUse the bottom-right Base spacing / Trace height sliders. Scroll vertically for additional reads. Settings > Appearance changes base size/colors. Select read bases, then Cmd+L / Cmd+R to exclude the displayed left/right end. Edit > Reverse Complement reverses the current sequence view.\n\n'
            '(7) Names, sorting and copying\nDouble-click a sequence name to rename it. Double-click Sequences to sort ALL reads and contigs A–Z; repeat for Z–A. Double-click Length (bp) to sort numerically; repeat to reverse. Sorting never changes assembly reference order. Cmd+C copies selected bases or sidebar sequences. Right-click > Copy FASTA copies only the selected entries. Cuts/deletions are excluded; low-quality calls are not silently converted to N.\n\n'
            '(8) Save, export and recovery\nCmd+S names an untitled project on the first save, then overwrites that same file. The window title shows its filename. Shift+Cmd+S (Save Project As) changes the current filename. Export Project Copy does NOT change the current filename or save destination. Default JSON/FASTA filenames use today\'s YYYYMMDD_ prefix. Unsaved work is snapshotted every 30 seconds; reopen after a crash to recover. The latest edits and Undo history may be lost. Keep the original AB1 files.\n\n'
            '(9) Contig orientation\nNew contigs follow the first earlier, sufficiently matching contig. Assembly > Unify Contig Orientation applies this to existing contigs. Unrelated or ambiguous matches are left unchanged. This is relative orientation, not annotated genomic forward strand. Edit > Reverse Complement remains available.\n\n'
            '(10) Find a sequence\nSelect a contig and type DNA into the Find box above the consensus (Cmd+F). Matches are orange; both forward and reverse-complement strands are checked. Enter / Shift+Enter or the arrows visit next / previous matches; Cmd+G / Shift+Cmd+G also work. Esc clears the search. Settings > Find Settings sets minimum identity from 80 to 100% (default: exact). Identity is matching letters / query length, without gaps. IUPAC symbols match literally, not as wildcards. Searches use the edited consensus, including inserted bases and excluding deletions; they do not edit the sequence.\n\n'
            '(11) Return a contig to AB1 files\nSelect a contig, then right-click > Return Reads to AB1 (also in Assembly). This removes the contig, not its source reads. Source edits and cuts are kept; consensus-only edits and review flags are removed. Cmd+Z restores the whole contig. Shared reads stay under any remaining contig.\n\n'
            '(12) Confirmation choices\nDelete, Return Reads to AB1 and export-with-flags dialogs offer "Don\'t ask again for this action". The choice is saved only when you proceed. Settings > Confirmations re-enables each prompt. Unsaved changes and warnings before replacing consensus edits cannot be skipped this way.')
        window,body=self.panel('How To');window.resizable(True,True)
        text=tk.Text(body,width=76,height=28,wrap='word',font=(UI_FAMILY,12),fg='black',bg='white',padx=12,pady=10)
        scroll=ttk.Scrollbar(body,orient='vertical',command=text.yview);text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');text.pack(fill='both',expand=True)
        text.insert('1.0',shortcut_label(instructions));text.configure(state='disabled')
        window.bind('<Escape>',lambda e:window.destroy())

    def developer_dialog(self):
        messagebox.showinfo('Developer',f'{APP_NAME} ver {APP_VERSION}\n\n{DEVELOPER}\n{LOCATION}')

    def license_dialog(self):
        root=resource_root()
        try:notice='\n\n'.join((root/name).read_text(encoding='utf-8') for name in ('CITATION.txt','LICENSE.txt'))
        except OSError as exc:messagebox.showerror('License and Citation',str(exc));return
        window,body=self.panel('License and Citation');window.resizable(True,True)
        text=tk.Text(body,width=78,height=25,wrap='word',font=(UI_FAMILY,12),fg='black',bg='white',padx=12,pady=10)
        scroll=ttk.Scrollbar(body,orient='vertical',command=text.yview);text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');text.pack(fill='both',expand=True)
        text.insert('1.0',notice);text.configure(state='disabled')
        window.bind('<Escape>',lambda e:window.destroy())

    def close_project(self):
        if self.confirm_unsaved():self.clear_recovery();self.closing=True;self.destroy()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project');parser.add_argument('ab1',nargs='*')
    parser.add_argument('--smoke-test',metavar='REPORT.json',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.smoke_test:
        if args.project or args.ab1:parser.error('--smoke-test cannot open user files')
        from smoke_test import run_smoke_test
        return run_smoke_test(Editor,args.smoke_test)
    try:project=Project.load(args.project) if args.project else Project()
    except Exception as e:
        root=tk.Tk();root.withdraw();messagebox.showerror('Cannot Open Project',str(e));root.destroy();return 1
    app=Editor(project,allow_recovery=not (args.project or args.ab1))
    qa_log=os.environ.get('NAMIYOMI_QA_LOG') or os.environ.get('CHROMELIA_QA_LOG')
    if qa_log:
        from qa_probe import attach
        attach(app,qa_log)
    if args.ab1:app.import_paths(args.ab1)
    elif project.contigs:app.show_contig(next(iter(project.contigs)))
    elif project.reads:app.show_read(next(iter(project.reads)))
    app.mainloop();return 0


if __name__=='__main__':raise SystemExit(main())
