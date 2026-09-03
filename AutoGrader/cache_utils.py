"""Cache invalidation helpers.

All wildcard cache invalidation in the project goes through
``delete_cache_patterns`` so that bulk operations can coalesce it.

With django-redis, ``cache.delete_pattern`` SCANs the Redis keyspace, which
is far too expensive to run dozens of times per request. Signal handlers
(user/enrollment saves) each clear ~10 patterns, so a bulk operation that
saves N rows would otherwise trigger hundreds of keyspace scans.
``batched_cache_invalidation`` collects the patterns instead and clears the
deduplicated set once, after the batch finishes.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from django.core.cache import cache

logger = logging.getLogger(__name__)

# ContextVar (not a module global) so concurrent requests/tasks in the same
# process — threads or async — never share or clobber each other's batch.
_pending_patterns: ContextVar = ContextVar("pending_cache_patterns", default=None)


def _delete_now(patterns):
    """Delete every key matching each pattern, best-effort.

    Invalidation is not allowed to fail a request that already committed its
    writes: a Redis blip here means stale cache for at most CACHE_TTL, which
    is preferable to a 500 after the data was saved.
    """
    if not hasattr(cache, "delete_pattern"):
        # Non-redis backend (e.g. locmem in tests) has no pattern support.
        return

    for pattern in set(patterns):
        try:
            cache.delete_pattern(pattern)
        except Exception:
            logger.exception("Failed to invalidate cache pattern %r", pattern)


def delete_cache_patterns(*patterns):
    """Invalidate cache keys matching the given wildcard patterns.

    Inside a ``batched_cache_invalidation`` block the patterns are only
    recorded; the deduplicated set is flushed when the outermost block exits.
    Outside a batch they are deleted immediately.
    """
    if not patterns:
        return

    pending = _pending_patterns.get()
    if pending is not None:
        pending.update(patterns)
        return

    _delete_now(patterns)


@contextmanager
def batched_cache_invalidation():
    """Coalesce all ``delete_cache_patterns`` calls made inside the block.

    The collected patterns are flushed exactly once when the outermost block
    exits — including on exception, since rows saved before the failure still
    require invalidation. Nested blocks reuse the outermost collector.
    """
    if _pending_patterns.get() is not None:
        yield
        return

    token = _pending_patterns.set(set())
    try:
        yield
    finally:
        pending = _pending_patterns.get()
        _pending_patterns.reset(token)
        _delete_now(pending)
