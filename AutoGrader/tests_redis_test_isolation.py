"""H-9: concurrent test processes must not share Redis-backed test state.

**FIXED.** All four tests pass; the two acceptance tests below were
`expectedFailure` until the fix landed.

THE DEFECT
----------
`REDIS_LOCAL_URL` pointed every test run at Redis DB 0, and django-redis
implements `cache.clear()` as a raw `FLUSHDB`, which ignores `KEY_PREFIX`
entirely. Test `setUp`/`tearDown` methods call `clear()` constantly, so one
process destroyed every other process's cache, H-1 generation counters,
locks, throttle buckets and presence keys.

It produced a real regression failure:
`assignments.tests_pdf_cache.InvalidationScopeTest
.test_a_whole_course_of_saves_does_not_cool_one_warm_assignment` asserting
`None != b'%PDF-warm'`, reproducible only in a long multi-app run, and NOT
caused by the H-1 change first blamed for it.

THE REQUIREMENT
---------------
    Concurrent test sessions must not be able to modify, flush or
    invalidate one another's Redis-backed test state - Django cache,
    H-1 generation counters, cache locks, throttle state, and any
    Celery/Redis state sharing the instance.

THE FIX
-------
`AutoGrader/test_cache.py` plus a settings branch under `manage.py test`:
each test PROCESS gets its own key prefix, and `clear()` deletes only that
prefix instead of flushing the database. A prefix rather than one of Redis's
16 database slots, so it scales to CI parallelism without collisions.
"""

import subprocess
import sys

from django.core.cache import cache
from django.test import SimpleTestCase

from AutoGrader.cache_generation import SCOPE_USER, bump_generation, get_generation

WARM_KEY = "h9:isolation:warm-entry"
WARM_VALUE = "survives"

#: Run in a separate interpreter using the PROJECT'S OWN settings, with
#: `sys.argv` made to look like a test run.
#:
#: That last detail is load-bearing. The requirement is about two concurrent
#: TEST SESSIONS, so the helper has to be one - settings keys the per-process
#: cache isolation off `"test" in sys.argv`. An earlier version of this
#: helper ran as a plain script, so it picked up the PRODUCTION backend,
#: whose `clear()` is still a raw FLUSHDB, and appeared to prove the fix had
#: failed when it was actually measuring a different scenario.
#:
#: The narrower exposure that remains, and is out of scope by design: a
#: non-test process (a `manage.py shell`, a stray script) calling
#: `cache.clear()` still issues FLUSHDB and would wipe a running suite. That
#: is a developer action against the development cache, not a concurrent
#: test session.
FLUSH_IN_ANOTHER_PROCESS = """
import sys

sys.argv = ["manage.py", "test"]
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")
django.setup()
from django.conf import settings
from django.core.cache import cache

print(settings.CACHES["default"]["KEY_PREFIX"])
cache.clear()
"""


def flush_from_another_process():
    """Clear the cache from a separate OS process. Returns its cache URL."""
    result = subprocess.run(
        [sys.executable, "-c", FLUSH_IN_ANOTHER_PROCESS],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"helper process failed: {result.stderr[-400:]}")
    return result.stdout.strip().splitlines()[-1]


class RedisTestIsolationTests(SimpleTestCase):
    """Does another process sharing this Redis destroy our state?"""

    def tearDown(self):
        cache.delete(WARM_KEY)

    def test_the_mechanism_a_cache_clear_destroys_a_warm_entry(self):
        """The mechanism, demonstrated deterministically in-process.

        This is what a concurrent run does to an unrelated suite, and it is
        exactly the shape of the PDF-cache failure: store, something
        clears, read returns None.
        """
        cache.set(WARM_KEY, WARM_VALUE, 300)
        self.assertEqual(cache.get(WARM_KEY), WARM_VALUE)

        cache.clear()

        self.assertIsNone(
            cache.get(WARM_KEY),
            "cache.clear() left the entry - if this ever passes, the "
            "mechanism behind H-9 has changed and its diagnosis needs "
            "revisiting",
        )

    def test_generation_counters_are_also_destroyed_by_a_flush(self):
        """The blast radius is wider than the Django cache.

        H-1's generation counters live in the same instance. A concurrent
        flush resets them, which is the stale-revival failure H-1's design
        (§6) exists to prevent - so shared test Redis undermines H-1's
        central invariant too, not just the PDF cache.
        """
        entity = "h9-counter-probe"
        bump_generation(SCOPE_USER, entity)
        bumped = get_generation(SCOPE_USER, entity)
        self.assertGreater(bumped, 1)

        cache.clear()

        self.assertEqual(
            get_generation(SCOPE_USER, entity),
            1,
            "the counter survived a flush - re-check H-9's blast radius",
        )

    def test_a_concurrent_process_cannot_flush_our_cache(self):
        """THE ACCEPTANCE TEST FOR H-9 - now passing.

        A separate OS process, configured exactly as a concurrent test
        session, clears its cache. Our warm entry must survive.

        This was `expectedFailure` while every test run shared Redis DB 0
        and `clear()` was a raw FLUSHDB. It passes because settings now give
        each test PROCESS its own key prefix and a `clear()` scoped to that
        prefix (see AutoGrader/test_cache.py).
        """
        cache.set(WARM_KEY, WARM_VALUE, 300)
        self.assertEqual(cache.get(WARM_KEY), WARM_VALUE)

        other_process_cache_url = flush_from_another_process()

        self.assertEqual(
            cache.get(WARM_KEY),
            WARM_VALUE,
            "a separate test process flushed this process's cache (its "
            f"prefix was {other_process_cache_url!r}, ours is "
            f"{cache.key_prefix!r}). Concurrent test runs can therefore "
            "fail each other, which is H-9.",
        )

    def test_a_concurrent_process_cannot_reset_our_generation_counters(self):
        """Same requirement, for the H-1 invariant specifically.

        This is the half that matters most: a reset counter makes every
        superseded cache entry reachable again, which is the stale-revival
        failure H-1's design exists to prevent.
        """
        entity = "h9-cross-process-counter"
        bump_generation(SCOPE_USER, entity)
        expected = get_generation(SCOPE_USER, entity)

        flush_from_another_process()

        self.assertEqual(
            get_generation(SCOPE_USER, entity),
            expected,
            "a separate process reset our generation counter - superseded "
            "cache entries would become reachable again",
        )
