"""Per-process cache isolation for the test suite (H-9).

THE PROBLEM
-----------
`REDIS_LOCAL_URL` points every test run at Redis DB 0, and django-redis's
`clear()` is implemented as a raw `FLUSHDB`:

    def clear(self, client=None):
        client.flushdb()

`FLUSHDB` ignores `KEY_PREFIX` entirely. So one process calling
`cache.clear()` - which test `setUp`/`tearDown` methods do constantly -
destroys every other process's cache, generation counters, locks, throttle
buckets and presence keys. A unique key prefix does NOT protect against it.

That produced a real regression failure: an unrelated PDF-cache assertion
in a 30-minute multi-app run, reproducible only in that window, and not
caused by the change it was first blamed on. Worse, a concurrent flush also
resets H-1's generation counters, which is exactly the stale-revival failure
`docs/H1_CACHE_INVALIDATION_DESIGN.md` §6 exists to prevent.

THE FIX
-------
Two halves, and both are needed:

1. a **per-process key prefix**, so two processes never write the same key;
2. a `clear()` that deletes **only this process's prefix** instead of
   flushing the database.

Scarce Redis database indices are deliberately NOT used as the isolation
mechanism. Redis ships with 16, so a PID-modulo scheme collides at ~1/16 for
two concurrent runs and cannot scale to CI parallelism. A prefix has no such
ceiling.

WHY A TEST-ONLY BACKEND IS ACCEPTABLE HERE
------------------------------------------
Only `clear()` differs from production, and `clear()` is a test utility - no
production code path calls it (verified by grep; production invalidation
goes through `delete_pattern` or generation bumps). Every other operation,
including `delete_pattern` and the `incr`/`SET NX` behaviour H-1's counters
rely on, is the real django-redis implementation against a real Redis.
"""

import logging
import os

from django_redis.cache import RedisCache

logger = logging.getLogger(__name__)


def test_key_prefix():
    """A prefix unique to this OS process.

    Includes the pid so concurrent runs cannot collide, and keeps the
    project's `gaplus` root so a stray key is still recognisable as ours.
    """
    return f"gaplus-t{os.getpid()}"


def real_redis_caches(location):
    """A `CACHES` override for a suite that must run on REAL Redis.

    Use this instead of hand-writing a `django_redis.cache.RedisCache`
    override. That hand-written form reintroduced H-9 in twelve modules
    (eleven on a fixed database number with the shared `gaplus` prefix, one
    on the default database). The unscoped backend's `clear()` is a raw
    FLUSHDB of the whole database, so two concurrent test runs wiped each
    other's cache and generation counters mid-test. It aborted a real gate
    on an overlap.

    A dedicated database number is NOT isolation: every run of the same
    module picks the same number. The per-process prefix plus a
    prefix-scoped `clear()` is, so `location` only chooses which Redis
    database the keys live in.

    `AutoGrader/tests_redis_test_isolation.py` fails the suite if any test
    module configures the unscoped backend again.
    """
    return {
        "default": {
            "BACKEND": "AutoGrader.test_cache.PrefixScopedRedisCache",
            "LOCATION": location,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
            "KEY_PREFIX": test_key_prefix(),
        }
    }


#: A test key that asked for "never expires" lives at most this long. It must
#: exceed the longest gate (the full suite has taken ~2.8 h) so a key never
#: expires under a running test; it exists so keys a killed run left behind
#: (nothing in-process runs on SIGKILL) still disappear on their own.
TEST_KEY_TTL_SECONDS = 12 * 60 * 60


def _is_generation_counter(key):
    # Imported lazily: cache_generation imports the cache at module load.
    from AutoGrader.cache_generation import GENERATION_KEY_PREFIX

    return str(key).startswith(f"{GENERATION_KEY_PREFIX}:")


class PrefixScopedRedisCache(RedisCache):
    """A `RedisCache` whose `clear()` cannot reach another process's keys.

    It also gives every "never expires" key a TTL (`TEST_KEY_TTL_SECONDS`),
    EXCEPT H-1 generation counters. A counter that expired would read back as
    DEFAULT_GENERATION and make every superseded entry reachable again, the
    stale-revival failure the counters exist to prevent, so counters keep the
    production behaviour (`timeout=None`) and are removed by the runner's
    teardown and dead-pid sweep instead (`AutoGrader/redis_test_hygiene.py`).

    `key_prefix` is a PROPERTY, not the plain attribute `BaseCache.__init__`
    assigns, because Django loads settings (and so calls `test_key_prefix()`)
    exactly once, in the parent process, before `manage.py test --parallel`
    forks its worker processes. Every forked child inherits that already-
    computed string via copy-on-write, so a plain attribute would give every
    worker the PARENT's pid, not its own - the exact bug this class exists
    to prevent, just moved from "no prefix" to "one shared prefix". A
    property re-runs `os.getpid()` on every access instead, and
    django_redis's client reads `self._backend.key_prefix` fresh on every
    `make_key()` call (see `django_redis/client/default.py`), so each
    worker's keys resolve to its own real, live pid regardless of which
    process originally constructed this cache instance.

    The setter exists only so `BaseCache.__init__`'s
    `self.key_prefix = params.get("KEY_PREFIX", "")` doesn't raise
    AttributeError; the assigned value is intentionally discarded, since a
    prefix computed once at construction is exactly what must NOT happen.
    """

    @property
    def key_prefix(self):
        return test_key_prefix()

    @key_prefix.setter
    def key_prefix(self, value):
        pass

    @staticmethod
    def _clamp(keys, args, kwargs, timeout_at):
        if any(_is_generation_counter(key) for key in keys):
            return args, kwargs
        if len(args) > timeout_at:
            if args[timeout_at] is None:
                args = (
                    *args[:timeout_at],
                    TEST_KEY_TTL_SECONDS,
                    *args[timeout_at + 1 :],
                )
        elif "timeout" in kwargs and kwargs["timeout"] is None:
            kwargs = {**kwargs, "timeout": TEST_KEY_TTL_SECONDS}
        return args, kwargs

    def set(self, key, *args, **kwargs):
        args, kwargs = self._clamp([key], args, kwargs, 1)
        return super().set(key, *args, **kwargs)

    def add(self, key, *args, **kwargs):
        args, kwargs = self._clamp([key], args, kwargs, 1)
        return super().add(key, *args, **kwargs)

    def set_many(self, data, *args, **kwargs):
        args, kwargs = self._clamp(list(data), args, kwargs, 0)
        return super().set_many(data, *args, **kwargs)

    def clear(self):
        """Delete only the keys carrying this cache's prefix.

        Falls back to the inherited `FLUSHDB` only if no prefix is
        configured - with no prefix there is nothing to scope to, and a
        silent no-op would be worse than the flush, because callers rely on
        `clear()` actually clearing.
        """
        prefix = self.key_prefix
        if not prefix:
            logger.warning(
                "PrefixScopedRedisCache has no KEY_PREFIX; falling back to "
                "FLUSHDB, which is not isolated from other processes."
            )
            return super().clear()

        # `delete_pattern` applies make_key()'s prefix+version itself, so the
        # pattern is relative - "*" here means "everything of mine".
        self.delete_pattern("*")
        return None
