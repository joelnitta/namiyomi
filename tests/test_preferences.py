import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from preferences import ConfirmationPreferences, KNOWN_CONFIRMATIONS


class ConfirmationPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'namiyomi' / 'preferences.json'

    def write_payload(self, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload), encoding='utf-8')

    def assert_all_ask(self, preferences):
        for key in KNOWN_CONFIRMATIONS:
            self.assertTrue(preferences.should_ask(key), key)
        self.assertTrue(preferences.should_ask('unsaved_changes'))
        self.assertTrue(preferences.should_ask('security_warning'))

    def test_missing_file_defaults_to_asking_without_writing(self):
        preferences = ConfirmationPreferences(self.path)
        self.assert_all_ask(preferences)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.parent.exists())

    def test_default_path_and_environment_override(self):
        fake_home = Path(self.directory.name) / 'fake-home'
        with patch.dict(os.environ, {'NAMIYOMI_PREFERENCES_PATH': ''}), \
                patch('preferences.Path.home', return_value=fake_home), \
                patch('platform_support.sys.platform', 'darwin'):
            preferences = ConfirmationPreferences()
        self.assertEqual(preferences.path, fake_home / 'Library' /
                         'Application Support' / 'namiyomi' / 'preferences.json')
        with patch.dict(os.environ, {'NAMIYOMI_PREFERENCES_PATH': str(self.path)}):
            self.assertEqual(ConfirmationPreferences().path, self.path)
            explicit = self.path.with_name('explicit.json')
            self.assertEqual(ConfirmationPreferences(explicit).path, explicit)

    def test_choices_persist_independently_and_can_be_reenabled(self):
        preferences = ConfirmationPreferences(self.path)
        preferences.set_suppressed('delete_sequences', True)
        preferences.set_suppressed('return_to_ab1', True)
        loaded = ConfirmationPreferences(self.path)
        self.assertFalse(loaded.should_ask('delete_sequences'))
        self.assertFalse(loaded.should_ask('return_to_ab1'))
        self.assertTrue(loaded.should_ask('export_review_flags'))
        loaded.set_suppressed('delete_sequences', False)
        loaded = ConfirmationPreferences(self.path)
        self.assertTrue(loaded.should_ask('delete_sequences'))
        self.assertFalse(loaded.should_ask('return_to_ab1'))
        self.assertEqual(json.loads(self.path.read_text()), {
            'version': 1, 'suppressed_confirmations': ['return_to_ab1'],
        })
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    def test_invalid_json_or_encoding_defaults_to_asking_without_rewrite(self):
        self.path.parent.mkdir()
        for contents in (b'not json', b'{', b'\xff\xfe'):
            with self.subTest(contents=contents):
                self.path.write_bytes(contents)
                self.assert_all_ask(ConfirmationPreferences(self.path))
                self.assertEqual(self.path.read_bytes(), contents)

    def test_invalid_schema_never_suppresses_confirmations(self):
        malformed = [None, [], {}, {'version': 1},
                     {'version': 2, 'suppressed_confirmations': ['delete_sequences']},
                     {'version': True, 'suppressed_confirmations': ['delete_sequences']},
                     {'version': 1, 'suppressed_confirmations': 'delete_sequences'},
                     {'version': 1, 'suppressed_confirmations': [None]},
                     {'version': 1, 'suppressed_confirmations': [['delete_sequences']]},
                     {'version': 1, 'suppressed_confirmations': ['delete_sequences', 'unsaved_changes']},
                     {'version': 1, 'suppressed_confirmations': ['delete_sequences'], 'security_warning': False}]
        for payload in malformed:
            with self.subTest(payload=payload):
                self.write_payload(payload)
                self.assert_all_ask(ConfirmationPreferences(self.path))

    def test_unknown_keys_and_non_boolean_choices_are_rejected(self):
        preferences = ConfirmationPreferences(self.path)
        for key in ('unsaved_changes', 'security_warning', '', 'DELETE_SEQUENCES'):
            with self.subTest(key=key):
                self.assertTrue(preferences.should_ask(key))
                with self.assertRaises(ValueError):
                    preferences.set_suppressed(key, True)
        for value in (1, 0, None, 'true'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                preferences.set_suppressed('delete_sequences', value)
        self.assert_all_ask(preferences)
        self.assertFalse(self.path.exists())

    def test_reset_persists_all_confirmations_on(self):
        preferences = ConfirmationPreferences(self.path)
        for key in KNOWN_CONFIRMATIONS:
            preferences.set_suppressed(key, True)
        preferences.reset()
        self.assert_all_ask(preferences)
        self.assert_all_ask(ConfirmationPreferences(self.path))
        self.assertEqual(json.loads(self.path.read_text()), {
            'version': 1, 'suppressed_confirmations': [],
        })

    def test_write_failure_preserves_previous_memory_and_disk_choices(self):
        preferences = ConfirmationPreferences(self.path)
        preferences.set_suppressed('delete_sequences', True)
        original = self.path.read_bytes()
        with patch('preferences.atomic_text', side_effect=OSError('Disk full')):
            for operation in (
                lambda: preferences.set_suppressed('return_to_ab1', True),
                lambda: preferences.set_suppressed('delete_sequences', False),
                preferences.reset,
            ):
                with self.assertRaises(OSError):
                    operation()
                self.assertFalse(preferences.should_ask('delete_sequences'))
                self.assertTrue(preferences.should_ask('return_to_ab1'))
                self.assertEqual(self.path.read_bytes(), original)

    def test_directory_failure_preserves_memory_and_does_not_modify_file(self):
        self.path.parent.write_text('Not a directory', encoding='utf-8')
        preferences = ConfirmationPreferences(self.path)
        with self.assertRaises(OSError):
            preferences.set_suppressed('delete_sequences', True)
        self.assert_all_ask(preferences)
        self.assertEqual(self.path.parent.read_text(), 'Not a directory')


if __name__ == '__main__':
    unittest.main()
