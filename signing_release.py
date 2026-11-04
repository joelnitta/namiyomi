"""Fail-closed checks for Developer ID releases; no credentials are accessed here."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import uuid


IDENTITY_LINE = re.compile(r'^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"([^"]+)"\s*$')
SHA1 = re.compile(r'^[0-9A-Fa-f]{40}$')
DEVELOPER_ID_PREFIX = 'Developer ID Application: '


def resolve_identity(requested: str, listing: str) -> str:
    """Resolve an exact public certificate name or SHA-1 to one valid identity.

    ``listing`` must be stdout from ``security find-identity -v -p codesigning``.
    Its numbered entries are the currently usable (certificate + private key)
    identities; no Keychain passwords or private-key material are requested.
    """
    identities = {}
    for line in listing.splitlines():
        match = IDENTITY_LINE.fullmatch(line)
        if match:
            fingerprint, name = match.groups()
            if name.startswith(DEVELOPER_ID_PREFIX):
                identities[fingerprint.upper()] = name
    if SHA1.fullmatch(requested):
        matches = [requested.upper()] if requested.upper() in identities else []
    else:
        # Never accept a substring, generic "Developer ID", or another cert type.
        matches = [fingerprint for fingerprint, name in identities.items() if name == requested]
    if not matches:
        raise ValueError('No valid Developer ID Application identity exactly matches the requested name or SHA-1. '
                         'Install the certificate with its matching private key in an available Keychain.')
    if len(matches) != 1:
        raise ValueError('That Developer ID Application name matches multiple valid identities. '
                         'Specify the exact 40-character SHA-1 fingerprint instead.')
    return matches[0]


def submission_id(result: dict) -> str:
    if not isinstance(result, dict):
        raise ValueError('Notarization result is not a JSON object.')
    try:
        return str(uuid.UUID(result['id']))
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise ValueError('No valid submission ID was returned; a notarization log cannot be retrieved.') from exc


def validate_notarization(result: dict, log: dict) -> str:
    """Accept only matching Accepted result/log with no unresolved log issues."""
    job_id = submission_id(result)
    if not isinstance(log, dict):
        raise ValueError('Notarization log is not a JSON object.')
    try:
        log_id = str(uuid.UUID(log['jobId']))
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise ValueError('Notarization log has no valid job ID.') from exc
    if log_id != job_id:
        raise ValueError('Notarization result and log belong to different submissions.')
    result_status, log_status = result.get('status'), log.get('status')
    if result_status != 'Accepted' or log_status != 'Accepted':
        raise ValueError(f'Notarization was not Accepted (result={result_status!r}, log={log_status!r}); '
                         'see the saved result and log. Do not distribute.')
    issues = log.get('issues')
    if issues is not None and not isinstance(issues, list):
        raise ValueError('Notarization log contains malformed issue data; do not distribute.')
    if issues:
        raise ValueError(f'Apple accepted the submission but its log contains {len(issues)} issue(s). '
                         'Review and resolve the saved warnings/errors before distributing.')
    return job_id


def read_json(path: str) -> dict:
    with Path(path).open(encoding='utf-8') as handle:
        return json.load(handle)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    identity = commands.add_parser('resolve-identity')
    identity.add_argument('requested')
    submission = commands.add_parser('submission-id')
    submission.add_argument('result')
    check = commands.add_parser('check-notarization')
    check.add_argument('result')
    check.add_argument('log')
    args = parser.parse_args(argv)
    try:
        if args.command == 'resolve-identity':
            print(resolve_identity(args.requested, sys.stdin.read()))
        elif args.command == 'submission-id':
            print(submission_id(read_json(args.result)))
        else:
            print(validate_notarization(read_json(args.result), read_json(args.log)))
    except (ValueError, OSError) as exc:
        print(f'Release check failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
