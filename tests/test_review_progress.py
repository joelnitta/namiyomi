import unittest

from review_progress import ReviewProgress


class ReviewProgressTests(unittest.TestCase):
    def test_in_flight_estimates_stay_monotonic_and_below_completion(self):
        progress = ReviewProgress(group_count=4, online=True, effort='medium', now=0)
        self.assertEqual(progress.estimate(0)[0], 0)
        progress.update('assembly', 1, 4, 1)
        self.assertEqual(progress.estimate(1)[0], 10)
        progress.update('approvals', 0, 2, 3)
        initial, eta = progress.estimate(3)
        later, shorter_eta = progress.estimate(5)
        self.assertLess(initial, later)
        self.assertLess(shorter_eta, eta)
        progress.update('approvals', 1, 2, 6)
        self.assertGreater(progress.estimate(6)[0], later)
        percentage, eta = progress.estimate(100)
        self.assertLess(percentage, 100)
        self.assertIsNone(eta)
        progress.update('applying', 0, 1, 101)
        self.assertLess(progress.estimate(105)[0], 100)

    def test_estimate_accounts_for_in_flight_parallel_requests(self):
        serial = ReviewProgress(0, True, 'high', 0)
        parallel = ReviewProgress(0, True, 'high', 0, parallel_requests=3)
        for progress in (serial, parallel):
            progress.update('approvals', 0, 9, 0)
        self.assertLess(parallel.estimate(0)[1], serial.estimate(0)[1])
