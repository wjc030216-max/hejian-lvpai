"""Free-quota campaign: the counter must never oversell and must survive restarts."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase2.store import Store  # noqa: E402
from phase2 import free_quota  # noqa: E402


class FreeQuotaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self._tmp.name, 'phase2.sqlite3'))
        os.environ['PHASE2_FREE_QUOTA_TOTAL'] = '5'
        free_quota.reset(self.store)

    def tearDown(self):
        os.environ.pop('PHASE2_FREE_QUOTA_TOTAL', None)
        self._tmp.cleanup()

    def test_disabled_without_configuration(self):
        os.environ.pop('PHASE2_FREE_QUOTA_TOTAL', None)
        self.assertFalse(free_quota.consume(self.store))
        status = free_quota.status(self.store)
        self.assertFalse(status['enabled'])
        self.assertEqual(status['remaining'], 0)

    def test_consumes_exactly_the_configured_total(self):
        results = [free_quota.consume(self.store) for _ in range(7)]
        self.assertEqual(results, [True] * 5 + [False] * 2)
        status = free_quota.status(self.store)
        self.assertEqual(status['used'], 5)
        self.assertEqual(status['remaining'], 0)

    def test_remaining_decreases_by_one(self):
        self.assertTrue(free_quota.consume(self.store))
        self.assertEqual(free_quota.status(self.store)['remaining'], 4)

    def test_raising_total_adds_slots_without_losing_usage(self):
        for _ in range(5):
            self.assertTrue(free_quota.consume(self.store))
        os.environ['PHASE2_FREE_QUOTA_TOTAL'] = '8'
        status = free_quota.status(self.store)
        self.assertEqual(status['total'], 8)
        self.assertEqual(status['used'], 5)
        self.assertEqual(status['remaining'], 3)

    def test_consume_inside_caller_transaction(self):
        with self.store.transaction() as db:
            self.assertTrue(free_quota.consume(self.store, db))
        self.assertEqual(free_quota.status(self.store)['used'], 1)

    def test_resets_on_next_day(self):
        from unittest import mock
        with mock.patch.object(free_quota, 'today', return_value='2026-11-03'):
            for _ in range(3):
                self.assertTrue(free_quota.consume(self.store))
            self.assertEqual(free_quota.status(self.store)['remaining'], 2)
        with mock.patch.object(free_quota, 'today', return_value='2026-11-04'):
            status = free_quota.status(self.store)
            self.assertEqual(status['used'], 0)
            self.assertEqual(status['remaining'], 5)
            self.assertTrue(free_quota.consume(self.store))

    def test_concurrent_consumers_never_oversell(self):
        os.environ['PHASE2_FREE_QUOTA_TOTAL'] = '10'
        free_quota.reset(self.store)
        wins = []
        lock = threading.Lock()

        def worker():
            ok = free_quota.consume(self.store)
            with lock:
                wins.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(1 for ok in wins if ok), 10)
        self.assertEqual(free_quota.status(self.store)['used'], 10)


class MySqlResultTest(unittest.TestCase):
    def test_result_exposes_rowcount(self):
        try:
            from phase2.mysql_store import Result
        except ImportError as error:  # sqlalchemy missing in a slim environment
            self.skipTest(str(error))

        class Fake:
            rowcount = 1

        self.assertEqual(Result(Fake()).rowcount, 1)


if __name__ == '__main__':
    unittest.main()
