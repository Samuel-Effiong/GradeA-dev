"""
H-107: a parallel test worker that is terminated must die.

The project's test runner turns SIGTERM into SystemExit in the main
process (AutoGrader/redis_test_hygiene.py), so that a stopped run still
cleans up after itself. `manage.py test --parallel` forks its pool workers
from that process, and a forked worker inherits the handler.

When a pool is terminated, each worker is sent SIGTERM. If the signal
arrives while the worker is inside a test, the inherited handler raises
SystemExit there, and unittest's catch-all around a test body records it
as that test's error. The worker lives on, goes back to the task queue,
and waits for ever on the queue's read lock, which the terminating parent
holds; the parent waits for ever for the worker to exit. A run whose
parent left its result loop early (for any reason) then hangs with no
output instead of failing (2026-10-02 and 2026-10-05, H-91's regression).

So a pool worker must start with SIGTERM's default action. The suite class
does that in its own `init_worker`, which Django runs in every worker,
forked or spawned. (`process_setup` runs in spawned workers only.)
"""

import os
import signal
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase
from django.test.runner import ParallelTestSuite

from AutoGrader import redis_test_runner
from AutoGrader.redis_test_runner import IsolatedParallelTestSuite

JOINED = "THE POOL JOINED: THE WORKER DIED"
SURVIVED = "THE WORKER SURVIVED THE TERMINATE"

#: Run in a fresh interpreter: a pool cannot be started from inside one of
#: `--parallel`'s own workers, and this must not touch the test's process.
#: The main process gets the runner's SIGTERM handler from the real
#: `redis_test_hygiene()` (its two Redis sweeps are replaced: they are not
#: what is under test). Django's own ParallelTestSuite.run then starts the
#: pool from our suite class and, once the worker is inside its test,
#: terminates it, exactly as it does when a run is told to stop.
POOL_PROBE = f"""
import multiprocessing, os, signal, sys, threading, time, unittest
from unittest import mock

import django

django.setup()

from AutoGrader import redis_test_hygiene as hygiene
from AutoGrader.redis_test_runner import IsolatedParallelTestSuite

WORKER_PID = multiprocessing.Value("i", 0)


class StillRunning(unittest.TestCase):
    def test_it_is_still_running_when_the_pool_is_terminated(self):
        WORKER_PID.value = os.getpid()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            time.sleep(0.05)


def stop_the_run_once_the_worker_is_in_its_test(result):
    while not WORKER_PID.value:
        time.sleep(0.05)
    time.sleep(0.3)
    result.shouldStop = True  # Django's loop then calls pool.terminate()
    time.sleep(10)
    # Still here: pool.join() has not returned, so the worker is alive.
    print({SURVIVED!r}, flush=True)
    os.kill(WORKER_PID.value, signal.SIGKILL)
    os._exit(3)


with mock.patch.object(hygiene, "sweep_dead_prefixes", lambda: None), mock.patch.object(
    hygiene, "delete_own_keys", lambda: None
), hygiene.redis_test_hygiene():
    assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL
    suite = IsolatedParallelTestSuite(
        [unittest.TestSuite([StillRunning("test_it_is_still_running_when_the_pool_is_terminated")])],
        1,
    )
    suite.used_aliases = set()  # no database in this probe
    result = unittest.TestResult()
    threading.Thread(
        target=stop_the_run_once_the_worker_is_in_its_test, args=(result,), daemon=True
    ).start()
    suite.run(result)
    print({JOINED!r}, "errors reported by the worker:", len(result.errors), flush=True)
"""


class ATerminatedPoolWorkerDiesTests(SimpleTestCase):
    def test_a_forked_worker_terminated_in_the_middle_of_a_test_dies(self):
        probe = subprocess.run(
            [sys.executable, "-c", POOL_PROBE],
            cwd=settings.BASE_DIR,
            env={
                **os.environ,
                "DJANGO_SETTINGS_MODULE": os.environ["DJANGO_SETTINGS_MODULE"],
            },
            capture_output=True,
            text=True,
            timeout=120,
        )

        shown = f"stdout:\n{probe.stdout}\nstderr:\n{probe.stderr[-3000:]}"
        self.assertNotIn(SURVIVED, probe.stdout, shown)
        self.assertEqual(probe.returncode, 0, shown)
        # It died of the signal: it did not finish its test and report it.
        self.assertIn(f"{JOINED} errors reported by the worker: 0", probe.stdout, shown)

    def test_the_setup_of_a_spawned_worker_restores_the_default_action_too(self):
        def handler(signum, frame):  # pragma: no cover - never delivered
            raise AssertionError("not expected")

        previous = signal.signal(signal.SIGTERM, handler)
        try:
            redis_test_runner._worker_setup()

            self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)
        finally:
            signal.signal(signal.SIGTERM, previous)


class TheFixCannotBeDroppedSilentlyTests(SimpleTestCase):
    """`init_worker` is a class attribute of Django's ParallelTestSuite, not
    a documented hook. If an upgrade renames it, or someone points ours
    back at Django's, this says so."""

    def test_django_still_starts_each_worker_through_init_worker(self):
        self.assertTrue(
            hasattr(ParallelTestSuite, "init_worker"),
            "Django's ParallelTestSuite has no init_worker any more: find "
            "where pool workers are initialised now and restore SIGTERM's "
            "default action there (H-107).",
        )

    def test_our_suite_starts_each_worker_through_our_own(self):
        ours = IsolatedParallelTestSuite.init_worker
        expected = getattr(redis_test_runner, "_init_worker", None)
        self.assertIsNotNone(
            expected, "AutoGrader.redis_test_runner has no _init_worker"
        )
        self.assertIs(getattr(ours, "__func__", ours), expected)
        self.assertIsNot(
            getattr(ours, "__func__", ours),
            getattr(
                ParallelTestSuite.init_worker,
                "__func__",
                ParallelTestSuite.init_worker,
            ),
        )
