"""
H-65: one run at a time for each Celery Beat task.

WHY
---
Two runs of the same scheduled task overlap more often than it seems: a
redeploy near a fire time starts the new Beat before the old one's run has
finished, Beat can end up running on more than one replica, and anyone can
run a task by hand. The per-row row locks don't make an overlap safe on
their own (see billing/tests/test_overlapping_run_rechecks.py for what a
second run did with its stale copy), and for the email senders and the paid
probes an overlap means duplicate emails or double the Stripe and AI spend.

WHY REDIS, NOT A POSTGRES ADVISORY LOCK
---------------------------------------
Production reaches Postgres through pgbouncer in TRANSACTION pooling mode
(settings.py, docs/ops/postgres-guard-rails.md). A session-level advisory
lock stays on whichever pooled server connection took it, which other
clients then reuse, so it can leak or be released by the wrong client. A
transaction-level one would need a single transaction for the whole job,
which the idle-in-transaction kill and the per-row transactions rule out.

HOW
---
`@single_instance(max_hold=...)` goes under `@shared_task`. Each run:

1. takes the lock with SET NX PX and a token of its own (the Celery task
   id plus a random suffix), so no two runs ever share a token;
2. if another run holds it, SKIPS: it logs the task name, the key and the
   holder's id (never anything about a user) and returns a summary;
3. if Redis can't be reached, FAILS CLOSED: it logs an ERROR and skips.
   The Celery broker is the same Redis, so while it's down Beat can't
   dispatch these tasks anyway; the next scheduled run catches up, and the
   per-row locks and re-checks stay in place. `/health` probes the cache,
   and `check_beat_health` reports it too (`lock_store_problem`);
4. while the run is alive, a heartbeat thread extends the lock's short TTL,
   but only while the token is still this run's (compare-and-extend in
   Lua), and only up to `max_hold`. A hard-killed worker's lock lapses
   within one TTL. A run still going after `max_hold` is logged at ERROR
   and its lock is left to lapse, so a hung run can never block the job
   for good; `max_hold` is below the schedule interval (a test checks
   every entry), so the next scheduled run always gets its turn;
5. releases the lock in `finally` with compare-and-delete in Lua, so a run
   whose lock lapsed can never delete a newer run's lock.

A new CELERY_BEAT_SCHEDULE entry must either use this decorator or be
added to `EXEMPT_BEAT_TASKS` with a reason: AutoGrader/tests_beat_locks.py
checks every entry.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

logger = logging.getLogger(__name__)

KEY_ROOT = "beat-lock"

#: How long the lock lives without a heartbeat. Short, so a hard-killed
#: worker's lock lapses quickly; the heartbeat extends it every third.
DEFAULT_TTL_SECONDS = 300

#: The longest a run may hold its lock, per schedule. Each is above the
#: task's worst realistic run and below its schedule interval, so a hung run
#: can never swallow the next scheduled one (a test checks every entry).
EVERY_5_MIN = timedelta(minutes=4)
HOURLY = timedelta(minutes=50)
EVERY_6_HOURS = timedelta(hours=5)
DAILY = timedelta(hours=6)
WEEKLY = timedelta(hours=12)
#: reconcile_stripe_prices: ~18 Stripe reads. The bound its own lock used
#: before H-65 moved it onto this helper.
PRICE_SWEEP = timedelta(minutes=30)

#: Beat entries that run without the lock, and why.
EXEMPT_BEAT_TASKS = {
    # Every 60 s, one presence row per tick; an overlap writes one extra
    # sample, and taking a lock every minute costs more than it saves.
    "dashboard.tasks.record_concurrent_users": "one sample row per tick",
    # The watchdog must never be skippable: it's what reports a stuck or
    # failing lock (lock_store_problem below).
    "AutoGrader.beat_health.check_beat_health": "the watchdog itself",
}

SKIPPED_HELD = "another run already holds the lock; skipping this one"
SKIPPED_CACHE_ERROR = (
    "the lock could not be checked (cache error), so this run fails closed " "and skips"
)

_RELEASE = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) end return 0"
)
_EXTEND = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) end return 0"
)


def _redis():
    from django_redis import get_redis_connection

    return get_redis_connection("default")


def lock_key(name):
    """The Redis key for a task's lock, under the cache's own prefix."""
    from django.core.cache import cache

    return cache.make_key(f"{KEY_ROOT}:{name}")


def _seconds(value):
    if isinstance(value, timedelta):
        return int(value.total_seconds())
    return int(value)


def _run_id():
    from celery import current_task

    request = getattr(current_task, "request", None)
    task_id = getattr(request, "id", None) or "direct"
    return f"{task_id}:{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class SingleInstance:
    """What the decorator records on a task, for the coverage guard."""

    name: str
    ttl_seconds: int
    max_hold_seconds: int


class BeatLock:
    def __init__(self, name, ttl_seconds, max_hold_seconds):
        self.name = name
        self.key = lock_key(name)
        self.ttl_ms = ttl_seconds * 1000
        self.max_hold_seconds = max_hold_seconds
        self.token = None

    def acquire(self, token):
        self.token = token
        return bool(_redis().set(self.key, token, nx=True, px=self.ttl_ms))

    def holder(self):
        value = _redis().get(self.key)
        return value.decode() if isinstance(value, bytes) else value

    def extend(self):
        return bool(_redis().eval(_EXTEND, 1, self.key, self.token, self.ttl_ms))

    def release(self):
        return bool(_redis().eval(_RELEASE, 1, self.key, self.token))


class _Heartbeat(threading.Thread):
    def __init__(self, lock):
        super().__init__(name=f"beat-lock-heartbeat:{lock.name}", daemon=True)
        self.lock = lock
        self.stopped = threading.Event()
        self.interval = lock.ttl_ms / 1000 / 3

    def run(self):
        started = time.monotonic()
        while not self.stopped.wait(self.interval):
            if time.monotonic() - started >= self.lock.max_hold_seconds:
                logger.error(
                    "Beat task %s is still running after its maximum hold of "
                    "%ss. Its lock (key %s, run %s) is left to lapse, so the "
                    "next run can start.",
                    self.lock.name,
                    self.lock.max_hold_seconds,
                    self.lock.key,
                    self.lock.token,
                )
                return
            try:
                if not self.lock.extend():
                    logger.error(
                        "Beat task %s lost its lock while running (key %s, "
                        "run %s): it lapsed, so another run may overlap this "
                        "one.",
                        self.lock.name,
                        self.lock.key,
                        self.lock.token,
                    )
                    return
            except Exception:
                # Try again next tick; the TTL covers a brief outage.
                logger.error(
                    "Beat task %s could not refresh its lock (key %s, run %s).",
                    self.lock.name,
                    self.lock.key,
                    self.lock.token,
                    exc_info=True,
                )

    def stop(self):
        self.stopped.set()
        self.join(timeout=5)


def single_instance(*, max_hold, ttl=DEFAULT_TTL_SECONDS):
    """Run the decorated Beat task at most once at a time. See the module
    docstring. Goes under `@shared_task`."""
    max_hold_seconds = _seconds(max_hold)
    ttl_seconds = min(_seconds(ttl), max_hold_seconds)

    def decorate(fn):
        name = f"{fn.__module__}.{fn.__name__}"

        @functools.wraps(fn)
        def run(*args, **kwargs):
            lock = BeatLock(name, ttl_seconds, max_hold_seconds)
            token = _run_id()
            try:
                acquired = lock.acquire(token)
            except Exception:
                logger.error(
                    "Beat task %s: %s (key %s, run %s).",
                    name,
                    SKIPPED_CACHE_ERROR,
                    lock.key,
                    token,
                    exc_info=True,
                )
                return f"{name}: {SKIPPED_CACHE_ERROR}."
            if not acquired:
                try:
                    holder = lock.holder()
                except Exception:
                    holder = "unknown"
                logger.warning(
                    "Beat task %s: %s (key %s, held by run %s; this run %s).",
                    name,
                    SKIPPED_HELD,
                    lock.key,
                    holder,
                    token,
                )
                return f"{name}: {SKIPPED_HELD}."

            heartbeat = _Heartbeat(lock)
            heartbeat.start()
            try:
                return fn(*args, **kwargs)
            finally:
                heartbeat.stop()
                try:
                    released = lock.release()
                except Exception:
                    logger.error(
                        "Beat task %s could not release its lock (key %s, "
                        "run %s); it lapses within %ss.",
                        name,
                        lock.key,
                        token,
                        ttl_seconds,
                        exc_info=True,
                    )
                else:
                    if not released:
                        logger.error(
                            "Beat task %s finished without its lock (key %s, "
                            "run %s): it had lapsed, so another run may have "
                            "overlapped this one.",
                            name,
                            lock.key,
                            token,
                        )

        run.single_instance = SingleInstance(  # type: ignore[attr-defined]
            name, ttl_seconds, max_hold_seconds
        )
        return run

    return decorate


def declared_lock(task) -> SingleInstance | None:
    """The lock a Celery task (or plain function) declares, if any."""
    return getattr(getattr(task, "run", task), "single_instance", None)


def lock_store_problem():
    """None when the lock store answers, else a line for check_beat_health:
    while it doesn't, every locked Beat task fails closed and skips."""
    try:
        probe = lock_key("__probe__")
        client = _redis()
        client.set(probe, "1", px=10_000)
        if client.get(probe) is None:
            raise RuntimeError("the probe key was not readable after writing it")
    except Exception as exc:
        return (
            "The cache holding the Beat task locks is not answering "
            f"({type(exc).__name__}), so every single-instance Beat task is "
            "failing closed and skipping its runs."
        )
    return None
