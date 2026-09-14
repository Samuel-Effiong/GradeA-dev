"""H-9: concurrent test processes must not share Redis-backed test state.

**FIXED, then REOPENED by a regression, then fixed again (2026-09-14).** The
original four tests below were `expectedFailure` until the first fix landed.
They passed, but they only exercised the project-wide settings branch.
Twelve modules that must run on real Redis wrote their own `CACHES`
override, which replaced that branch entirely: the unscoped backend (eleven
on a fixed database number with the shared `gaplus` prefix, one on the
default database), so `clear()` was FLUSHDB again. Two concurrent gates
collided on it and one was aborted.
`SuiteOverridesCannotBypassIsolationTests` pins the fix, a shared
`real_redis_caches()` helper, and fails if any test module reintroduces the
unscoped backend.

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

import ast
import os
import subprocess
import sys
import uuid
from pathlib import Path

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from AutoGrader.cache_generation import SCOPE_USER, bump_generation, get_generation
from AutoGrader.test_cache import real_redis_caches, test_key_prefix

WARM_KEY = "h9:isolation:warm-entry"
WARM_VALUE = "survives"

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Assembled at runtime so this module's own source does not match the scan.
UNSCOPED_REDIS_BACKEND = ".".join(("django_redis", "cache", "RedisCache"))

#: A fixed database number shared by BOTH processes, the shape of the
#: reintroduced defect. 14 because no test module uses it, so an unrelated
#: legacy flush elsewhere cannot fake a failure here. Which number is shared
#: does not matter to what the test proves.
SHARED_FIXED_DB = "redis://127.0.0.1:6379/14"

#: A second concurrent test session that clears a REAL-Redis suite override
#: on the same fixed database number.
FLUSH_OVERRIDE_IN_ANOTHER_PROCESS = """
import sys

sys.argv = ["manage.py", "test"]
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")
django.setup()
from django.core.cache import cache
from django.test import override_settings

from AutoGrader.test_cache import real_redis_caches

with override_settings(CACHES=real_redis_caches(os.environ["H9_LOCATION"])):
    print(cache.key_prefix)
    cache.clear()
"""

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


def _test_modules():
    for path in REPO_ROOT.rglob("*.py"):
        parts = path.relative_to(REPO_ROOT).parts
        if parts[0].startswith(".") or "node_modules" in parts or "migrations" in parts:
            continue
        if path.name.startswith("test") or "tests" in parts:
            yield path


class SuiteOverridesCannotBypassIsolationTests(SimpleTestCase):
    """The regression that reopened H-9 after it was closed.

    Twelve modules that must run on real Redis wrote their own `CACHES`
    override with the unscoped backend: eleven on a fixed database number
    with the shared `gaplus` prefix, one on the default database. Their `clear()` is FLUSHDB of that database, so two
    concurrent test runs of those modules wiped each other's cache entries
    and generation counters. The per-process settings branch never applied,
    because the override replaced it. It surfaced as a real aborted gate.
    """

    def test_no_test_module_configures_the_unscoped_redis_backend(self):
        offenders = []
        for path in _test_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and node.value == UNSCOPED_REDIS_BACKEND
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "these test modules configure the unscoped Redis backend, whose "
            "clear() flushes the whole database under every other concurrent "
            "test run - use AutoGrader.test_cache.real_redis_caches() instead",
        )

    def test_the_real_redis_override_is_scoped_to_this_process(self):
        config = real_redis_caches(SHARED_FIXED_DB)["default"]
        self.assertEqual(
            config["BACKEND"], "AutoGrader.test_cache.PrefixScopedRedisCache"
        )
        self.assertEqual(config["KEY_PREFIX"], test_key_prefix())
        self.assertIn(str(os.getpid()), config["KEY_PREFIX"])

    def test_another_process_clearing_the_same_fixed_db_cannot_wipe_ours(self):
        """THE ACCEPTANCE TEST for the regression.

        Both processes use a real-Redis suite override on the SAME fixed
        database number, the exact shape that collided. Our warm entry and
        generation counter must survive the other process's clear().
        """
        entity = "h9-fixed-db-counter"
        with override_settings(CACHES=real_redis_caches(SHARED_FIXED_DB)):
            cache.set(WARM_KEY, WARM_VALUE, 300)
            bump_generation(SCOPE_USER, entity)
            expected = get_generation(SCOPE_USER, entity)
            try:
                result = subprocess.run(
                    [sys.executable, "-c", FLUSH_OVERRIDE_IN_ANOTHER_PROCESS],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                    env={**os.environ, "H9_LOCATION": SHARED_FIXED_DB},
                )
                if result.returncode != 0:
                    raise RuntimeError(f"helper failed: {result.stderr[-400:]}")
                other_prefix = result.stdout.strip().splitlines()[-1]
                self.assertNotEqual(other_prefix, cache.key_prefix)

                self.assertEqual(
                    cache.get(WARM_KEY),
                    WARM_VALUE,
                    "another test process clearing the same fixed Redis "
                    "database wiped this process's entry",
                )
                self.assertEqual(get_generation(SCOPE_USER, entity), expected)
            finally:
                cache.clear()


#: A second concurrent test session that purges a broker queue with the SAME
#: name as one of ours.
PURGE_QUEUE_IN_ANOTHER_PROCESS = """
import sys

