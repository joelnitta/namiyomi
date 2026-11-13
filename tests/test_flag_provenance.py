import unittest

from app import Editor
from core import Project
from test_core import demo_project
import test_interface
from waveform_review import review_sites


class FlagProvenanceTests(unittest.TestCase):
    def test_original_flag_remains_visible_after_ai_replacement_and_reopen(self):
        with demo_project() as (project, key, original):
            contig = project.contigs[key]
            resolved, unresolved = 5, 6
            for position in (resolved, unresolved):
                contig.columns[position].call = 'N'
                contig.columns[position].reason = 'conflict'
                self.assertEqual(contig.flag_color(position), 'black')

            contig.ai_review = {str(resolved): 'A', str(unresolved): 'N'}
            saved = original.with_name('Reviewed.sanger.json')
            project.export_project(saved)
            reopened = Project.load(saved).contigs[key]

            self.assertTrue(reopened.auto_flagged(resolved))
            self.assertEqual(reopened.call(resolved), 'A')
            self.assertEqual(reopened.flag_color(resolved), 'black')
            self.assertNotIn(resolved, reopened.issues())
            self.assertEqual(reopened.flag_color(unresolved), 'red')
            self.assertIn(unresolved, reopened.issues())

    def test_next_flag_visits_original_flag_and_manual_review_keeps_history(self):
        editor = test_interface.InterfaceTests().editing_controller()
        contig = editor.project.contigs[editor.group_key]
        resolved, unresolved = 101, 102
        for position in (resolved, unresolved):
            contig.columns[position].call = 'N'
            contig.columns[position].reason = 'conflict'
        contig.ai_review = {str(resolved): 'C', str(unresolved): 'N'}

        Editor.next_issue(editor)
        self.assertEqual(editor.selection, resolved)
        editor.mark_reviewed()
        self.assertIsNone(contig.flag_color(resolved))
        self.assertEqual(contig.call(resolved), 'C')

        Editor.next_issue(editor)
        self.assertEqual(editor.selection, unresolved)
        editor.mark_reviewed()
        self.assertEqual(contig.ai_review[str(unresolved)], 'N')
        self.assertIsNone(contig.flag_color(unresolved))
        self.assertNotIn(unresolved, contig.issues())
        self.assertFalse(any(site.column == unresolved for site in review_sites(editor.project)))

        editor.undo()
        restored = editor.project.contigs[editor.group_key]
        self.assertEqual(restored.flag_color(unresolved), 'red')


if __name__ == '__main__':
    unittest.main()
