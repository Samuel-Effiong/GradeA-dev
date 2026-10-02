"""Test runner that keeps Redis clean (`redis_test_hygiene`), blocks real
outbound network calls (`network_guard`, H-39) and clears Beat task locks
before every test (`testing.beat_locks`, H-65)."""

from django.test.runner import DiscoverRunner, ParallelTestSuite

from AutoGrader.network_guard import block_real_network_calls
from AutoGrader.redis_test_hygiene import redis_test_hygiene
from AutoGrader.testing.beat_locks import isolate_beat_locks_per_test


def _worker_setup(*args):
    """Runs first in a SPAWNED parallel worker, which starts from a fresh
    import (a forked one inherits the main process's setup)."""
    isolate_beat_locks_per_test()


class IsolatedParallelTestSuite(ParallelTestSuite):
    process_setup = _worker_setup


class RedisHygieneRunner(DiscoverRunner):
    parallel_test_suite = IsolatedParallelTestSuite

    def run_tests(self, *args, **kwargs):
        isolate_beat_locks_per_test()
        with redis_test_hygiene(), block_real_network_calls():
            return super().run_tests(*args, **kwargs)
