"""
billing/live_qa/concurrency.py
==============================
Worker pool, per-thread database hygiene, and the account-wide Stripe
throttle.

WHY THREADS AND NOT ASYNCIO
---------------------------
Every layer beneath this is synchronous — the Django ORM, the stripe
client, webhooks._record_and_dispatch, and the existing scenarios. An
asyncio design would have to wrap all of it in a thread pool anyway.

More decisively: part of the point is to exercise the C3 webhook claim
logic under GENUINE concurrency, which needs real OS threads on real
database connections. That shape is already proven in this repo by
billing/tests/test_webhook_idempotency.py's WebhookRaceRegressionTests.

The work is ~100% I/O wait (Stripe HTTP plus poll sleeps), so the GIL is
irrelevant here.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from django.db import connection, connections

logger = logging.getLogger(__name__)


@contextmanager
def worker_db_connections():
    """
    Give a worker thread its own database connections and close them on
    the way out.

    Django's connection registry is thread-local, so a thread that opens
    connections and never closes them leaks one per worker for the life
    of the process. On a six-hour run that is how you exhaust a Postgres
    connection limit.
    """
    try:
        yield
    finally:
        # Closes only THIS thread's connections — that is the whole point
        # of the registry being thread-local.
        connections.close_all()


def refresh_stale_connection() -> None:
    """
    Drop a connection that has gone stale between work items.

    Over a long run Postgres (or pgbouncer, or any intermediary) WILL
    close an idle connection. Without this the next query raises
    InterfaceError from deep inside a scenario, which reads exactly like
    a billing bug and costs an afternoon to diagnose.
    """
    try:
        connection.close_if_unusable_or_obsolete()
    except Exception:  # noqa: BLE001 - hygiene must never break the run
        logger.debug("Could not refresh a stale DB connection.", exc_info=True)


class StripeRateLimiter:
    """
    Token bucket shared by every worker.

    Installed into billing.stripe_live_qa.guarded_call rather than called
    from the pool, because guarded_call is already the single, test-
    enforced path to Stripe — so throttling there cannot be bypassed by a
    scenario that reaches for `stripe.*` directly.

    Stripe's test mode allows roughly 25 reads and 25 writes per second
    per account. The default here is deliberately well under that: the
    suite is not trying to saturate the API, only to avoid 429s that
    would surface as scenario failures.
    """

    def __init__(self, rate_per_second: float = 8.0, burst: int = 16) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self.rate_per_second = float(rate_per_second)
        self.burst = max(1, int(burst))
        self._tokens = float(self.burst)
        self._updated_at = time.monotonic()
        self._lock = threading.Lock()
        self.waited_seconds = 0.0
        self.acquisitions = 0

    def acquire(self, tokens: int = 1) -> None:
        """Block until `tokens` are available. Never raises."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(
                    float(self.burst), self._tokens + elapsed * self.rate_per_second
                )
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    self.acquisitions += 1
                    return
                deficit = tokens - self._tokens
                sleep_for = deficit / self.rate_per_second

            self.waited_seconds += sleep_for
            time.sleep(min(sleep_for, 1.0))


@dataclass
class WorkItemResult:
    """One unit of concurrent work. `error` is captured, never raised —
    one actor blowing up must not take the pool (or the other eleven
    actors' hours of accumulated clock advances) with it."""

    label: str
    value: Any = None
    error: Optional[BaseException] = None
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Deadline:
    """A wall-clock budget checked BEFORE each unit of work.

    Exists so a nightly tier cannot quietly become a three-hour job when
    Stripe is slow. Exhaustion is reported as a note, never a failure.
    """

    budget_seconds: Optional[float] = None
    started_at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        if self.budget_seconds is None:
            return False
        return (time.monotonic() - self.started_at) >= self.budget_seconds

    @property
    def remaining_seconds(self) -> Optional[float]:
        if self.budget_seconds is None:
            return None
        return max(0.0, self.budget_seconds - (time.monotonic() - self.started_at))


class LiveQAWorkerPool:
    """
    Runs independent actors concurrently, each on its own DB connections.

    Deliberately NOT a general-purpose executor wrapper: it names threads
    (the name reaches every log line, which is most of what makes a
    concurrent failure readable), captures exceptions per item, and
    refreshes stale connections between items.
    """

    def __init__(self, max_workers: int = 6, deadline: Optional[Deadline] = None):
        self.max_workers = max(1, int(max_workers))
        self.deadline = deadline or Deadline()
        self._active = 0
        self._active_lock = threading.Lock()

    @property
    def active_count(self) -> int:
        """How many work items are executing right now.

        Read by the quiescence barrier: patching process-global state
        (e.g. timezone.now) while any worker is mid-scenario would
        silently corrupt every other actor's results, so the barrier
        asserts on this rather than hoping.
        """
        with self._active_lock:
            return self._active

    @contextmanager
    def _tracked(self):
        with self._active_lock:
            self._active += 1
        try:
            yield
        finally:
            with self._active_lock:
                self._active -= 1

    def _run_one(self, label: str, fn: Callable, *args, **kwargs) -> WorkItemResult:
        started = time.monotonic()
        with worker_db_connections(), self._tracked():
            refresh_stale_connection()
            try:
                value = fn(*args, **kwargs)
                return WorkItemResult(
                    label=label,
                    value=value,
                    duration_seconds=time.monotonic() - started,
                )
            except BaseException as exc:  # noqa: BLE001 - captured by design
                logger.exception("[LIVE QA] work item %s raised.", label)
                return WorkItemResult(
                    label=label,
                    error=exc,
                    duration_seconds=time.monotonic() - started,
                )

    def run(self, items) -> list:
        """
        `items` is an iterable of (label, callable) pairs.

        Returns a WorkItemResult per item, in submission order. Items not
        started before the deadline expires come back with a
        BUDGET-exhausted marker rather than being silently dropped —
        silent truncation reads as "we covered everything" when we did
        not.
        """
        items = list(items)
        if not items:
            return []

        results: list = [None] * len(items)
        with ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="liveqa-w"
        ) as executor:
            futures = {}
            for index, (label, fn) in enumerate(items):
                if self.deadline.expired:
                    results[index] = WorkItemResult(
                        label=label,
                        error=TimeoutError(
                            "wall-clock budget exhausted before this item started"
                        ),
                    )
                    continue
                futures[executor.submit(self._run_one, label, fn)] = index

            for future, index in futures.items():
                results[index] = future.result()

        return [r for r in results if r is not None]
