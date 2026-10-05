"""Test runner that keeps Redis clean (`redis_test_hygiene`), blocks real
outbound network calls (`network_guard`, H-39) and clears Beat task locks
before every test (`testing.beat_locks`, H-65)."""

import signal

from django.test.runner import DiscoverRunner, ParallelTestSuite

from AutoGrader.network_guard import block_real_network_calls
from AutoGrader.redis_test_hygiene import redis_test_hygiene
from AutoGrader.testing.beat_locks import isolate_beat_locks_per_test


def _die_on_sigterm():
    """Give this parallel worker SIGTERM's default action back (H-107).

    `redis_test_hygiene` turns SIGTERM into SystemExit in the main process,
    and a forked worker inherits that handler. Terminating the pool sends
    each worker SIGTERM; if it arrives in the middle of a test, unittest's
    catch-all around the test body records the SystemExit as a test error,
    the worker lives on, and it then waits for ever on the task queue's
    lock, which the terminating parent holds. The run hangs with no output.
    A worker has nothing of its own to clean up (the main process cleans
    Redis), so it must simply die."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _init_worker(*args, **kwargs):
    """The pool's initializer: runs first in EVERY parallel worker, forked
    or spawned. (`process_setup` below runs in spawned workers only.)"""
    _die_on_sigterm()
    return ParallelTestSuite.init_worker(*args, **kwargs)


def _worker_setup(*args):
    """Runs first in a SPAWNED parallel worker, which starts from a fresh
    import (a forked one inherits the main process's setup)."""
    _die_on_sigterm()
    isolate_beat_locks_per_test()


class IsolatedParallelTestSuite(ParallelTestSuite):
    init_worker = _init_worker
    process_setup = _worker_setup


class RedisHygieneRunner(DiscoverRunner):
    parallel_test_suite = IsolatedParallelTestSuite

    def run_tests(self, *args, **kwargs):
        isolate_beat_locks_per_test()
        with redis_test_hygiene(), block_real_network_calls():
            return super().run_tests(*args, **kwargs)
