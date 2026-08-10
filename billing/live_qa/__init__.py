"""
billing/live_qa/
================
Concurrency, event routing and (later) invariants for the real-Stripe QA
suite.

This package imports FROM billing/stripe_live_qa.py and
billing/stripe_live_qa_scenarios.py, never the reverse. Those two modules
keep working exactly as before — the single-threaded path they provide is
still what `--tier smoke` and their own unit tests use.

WHY ANY OF THIS EXISTS
----------------------
Ten simulated years of a monthly plan is ~120 test-clock advances. Each
advance is asynchronous (Stripe re-bills, we poll until ready) and costs
roughly 20-40 seconds including event draining, so one subscriber takes
an hour of almost pure waiting. Sequentially that makes a deep run
impossible; concurrently it is the same hour for a dozen subscribers.

The parallel unit is the TEST CLOCK — one clock, one customer, one local
user, one independent set of database rows. That is the only clean
isolation boundary available, and everything here follows from it.
"""

from . import invariants_global, invariants_individual  # noqa: F401,E402  (registers)
from .concurrency import (
    Deadline,
    LiveQAWorkerPool,
    StripeRateLimiter,
    WorkItemResult,
    worker_db_connections,
)
from .events import AccountEventPoller, CustomerEventStream, EventBus
from .harness import ConcurrentLiveQAHarness

# Importing the catalogues is what POPULATES the INVARIANTS registry. They
# are imported here (rather than lazily at first use) so the registry is
# always complete — an invariant that silently never registered would be
# indistinguishable from one that always passes.
from .invariants import (  # noqa: F401  (re-exported below)
    INVARIANTS,
    ActorHistory,
    InvariantContext,
    StepRecord,
    StripeSnapshot,
    evaluate,
)
from .runner import run_suite_concurrently

__all__ = [
    "INVARIANTS",
    "AccountEventPoller",
    "ActorHistory",
    "ConcurrentLiveQAHarness",
    "CustomerEventStream",
    "Deadline",
    "EventBus",
    "InvariantContext",
    "LiveQAWorkerPool",
    "StepRecord",
    "StripeRateLimiter",
    "StripeSnapshot",
    "WorkItemResult",
    "evaluate",
    "run_suite_concurrently",
    "worker_db_connections",
]
