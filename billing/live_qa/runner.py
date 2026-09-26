"""
billing/live_qa/runner.py
=========================
Concurrent entry point for the real-Stripe QA suite.

Same contract as billing.stripe_live_qa_scenarios.run_suite — takes
scenario names, returns a SuiteResult — so the management command and the
Celery task can switch to it without changing how results are reported.

TEARDOWN ORDER IS LOAD-BEARING
------------------------------
    stop the poller -> drain what is left -> clean up

Cleaning up before draining would leave dispatched_event_ids incomplete
and leak StripeEvent ledger rows, which is the exact false-page the
module docstring in billing/stripe_live_qa.py warns about.
"""

from __future__ import annotations

import logging
import time
from functools import partial
from typing import Optional

from billing.stripe_live_qa import (
    LiveQAConfigurationError,
    ScenarioResult,
    SuiteResult,
    assert_live_qa_enabled,
    set_rate_limiter,
)

from .concurrency import Deadline, LiveQAWorkerPool, StripeRateLimiter
from .events import AccountEventPoller
from .harness import ConcurrentLiveQAHarness

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 6
POLLER_JOIN_TIMEOUT = 15


def _run_scenario(harness, name, fn) -> ScenarioResult:
    """Run one scenario, capturing everything. Never raises: the pool
    records the outcome and the other actors keep going."""
    started = time.monotonic()
    result = ScenarioResult(name=name)

    if harness.invariants is not None:
        harness.invariants.begin_scenario()

    try:
        recorder = fn(harness)
        result.checks = list(recorder.checks)
        result.passed = recorder.passed
    except Exception as exc:  # noqa: BLE001 - one scenario must not end the run
        result.passed = False
        result.error = repr(exc)
        logger.exception("[LIVE QA %s] Scenario %s raised.", harness.run_id, name)

    # Invariant violations count against the scenario even when its own
    # assertions all passed. A scenario that "succeeds" while leaving the
    # database inconsistent has not succeeded.
    if harness.invariants is not None:
        violations = harness.invariants.collect()
        if violations:
            result.checks.extend(v.to_check() for v in violations)
            result.passed = False

    result.duration_seconds = time.monotonic() - started
    return result


def run_suite_concurrently(
    scenario_names=None,
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    keep_objects: bool = False,
    budget_seconds: Optional[float] = None,
    rate_per_second: float = 8.0,
) -> SuiteResult:
    assert_live_qa_enabled()

    # Imported here, not at module scope: the scenarios module imports the
    # service layer, and keeping this local avoids widening an import
    # graph that billing/tasks.py already pulls from both directions.
    from billing.stripe_live_qa_scenarios import SCENARIOS

    names = list(scenario_names) if scenario_names else list(SCENARIOS)
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        raise LiveQAConfigurationError(
            f"Unknown scenario(s): {', '.join(sorted(unknown))}. "
            f"Available: {', '.join(sorted(SCENARIOS))}."
        )

    harness = ConcurrentLiveQAHarness()
    result = SuiteResult(run_id=harness.run_id)

    limiter = StripeRateLimiter(rate_per_second=rate_per_second)
    set_rate_limiter(limiter)

    # Started BEFORE any customer exists, so no event is missed between
    # the first subscription being created and the poller's first pass.
    poller = AccountEventPoller(harness.bus, created_floor=harness.started_at)
    poller.start()

    logger.info(
        "[LIVE QA %s] Starting %d scenario(s) across %d worker(s): %s",
        harness.run_id,
        len(names),
        max_workers,
        ", ".join(names),
    )

    try:
        pool = LiveQAWorkerPool(
            max_workers=max_workers, deadline=Deadline(budget_seconds)
        )
        items = [
            (name, partial(_run_scenario, harness, name, SCENARIOS[name]))
            for name in names
        ]
        for work in pool.run(items):
            if work.ok and isinstance(work.value, ScenarioResult):
                result.scenarios.append(work.value)
                continue
            # The pool only surfaces an error here if _run_scenario
            # itself failed (budget exhaustion, or a BaseException it
            # does not catch) — a scenario raising is already captured.
            result.scenarios.append(
                ScenarioResult(
                    name=work.label,
                    passed=False,
                    error=repr(work.error),
                    duration_seconds=work.duration_seconds,
                )
            )
    finally:
        poller.stop()
        poller.join(timeout=POLLER_JOIN_TIMEOUT)
        set_rate_limiter(None)

        if keep_objects:
            logger.warning(
                "[LIVE QA %s] keep_objects set — NOT cleaning up. Test clocks "
                "left behind: %s. Delete them in the Stripe test dashboard, "
                "and remove local users matching liveqa-%s-*.",
                harness.run_id,
                ", ".join(harness.clock_ids) or "(none)",
                harness.run_id,
            )
        else:
            result.cleanup_errors = harness.cleanup()

    stats = harness.bus.stats
    result.notes.append(f"event bus: {stats.as_dict()}")
    if stats.foreign_events_seen:
        # Not a failure — but it means another run or another engineer was
        # active on this Stripe test account, which can affect results.
        result.notes.append(
            f"{stats.foreign_events_seen} event(s) belonged to customers "
            "outside this run — the Stripe test account is shared, so treat "
            "timing-sensitive results with care."
        )
    if limiter.waited_seconds > 1:
        result.notes.append(
            f"rate limiter delayed Stripe calls by "
            f"{limiter.waited_seconds:.1f}s across {limiter.acquisitions} calls"
        )

    return result
