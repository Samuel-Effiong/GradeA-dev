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


class PrefixScopedRedisCache(RedisCache):
    """A `RedisCache` whose `clear()` cannot reach another process's keys."""

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
