"""
Tests for AutoGrader.testing.concurrency.

The harness exists to stop concurrency tests from passing (or failing)
for the wrong reason, so each rule it enforces is tested by breaking it
here: a worker that outlives the join, a worker that leaks its database
connection, and a worker that raises.

TransactionTestCase: the harness's workers use real connections, and its
backend check reads pg_stat_activity, neither of which behaves correctly
inside TestCase's wrapping transaction.
"""

import threading

from django.db import connections
from django.test import TransactionTestCase

from AutoGrader.testing.concurrency import other_backends, run_concurrently


class RunConcurrentlyTests(TransactionTestCase):
    def test_every_worker_runs_and_results_keep_their_index(self):
        results, errors = run_concurrently(lambda i: i * 10, 8, test=self)

        self.assertEqual(errors, [])
        self.assertEqual(results, [0, 10, 20, 30, 40, 50, 60, 70])

    def test_workers_really_run_at_the_same_time(self):
        """The barrier, not luck: none may finish before all have started."""
        started = threading.Barrier(4, timeout=30)

        results, errors = run_concurrently(
            lambda i: started.wait(timeout=30) is not None, 4, test=self
        )

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 4)

    def test_an_exception_is_returned_not_swallowed(self):
        def explode(i):
            if i == 2:
                raise ValueError("worker 2 failed")
            return "ok"

        results, errors = run_concurrently(explode, 4, test=self)

        self.assertEqual([type(e) for e in errors], [ValueError])
        self.assertEqual(str(errors[0]), "worker 2 failed")
        self.assertIsNone(results[2], "a failed worker must not report a result")

    def test_a_worker_outliving_the_join_fails_and_names_the_thread(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def block(i):
            if i == 1:
                release.wait(timeout=60)
            return i

        with self.assertRaisesRegex(
            AssertionError, r"1 of 3 worker thread\(s\) still running after 2s"
        ) as caught:
            run_concurrently(block, 3, test=self, join_timeout=2, name="probe")

        self.assertIn("probe-1", str(caught.exception))
        self.assertIn("refusing to assert on partial state", str(caught.exception))

    def test_a_worker_that_leaves_its_connection_open_fails_the_run(self):
        """
        The guard that a GC-dependent teardown error cannot be trusted to
        catch. The worker below opens a connection and deliberately never
        closes it, so the run must fail rather than leak into teardown.
        """
        leaked = []
        lock = threading.Lock()

        def leak(i):
            # A second connection the harness's own close() cannot reach —
            # exactly what a worker that skips connection.close() leaves
            # behind, and referenced here so GC cannot quietly reclaim it.
            extra = connections.create_connection("default")
            with extra.cursor() as cursor:
                cursor.execute("SELECT 1")
            with lock:
                # The raw psycopg2 connection: a DatabaseWrapper may only be
                # closed by the thread that made it, and holding the raw one
                # keeps the backend alive exactly as a real leak would.
                leaked.append(extra.connection)
            return i

        try:
            with self.assertRaisesRegex(
                AssertionError,
                r"3 database session\(s\) still open after all 3 workers",
            ):
                run_concurrently(leak, 3, test=self)
        finally:
            for raw in leaked:
                raw.close()

    def test_a_clean_run_leaves_no_extra_backends(self):
        before = other_backends()

        run_concurrently(lambda i: None, 6, test=self)

        self.assertEqual(other_backends(), before)

    def test_workers_that_never_touch_the_database_need_no_connection(self):
        results, errors = run_concurrently(lambda i: i + 1, 4, test=self, uses_db=False)

        self.assertEqual(errors, [])
        self.assertEqual(results, [1, 2, 3, 4])
