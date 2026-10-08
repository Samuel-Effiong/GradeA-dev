"""
One correct way to run threads in a test.

WHY THIS EXISTS
---------------
`billing.tests.test_overage_purchase_integrity.ConcurrentOverageDeliveryTests`
failed intermittently with "0 != 500". Its workers were joined with
`t.join(timeout=60)` and nobody checked whether they had finished. One
worker was inside a live Stripe call (~80 s default timeout, longer than
the join), so its grant was written but NOT COMMITTED when the assertions
ran: the test reported a billing bug that did not exist.

A suite-wide sweep found the same shape in a dozen more places, plus two
neighbouring hazards that are just as quiet:

  * a worker that ends without closing its own connection keeps a Postgres
    backend open until garbage collection happens to reclaim it. The
    "other sessions using the database" teardown error catches this only
    when GC has not run first, so it hides real leaks;
  * a per-thread `join(timeout=...)` chain lets total waiting grow to
    count x timeout, which is why the flake took minutes to show up.

RULES THIS ENFORCES
-------------------
1. Workers start together (a barrier), so the race is actually raced.
2. One deadline for the whole group, not one timeout per thread.
3. A worker still alive at the deadline is a FAILURE, never a state to
   assert on.
4. Every worker closes its own DB connection in `finally`.
5. After the joins, the test database must hold no extra backends: the
   count has to return to its pre-run baseline.

USAGE
-----
    results, errors = run_concurrently(deliver, 20, test=self)

    self.assertEqual(errors, [])

`fn` is called with the worker index. `results[i]` holds what worker `i`
returned. Failures raise AssertionError, exactly as `self.fail()` does,
so a test method needs no extra plumbing. Pass `test=self` so a stuck
thread is reaped before the test-case teardown flushes the tables;
without it the reaping happens inline.
"""

import threading
import time

from django.db import connection

#: Waiting for every worker to reach the starting line.
DEFAULT_BARRIER_TIMEOUT = 30

#: Total wait for ALL workers to finish, shared as one deadline.
DEFAULT_JOIN_TIMEOUT = 60

#: How long a closed backend may take to disappear from pg_stat_activity.
BACKEND_SETTLE_TIMEOUT = 10

#: Last-resort wait for a stuck worker during cleanup.
REAP_TIMEOUT = 120


def other_backends():
    """Connections to this test database other than the caller's own."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
        return cursor.fetchone()[0]


def reap(threads, timeout=REAP_TIMEOUT):
    """Join whatever is left, so no live thread outlives the test."""
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))


def run_concurrently(
    fn,
    count,
    *,
    test=None,
    join_timeout=DEFAULT_JOIN_TIMEOUT,
    barrier_timeout=DEFAULT_BARRIER_TIMEOUT,
    name="worker",
    uses_db=True,
):
    """
    Run `fn(i)` in `count` threads released together; return
    `(results, errors)`.

    Raises AssertionError if any worker is still running at the deadline,
    or if the test database is left holding connections. Exceptions raised
    by `fn` are collected into `errors` for the caller to assert on — they
    are never swallowed silently.

    Set `uses_db=False` for workers that never touch the database (a
    SimpleTestCase, or a pure cache/renderer test): no connection is
    closed and no backend count is taken.
    """
    barrier = threading.Barrier(count)
    results = [None] * count
    errors = []

    def worker(i):
        try:
            barrier.wait(timeout=barrier_timeout)
            results[i] = fn(i)
        except Exception as exc:  # noqa: BLE001 - returned for assertion
            errors.append(exc)
        finally:
            if uses_db:
                # This thread opened it; nobody else can close it.
                connection.close()

    threads = [
        threading.Thread(target=worker, args=(i,), name=f"{name}-{i}")
        for i in range(count)
    ]
    backends_before = other_backends() if uses_db else 0
    if test is not None:
        test.addCleanup(reap, threads)

    try:
        for thread in threads:
            thread.start()

        deadline = time.monotonic() + join_timeout
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

        still_running = [t.name for t in threads if t.is_alive()]
        if still_running:
            raise AssertionError(
                f"{len(still_running)} of {count} worker thread(s) still "
                f"running after {join_timeout}s: {still_running}. Their "
                f"transactions may not have committed; refusing to assert "
                f"on partial state."
            )

        if uses_db:
            settle_by = time.monotonic() + BACKEND_SETTLE_TIMEOUT
            while (leaked := other_backends() - backends_before) > 0:
                if time.monotonic() > settle_by:
                    raise AssertionError(
                        f"{leaked} database session(s) still open after all "
                        f"{count} workers finished: a worker did not close "
                        f"its own connection."
                    )
                time.sleep(0.05)
    finally:
        if test is None:
            reap(threads)

    return results, errors
