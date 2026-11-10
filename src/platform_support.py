"""Small platform adaptations; importing this module never creates a window."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys


def resource_root():
    """Locate shared assets/notices in a checkout or a frozen application."""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def application_support_directory():
    """Use a per-user, non-roaming directory for local snapshots/preferences."""
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'namiyomi'
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA')
        return (Path(local) if local else Path.home() / 'AppData' / 'Local') / 'namiyomi'
    state = os.environ.get('XDG_STATE_HOME')
    return (Path(state) if state else Path.home() / '.local' / 'state') / 'namiyomi'


def shortcut_label(value):
    return value.replace('Cmd+', 'Ctrl+') if sys.platform != 'darwin' else value


def menu_accelerator(value):
    """Aqua needs Tk's Command syntax; Windows menus should display Ctrl."""
    modifier = 'Command-' if sys.platform == 'darwin' else 'Ctrl+'
    shift = 'Shift-' if sys.platform == 'darwin' else 'Shift+'
    return value.replace('⇧', shift).replace('⌘', modifier)


def edit_modifier_mask():
    """Tk event.state bits that turn a letter key into a shortcut, not a base edit.

    Lock keys must not be included: Windows Tk reports Num Lock as 0x08 (Mod1)
    and Scroll Lock as 0x20, while Alt is 0x20000. On Aqua, 0x08 is Command and
    0x10 is Option. On X11, 0x08 is usually Alt and 0x10 Num Lock.
    """
    if sys.platform == 'darwin':
        return 0x04 | 0x08 | 0x10
    if sys.platform == 'win32':
        return 0x04 | 0x20000
    return 0x04 | 0x08


def context_menu_bindings():
    # Control-click is a macOS secondary click, but means multi-select on Windows.
    return ('<Button-2>', '<Button-3>', '<Control-Button-1>') if sys.platform == 'darwin' else ('<Button-3>',)


def windows_process_alive(pid, kernel=None):
    """Query existence without sending a signal (os.kill(pid, 0) kills on Windows).

    An inaccessible process is conservatively treated as alive. No terminate or
    write rights are requested. The optional kernel argument supports tests.
    """
    if kernel is None:
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        # ERROR_INVALID_PARAMETER means no process with that ID exists.
        return ctypes.get_last_error() != 87
    try:
        return kernel.WaitForSingleObject(handle, 0) != 0
    finally:
        kernel.CloseHandle(handle)
