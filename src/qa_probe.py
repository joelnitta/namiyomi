"""Opt-in native-input diagnostics; never synthesizes input or edits data."""
import json


def attach(editor,path):
    handle=open(path,'a',encoding='utf-8')
    tag='namiyomiInputProbe'
    def report(event):
        handle.write(json.dumps(dict(widget=str(event.widget),type=str(event.type),
            key=event.keysym,state=event.state,x=event.x,y=event.y,
            focus=str(editor.focus_get()),selected=editor.tree.selection(),
            active=editor.active_track,cursor=editor.selection,undo=len(editor.undo_stack),redo=len(editor.redo_stack)))+'\n')
        handle.flush()
    def register(widget):
        widget.bindtags((tag,)+widget.bindtags())
        for child in widget.winfo_children():register(child)
    register(editor)
    editor.bind_class(tag,'<KeyPress>',report)
    editor.bind_class(tag,'<ButtonPress-1>',report)
