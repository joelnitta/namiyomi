"""Local UI confirmation choices, separate from saved sequence projects.

Only the explicitly listed, reversible workflow confirmations may be skipped.
Unknown keys (including unsaved-project and security prompts) always ask.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from core import atomic_text
from platform_support import application_support_directory


KNOWN_CONFIRMATIONS = frozenset({
    'delete_sequences',
    'return_to_ab1',
    'export_review_flags',
})


class ConfirmationPreferences:
    """Persist opt-out choices atomically; unreadable/invalid data fails closed.

    Callers should handle write errors before proceeding as though a choice was
    saved. A failed write leaves the in-memory choices unchanged.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None):
        if path is None:
            path = os.environ.get('NAMIYOMI_PREFERENCES_PATH') or (
                application_support_directory() / 'preferences.json')
        self.path = Path(path).expanduser()
        self._suppressed: set[str] = set()
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            if (
                not isinstance(payload, dict)
                or set(payload) != {'version', 'suppressed_confirmations'}
                or type(payload['version']) is not int
                or payload['version'] != 1
                or not isinstance(payload['suppressed_confirmations'], list)
            ):
                return
            values = payload['suppressed_confirmations']
            if any(not isinstance(key, str) or key not in KNOWN_CONFIRMATIONS
                   for key in values):
                return
            self._suppressed = set(values)
        except (OSError, UnicodeError, ValueError, RecursionError):
            # Missing, damaged, or inaccessible preferences must never hide a
            # confirmation. Do not rewrite the user's file just by opening it.
            pass

    def should_ask(self, key: str) -> bool:
        return key not in KNOWN_CONFIRMATIONS or key not in self._suppressed

    def set_suppressed(self, key: str, suppressed: bool):
        if key not in KNOWN_CONFIRMATIONS:
            raise ValueError(f'Unknown confirmation preference: {key!r}')
        if type(suppressed) is not bool:
            raise ValueError('The suppressed choice must be True or False.')
        updated = self._suppressed.copy()
        if suppressed:
            updated.add(key)
        else:
            updated.discard(key)
        if updated != self._suppressed:
            self._write(updated)

    def reset(self):
        """Restore every confirmation, without touching project data."""
        self._write(set())

    def _write(self, suppressed: set[str]):
        payload = {'version': 1, 'suppressed_confirmations': sorted(suppressed)}
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_text(self.path, json.dumps(payload, indent=2) + '\n')
        self._suppressed = suppressed
