"""
Test isolation for H-65's Beat task locks (test-only, never imported by
production code).

A lock left held by one test (one that takes a lock by hand, or a run cut
short) would make a later test's task skip: the later test would then pass
vacuously or fail for a reason that has nothing to do with it. So before
EVERY test, this process's beat-lock keys are deleted.

`isolate_beat_locks_per_test` wraps `SimpleTestCase._pre_setup`, which
every Django test case (SimpleTestCase, TestCase, TransactionTestCase)
runs before each test. The project's test runner installs it:

  * in the main process, before any test runs; forked parallel workers
    inherit it;
  * in spawned parallel workers too, through the parallel suite's
    `process_setup` hook (a spawned worker starts from a fresh import).

It deletes exact keys: one per lock name this process has created (every
lock goes through BeatLock or the decorator, which record the name), so it
never walks the keyspace. The keys live under this process's own cache
prefix (H-9), so a concurrent run's locks are never touched.
"""

from django.test import SimpleTestCase

_MARKER = "_clears_beat_locks"


def clear_beat_locks():
    """Delete this process's beat-lock keys: exactly the ones for every lock
    name the process has created (no keyspace walk), under its own cache
    prefix. Returns how many existed."""
    from django_redis import get_redis_connection

    from AutoGrader.beat_locks import known_lock_names, lock_key

    keys = [lock_key(name) for name in sorted(known_lock_names())]
    if not keys:
        return 0
    return get_redis_connection("default").delete(*keys)


def isolate_beat_locks_per_test():
    """Clear beat-lock keys before every Django test. Idempotent."""
    original = SimpleTestCase.__dict__["_pre_setup"].__func__
    if getattr(original, _MARKER, False):
        return

    def _pre_setup(cls):
        # Best effort: with Redis down, the tests that need it fail on
        # their own, for the right reason.
        try:
            clear_beat_locks()
        except Exception:  # noqa: BLE001
            pass
        return original(cls)

    setattr(_pre_setup, _MARKER, True)
    SimpleTestCase._pre_setup = classmethod(_pre_setup)  # type: ignore[attr-defined]


def is_installed():
    return getattr(SimpleTestCase.__dict__["_pre_setup"].__func__, _MARKER, False)
