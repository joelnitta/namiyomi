"""Offline release-preflight tests; no Keychain secrets or Apple uploads."""

import unittest

from signing_release import resolve_identity, submission_id, validate_notarization


A, B, C = 'A' * 40, 'B' * 40, 'C' * 40
NAME = 'Developer ID Application: Example Author (ABCDE12345)'
JOB = '2efe2717-52ef-43a5-96dc-0797e4ca1041'


def listing(*items):
    return '\n'.join(f'  {index}) {fingerprint} "{name}"' for index, (fingerprint, name) in enumerate(items, 1))


class IdentityTests(unittest.TestCase):
    def test_exact_name_and_fingerprint(self):
        text = listing((A, NAME))
        self.assertEqual(resolve_identity(NAME, text), A)
        self.assertEqual(resolve_identity(A.lower(), text), A)

    def test_empty_listing_and_missing_identity(self):
        with self.assertRaisesRegex(ValueError, 'No valid Developer ID Application'):
            resolve_identity(NAME, '     0 valid identities found\n')

    def test_rejects_substrings_and_other_certificate_types(self):
        text = listing((A, NAME), (B, 'Apple Development: Example Author (ABCDE12345)'),
                       (C, 'Developer ID Installer: Example Author (ABCDE12345)'))
        for request in ('Developer ID', 'Example Author', 'ABCDE12345', B, C):
            with self.subTest(request=request), self.assertRaises(ValueError):
                resolve_identity(request, text)

    def test_ambiguous_name_requires_fingerprint(self):
        text = listing((A, NAME), (B, NAME))
        with self.assertRaisesRegex(ValueError, 'multiple valid identities'):
            resolve_identity(NAME, text)
        self.assertEqual(resolve_identity(B, text), B)

    def test_same_identity_in_multiple_keychains_is_not_ambiguous(self):
        self.assertEqual(resolve_identity(NAME, listing((A, NAME), (A, NAME))), A)

    def test_invalid_or_annotated_entries_are_not_accepted(self):
        for text in (f'  1) {A} "{NAME}" (CSSMERR_TP_CERT_EXPIRED)',
                     f'{A} "{NAME}"', f'  1) not-a-sha "{NAME}"'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                resolve_identity(NAME, text)


class NotarizationTests(unittest.TestCase):
    def test_accepted_matching_clean_log(self):
        result = {'id': JOB, 'status': 'Accepted'}
        for issues in (None, []):
            self.assertEqual(validate_notarization(result, {'jobId': JOB.upper(), 'status': 'Accepted', 'issues': issues}), JOB)

    def test_rejected_or_pending_result_cannot_release(self):
        for status in ('Invalid', 'Rejected', 'In Progress', None):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'not Accepted'):
                validate_notarization({'id': JOB, 'status': status}, {'jobId': JOB, 'status': status, 'issues': []})

    def test_accepted_result_does_not_override_rejected_log(self):
        with self.assertRaisesRegex(ValueError, 'not Accepted'):
            validate_notarization({'id': JOB, 'status': 'Accepted'}, {'jobId': JOB, 'status': 'Invalid', 'issues': []})

    def test_stale_log_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'different submissions'):
            validate_notarization({'id': JOB, 'status': 'Accepted'},
                                 {'jobId': '00000000-0000-0000-0000-000000000000', 'status': 'Accepted'})

    def test_warnings_and_errors_stop_distribution_even_if_accepted(self):
        for severity in ('warning', 'error'):
            with self.subTest(severity=severity), self.assertRaisesRegex(ValueError, 'log contains 1 issue'):
                validate_notarization({'id': JOB, 'status': 'Accepted'},
                                     {'jobId': JOB, 'status': 'Accepted', 'issues': [{'severity': severity}]})

    def test_missing_or_invalid_submission_and_log_ids(self):
        for data in ({}, {'id': None}, {'id': 'not-a-uuid'}, []):
            with self.subTest(data=data), self.assertRaises(ValueError):
                submission_id(data)
        for log in (None, {}, {'jobId': None}, {'jobId': 'bad'}):
            with self.subTest(log=log), self.assertRaises(ValueError):
                validate_notarization({'id': JOB, 'status': 'Accepted'}, log)

    def test_malformed_issue_data_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'malformed issue'):
            validate_notarization({'id': JOB, 'status': 'Accepted'},
                                 {'jobId': JOB, 'status': 'Accepted', 'issues': 'unknown'})


if __name__ == '__main__':
    unittest.main()
