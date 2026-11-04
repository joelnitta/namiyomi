"""Platform behavior tests run without a desktop or private sequence files."""
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from app import Editor
from core import Project
from platform_support import (application_support_directory, context_menu_bindings,
                              menu_accelerator, shortcut_label, windows_process_alive)
from recovery import RecoveryStore
from smoke_test import synthetic_project


class PlatformSupportTests(unittest.TestCase):
    def test_mac_paths_are_unchanged(self):
        with patch('platform_support.sys.platform','darwin'), \
             patch('platform_support.Path.home',return_value=Path('/test-home')):
            self.assertEqual(application_support_directory(),Path('/test-home/Library/Application Support/namiyomi'))

    def test_windows_localappdata_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)/'home';local=Path(directory)/'local'
            with patch('platform_support.sys.platform','win32'), \
                 patch('platform_support.Path.home',return_value=home), \
                 patch.dict(os.environ,{'LOCALAPPDATA':str(local)}):
                self.assertEqual(application_support_directory(),local/'namiyomi')
                self.assertEqual(RecoveryStore().directory,(local/'namiyomi/Recovery').resolve())
                with patch.dict(os.environ,{'LOCALAPPDATA':''}):
                    self.assertEqual(application_support_directory(),home/'AppData/Local/namiyomi')

    def test_windows_context_menu_keeps_ctrl_click_for_multi_selection(self):
        with patch('platform_support.sys.platform','win32'):
            self.assertEqual(context_menu_bindings(),('<Button-3>',))
            self.assertEqual(menu_accelerator('⇧⌘S'),'Shift+Ctrl+S')
            self.assertEqual(shortcut_label('Cmd+Z / Shift+Cmd+Z'),'Ctrl+Z / Shift+Ctrl+Z')
        with patch('platform_support.sys.platform','darwin'):
            self.assertIn('<Control-Button-1>',context_menu_bindings())
            self.assertEqual(menu_accelerator('⇧⌘S'),'Shift-Command-S')

    def test_windows_numlock_does_not_disable_base_input(self):
        controller=SimpleNamespace(canvas_focus=lambda:True,edit_base=Mock())
        with patch('app.sys.platform','win32'):
            self.assertEqual(Editor.key_edit(controller,SimpleNamespace(state=0x10,char='a')),'break')
            controller.edit_base.assert_called_once_with('A')
            controller.edit_base.reset_mock()
            self.assertIsNone(Editor.key_edit(controller,SimpleNamespace(state=0x04,char='c')))
            controller.edit_base.assert_not_called()

    def test_windows_recovery_queries_process_without_os_kill(self):
        with patch('recovery.sys.platform','win32'), \
             patch('recovery.windows_process_alive',return_value=True) as check, \
             patch('recovery.os.kill') as kill:
            self.assertTrue(RecoveryStore.process_alive(1234))
            check.assert_called_once_with(1234);kill.assert_not_called()
            for pid in (None,True,0,-1,'1234'):
                self.assertFalse(RecoveryStore.process_alive(pid))

    def test_windows_process_query_closes_handle_and_requests_no_write_access(self):
        kernel=Mock();kernel.OpenProcess.return_value=31
        for wait,expected in ((258,True),(0,False),(0xffffffff,True)):
            with self.subTest(wait=wait):
                kernel.reset_mock();kernel.WaitForSingleObject.return_value=wait
                self.assertEqual(windows_process_alive(1234,kernel),expected)
                kernel.OpenProcess.assert_called_once_with(0x101000,False,1234)
                kernel.WaitForSingleObject.assert_called_once_with(31,0)
                kernel.CloseHandle.assert_called_once_with(31)

    def test_windows_process_query_missing_and_access_denied(self):
        kernel=Mock();kernel.OpenProcess.return_value=None
        for error,expected in ((87,False),(5,True),(0,True)):
            with self.subTest(error=error), \
                 patch('platform_support.ctypes.get_last_error',return_value=error,create=True):
                self.assertEqual(windows_process_alive(1234,kernel),expected)
        kernel.CloseHandle.assert_not_called()

    @unittest.skipUnless(sys.platform=='win32','Win32 API check requires Windows')
    def test_real_windows_process_query_does_not_close_this_process(self):
        self.assertTrue(RecoveryStore.process_alive(os.getpid()))

    def test_project_cross_drive_save_uses_absolute_source_path(self):
        project,_,_=synthetic_project()
        with patch('core.os.path.relpath',side_effect=ValueError('different drives')):
            payload=project.payload(Path.cwd())
        for row in payload['reads']:
            self.assertEqual(row['path'],Path(project.reads[row['id']].path).resolve().as_posix())

    def test_generated_smoke_project_is_independent_of_local_files(self):
        project,key,sequence=synthetic_project()
        self.assertEqual(len(project.contigs[key].sources),2)
        self.assertEqual(len(sequence),280)
        self.assertEqual(project.contigs[key].sequence(),sequence)


if __name__=='__main__':unittest.main()
