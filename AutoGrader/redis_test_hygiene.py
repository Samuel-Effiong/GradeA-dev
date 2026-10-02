"""Keep the test suite from leaking Redis keys (test-only, never imported by
production code).

WHY
---
Every test process writes cache, H-1 counter and Celery keys under its own
`gaplus-t<pid>:` prefix (see `AutoGrader/test_cache.py`, H-9). Nothing ever
removed them, so a killed or finished run left its keys behind for good. 500+
dead prefixes and ~134,000 keys piled up in Redis DB 0, and every `User`/
`Course` save runs `delete_pattern`, a SCAN over the WHOLE keyspace, so the
suite got slower with every run: the same billing tests took 2,492 s on the
bloated DB and 316 s on an empty one.

FOUR LAYERS, because no single one covers every way a run - or a process it
forked - can end
-----------------------------------------------------------------
1. Teardown (`finally`): a run that ends normally, fails, or is interrupted
   removes its own keys.
2. SIGTERM handler: turns SIGTERM into `SystemExit` so layer 1 also runs when a
   gate is stopped. Python's default SIGTERM action skips `finally`.
3. Start-of-run sweep: removes keys whose owning pid is dead. This is the only
   layer that covers SIGKILL, an OOM kill or a crash, when nothing in-process
   can run. It only ever touches a prefix whose pid is NOT running, so a
   concurrent sibling run's keys are never touched. A reused pid looks alive,
   which errs on the side of leaving keys alone.
4. End-of-run sweep: a second dead-prefix sweep at teardown, after layer 1.
   A forked test child (see `AutoGrader/tests_redis_test_isolation.py`'s
   fork-isolation tests) writes under its OWN pid and can exit mid-run, well
   before this teardown and well after the start-of-run sweep already ran -
   the one gap layers 1-3 don't cover between them. Confirmed by direct
   reproduction: without this layer, one full-suite run left 5 keys behind
   from exactly this source (Celery/kombu queue keys the fork tests create,
   which carry no TTL of their own).

`AutoGrader/test_cache.py` adds the fourth, passive layer: a TTL on every
test key except H-1 generation counters.

Redis being unreachable never fails a run: hygiene is best effort, and the
tests that need Redis fail on their own for the right reason.
"""

import contextlib
import logging
import os
import re
import signal
import threading

logger = logging.getLogger(__name__)

#: Redis ships with 16 logical databases. Tests use the default one plus the
#: fixed numbers in `real_redis_caches(...)` (7, 8, 11, 12, 15), so all 16 are
#: visited.
DATABASES = range(16)

#: The colon is part of the match on purpose: `gaplus-t12:` must not match
#: pid 123's `gaplus-t123:`. Django's `make_key` and kombu's
#: `global_keyprefix` both put a colon straight after the prefix.
_PREFIX_RE = re.compile(rb"^gaplus-t(\d+):")

_UNLINK_BATCH = 1000


def _clients():
    """Yield (db, client) for every logical database of the test Redis."""
    import redis
    from django.conf import settings

    location = settings.CACHES["default"]["LOCATION"]
    if isinstance(location, (list, tuple)):
        location = location[0]
    for db in DATABASES:
        yield db, redis.Redis.from_url(location, db=db)


def pid_is_alive(pid):
    """True if a process with this pid exists (any owner)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours
    return True


def _unlink(client, keys):
    for i in range(0, len(keys), _UNLINK_BATCH):
        client.unlink(*keys[i : i + _UNLINK_BATCH])


def delete_own_keys(pid=None):
    """Delete every key under `gaplus-t<pid>:` in every database."""
    pid = os.getpid() if pid is None else pid
    deleted = 0
    for _db, client in _clients():
        keys = list(client.scan_iter(match=f"gaplus-t{pid}:*", count=10000))
        _unlink(client, keys)
        deleted += len(keys)
    return deleted


def sweep_dead_prefixes(is_alive=pid_is_alive):
    """Delete the keys of every test prefix whose owning pid is dead.

    Returns the number of keys deleted. Only `gaplus-t<digits>:` keys are
    considered, so nothing else in the instance is ever touched. Liveness is
    checked again right before each prefix's keys are removed.
    """
    deleted = 0
    for _db, client in _clients():
        by_pid = {}
        for key in client.scan_iter(match="gaplus-t*:*", count=10000):
            match = _PREFIX_RE.match(key)
            if match:
                by_pid.setdefault(int(match.group(1)), []).append(key)
        for pid, keys in by_pid.items():
            if pid == os.getpid() or is_alive(pid):
                continue
            _unlink(client, keys)
            deleted += len(keys)
    return deleted


def _best_effort(action, label):
    try:
        return action()
    except Exception:
        logger.warning(
            "Redis test hygiene: %s failed; continuing", label, exc_info=True
        )
        return None


@contextlib.contextmanager
def redis_test_hygiene():
    """Sweep dead prefixes, then clean this run's own keys however it ends."""
    previous = None
    installed = False
    if threading.current_thread() is threading.main_thread():

        def _terminate(signum, _frame):
            # Raising lets `finally` (and unittest's own cleanup) run.
            raise SystemExit(128 + signum)

        previous = signal.signal(signal.SIGTERM, _terminate)
        installed = True

    _best_effort(sweep_dead_prefixes, "start-of-run sweep")
    try:
        yield
    finally:
        _best_effort(delete_own_keys, "teardown")
        # A forked test child (e.g. the H-9 fork-isolation tests) writes
        # under ITS OWN pid, not this process's, and can exit mid-run - long
        # before this teardown, but also long after the start-of-run sweep
        # already ran. Neither `delete_own_keys` (this pid only) nor the
        # start-of-run sweep (already run) ever reaches those keys, so a
        # second dead-prefix sweep here closes that gap. Safe to repeat: it
        # only ever touches a prefix whose owning pid is confirmed dead.
        _best_effort(sweep_dead_prefixes, "end-of-run sweep")
        if installed:
            signal.signal(signal.SIGTERM, previous)