sys.argv = ["manage.py", "test"]
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")
django.setup()
from AutoGrader.celery import app

with app.connection_for_write() as conn:
    print(conn.transport_options.get("global_keyprefix", ""))
    conn.default_channel.queue_purge(os.environ["H9_QUEUE"])
"""


class CeleryBrokerIsolationTests(SimpleTestCase):
    """The Celery half of H-9.

    The real-worker tests use the project's Redis broker and result backend,
    which every concurrent test run shares. A per-run queue name keeps the
    task messages apart, but not the exchange bindings or kombu's global
    `unacked` hash and index. So each test process gets its own
    `global_keyprefix` for both, alongside its cache prefix.
    """

    def test_no_test_module_builds_its_own_broker_client(self):
        """A raw Redis client built from the broker URL bypasses the prefix.

        It reads and deletes bare key names, so under per-process isolation it
        silently touches nothing it meant to, or worse, another run's
        unprefixed keys. Broker access in tests must go through kombu
        (`celery_app.connection_for_write()`), which applies the prefix.
        """
        needle = "CELERY" + "_BROKER_URL"
        offenders = []
        for path in _test_modules():
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if needle in line and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
        self.assertEqual(
            offenders,
            [],
            "these test modules reach the broker through its raw URL instead of "
            "kombu, bypassing the per-process broker key prefix",
        )

    def test_the_broker_and_result_backend_are_prefixed_per_process(self):
        from django.conf import settings

        from AutoGrader.celery import app as celery_app

        expected = f"{test_key_prefix()}:"
        self.assertEqual(
            settings.CELERY_BROKER_TRANSPORT_OPTIONS["global_keyprefix"], expected
        )
        self.assertEqual(
            celery_app.conf.broker_transport_options["global_keyprefix"], expected
        )
        self.assertEqual(
            celery_app.conf.result_backend_transport_options["global_keyprefix"],
            expected,
        )
        self.assertTrue(
            celery_app.backend.task_keyprefix.startswith(expected.encode()),
            "the result backend did not apply the per-process prefix to its keys",
        )
        # Production redelivery behaviour is untouched: only the prefix is added.
        self.assertEqual(
            settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"], 3600
        )

    def test_another_process_purging_the_same_queue_cannot_touch_our_messages(self):
        """THE ACCEPTANCE TEST for broker isolation.

        Both processes use the same queue name on the same broker, the exact
        situation of two real-worker runs sharing a queue name. The other
        process's purge must not remove our message.
        """
        from AutoGrader.celery import app as celery_app

        queue = f"h9-shared-queue-{uuid.uuid4().hex[:8]}"
        with celery_app.connection_for_write() as conn:
            probe = conn.SimpleQueue(queue)
            # Counted with the channel's `_size()` (LLEN), NOT
            # `SimpleQueue.qsize()`. `qsize()` is a passive queue_declare,
            # whose existence check uses Redis EXISTS, which kombu's
            # `global_keyprefix` does not prefix, so it reports a prefixed
            # queue as missing. LLEN is prefixed, as are the commands real
            # publishing and consuming use.
            channel = conn.default_channel
            try:
                probe.put({"probe": "survives"})
                self.assertEqual(channel._size(queue), 1)

                result = subprocess.run(
                    [sys.executable, "-c", PURGE_QUEUE_IN_ANOTHER_PROCESS],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                    env={**os.environ, "H9_QUEUE": queue},
                )
                if result.returncode != 0:
                    raise RuntimeError(f"helper failed: {result.stderr[-400:]}")
                other_prefix = result.stdout.strip().splitlines()[-1]
                self.assertNotEqual(
                    other_prefix, conn.transport_options["global_keyprefix"]
                )

                self.assertEqual(
                    channel._size(queue),
                    1,
                    "another test process purging a queue of the same name "
                    "removed this process's broker message",
                )
            finally:
                probe.clear()
                probe.close()
