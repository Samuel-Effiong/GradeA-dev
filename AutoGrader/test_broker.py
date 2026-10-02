"""Per-process Celery broker/result-backend isolation for --parallel
(H-9, continued from AutoGrader/test_cache.py).

THE PROBLEM
-----------
`AutoGrader/celery.py` calls `app.config_from_object("django.conf:settings",
namespace="CELERY")` at import time, copying settings.py's
`CELERY_BROKER_TRANSPORT_OPTIONS`/`CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS`
(each carrying a `global_keyprefix` computed once, from `os.getpid()`, when
settings.py itself was imported) into the Celery `app` singleton. Both
imports happen in the PARENT process, before `manage.py test --parallel`
forks its workers via `multiprocessing`. Every forked child inherits the
already-populated `app.conf` unchanged, so every worker's broker channel
and result backend read the identical baked prefix string - the SAME bug
`AutoGrader/test_cache.py` fixes for the Django cache, just one layer
lower, where a cache-style property on `settings.CACHES` can't reach it.

Reproduced directly (fork_celery_probe.py, not committed - see the commit
this file lands in for the numbers): three children forked from one
parent that had already called `django.setup()`, all publishing to the
same-named queue. Before this fix, all three resolved to the exact same
Redis key despite `global_keyprefix` supposedly being "per-process": one
child's `queue.clear()` deleted the other two children's messages
(size dropped from 3 to 0). Kombu's `Channel.__init__`
(`kombu/transport/virtual/base.py`) and Celery's
`_add_global_keyprefix()` (`celery/backends/base.py`) both copy
`global_keyprefix` into a plain instance attribute exactly once, at
construction, from that same pre-forked `app.conf` - freshly constructing
the Channel/Backend object in the child changes nothing, because the
STRING it reads is what's stale, not the object holding it.

THE FIX
-------
Mirrors AutoGrader/test_cache.py's PrefixScopedRedisCache: turn the
prefix into a property that calls `os.getpid()` on every access, with a
no-op setter so the base classes' one-time assignment doesn't raise.
Every actual key formed by kombu (`kombu/transport/redis.py`, which reads
`self.global_keyprefix` fresh per operation) or by the backend (which
reads `self.task_keyprefix` et al. per call, e.g. `get_key_for_task`)
resolves against whichever process is really running it.

Wired in only under `"test" in sys.argv` (AutoGrader/settings.py), via
`CELERY_BROKER_TRANSPORT` (a dotted class path kombu's own
`resolve_transport` already supports - no URL change needed) and a
`ClassName+redis://...` prefix on `CELERY_RESULT_BACKEND` (the same
`scheme+realscheme` syntax already used elsewhere in this codebase's own
tests, e.g. `backend="cache+memory://"`). Production is untouched: both
env vars/branches are test-only.
"""

import os

from celery.backends.base import BaseKeyValueStoreBackend
from celery.backends.redis import RedisBackend
from kombu.transport.redis import Channel, Transport


def test_broker_prefix():
    """A colon-terminated prefix unique to THIS OS process, resolved fresh
    on every access rather than baked in once before a --parallel fork.

    Independent of `AutoGrader.test_cache.test_key_prefix()` (same
    `gaplus-t<pid>` scheme, no shared import) so Celery's app module,
    which loads before anything touches the Django cache, never needs to
    import the cache framework just to get its own prefix.
    """
    return f"gaplus-t{os.getpid()}:"


class PrefixScopedRedisChannel(Channel):
    """A redis Channel whose `global_keyprefix` is resolved live.

    `virtual.Channel.__init__` copies `transport_options['global_keyprefix']`
    into a plain instance attribute once, at construction - stale by the
    time a forked worker ever gets to use it. The setter below absorbs
    that one-time assignment and discards it.
    """

    @property
    def global_keyprefix(self):
        return test_broker_prefix()

    @global_keyprefix.setter
    def global_keyprefix(self, value):
        pass


class PrefixScopedRedisTransport(Transport):
    """The redis Transport wired to the live-prefixed channel above."""

    Channel = PrefixScopedRedisChannel


class PrefixScopedRedisBackend(RedisBackend):
    """A RedisBackend whose task/group/chord key prefixes are resolved live.

    `_add_global_keyprefix()` has the same one-time-bake problem as the
    channel above. Rather than reimplement its string-joining logic, each
    prefix is a property computing `test_broker_prefix()` + this class's
    own unprefixed default (`BaseKeyValueStoreBackend`'s literal, so this
    can't drift from whatever a Celery upgrade ships) fresh on every
    access; the setters absorb `_add_global_keyprefix()`/`_encode_prefixes()`'s
    one-time assignments and discard them.
    """

    @property
    def task_keyprefix(self):
        return self.key_t(test_broker_prefix()) + self.key_t(
            BaseKeyValueStoreBackend.task_keyprefix
        )

    @task_keyprefix.setter
    def task_keyprefix(self, value):
        pass

    @property
    def group_keyprefix(self):
        return self.key_t(test_broker_prefix()) + self.key_t(
            BaseKeyValueStoreBackend.group_keyprefix
        )

    @group_keyprefix.setter
    def group_keyprefix(self, value):
        pass

    @property
    def chord_keyprefix(self):
        return self.key_t(test_broker_prefix()) + self.key_t(
            BaseKeyValueStoreBackend.chord_keyprefix
        )

    @chord_keyprefix.setter
    def chord_keyprefix(self, value):
        pass
