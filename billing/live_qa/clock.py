"""
billing/live_qa/clock.py
========================
Advancing simulated time by years instead of months, and the two things
that makes necessary: detecting Stripe's undocumented test-clock ceiling,
and moving the LOCAL clock alongside Stripe's.

THERE ARE TWO CLOCKS
--------------------
A Stripe test clock moves STRIPE's time. `timezone.now()` does not move
at all. After advancing two years, local `billing_cycle_end` sits in 2028
while `timezone.now()` is still today — so every local-time-driven code
path matches nothing and passes VACUOUSLY:

    reconcile_subscription_renewals   billing_cycle_end__lte=now
    process_annual_plan_credit_grants next_credit_grant_at__lte=now
    expire_active_trials              trial_end__lte=now
    cleanup_expired_credit_buckets    expires_at__lte=now

That last pair matters enormously here: an annual plan's monthly credit
grants are driven ENTIRELY by a local-clock task. Advance a Stripe clock
a year without also moving local time and the subscriber silently
receives one month of credits — which is indistinguishable from the bug
we fixed in Phase 0. The simulation would reproduce the bug rather than
detect it.

So SimNowPatch moves local time in step with Stripe's. It patches
process-global state, so it asserts the worker pool is parked rather than
hoping, and it is paired with a positive control: a run that never proves
the patched sweep CAN find work has not proven the sweep found nothing.

STRIPE'S CEILING
----------------
There is an undocumented limit on how far a test clock can advance.
Detection here is BEHAVIOURAL, never string-matched on Stripe's error
text: the limit is undocumented, so a wording change would turn a
non-bug into a red nightly. Instead, on rejection we probe — if a tiny
advance still works the limit is on distance-per-advance and we retry
with halved steps; if it does not, we have hit an absolute wall.

Either way the result is a NOTE, never a failure. "Reached Stripe's
ceiling at simulated year 3.4" is a fact about how far the run got, not
a defect in billing.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Optional

from billing.imports import stripe
from billing.stripe_live_qa import (
    LiveQAInfrastructureError,
    LiveQATimeout,
    guarded_call,
)
from billing.stripe_service import extract_subscription_billing_period

logger = logging.getLogger(__name__)

SECONDS_PER_YEAR = 365.25 * 24 * 3600

# Land clearly PAST a boundary, never exactly on it — Stripe bills at the
# boundary instant, so advancing to exactly period_end races the renewal.
ADVANCE_OVERSHOOT_SECONDS = 3600

# How many times a rejected advance is halved before we call it a wall.
MAX_HALVINGS = 4
# Below this, halving is pointless — we are not making progress.
MIN_USEFUL_STEP_SECONDS = 6 * 3600

# Outcomes
REACHED_TARGET = "target"
REACHED_STRIPE_CEILING = "stripe_ceiling"
REACHED_BUDGET = "budget"
REACHED_INFRASTRUCTURE = "infrastructure"
REACHED_NO_PERIOD = "no_period"


@dataclass
class HorizonOutcome:
    reached: str
    periods_advanced: int = 0
    simulated_seconds: int = 0
    final_frozen_time: int = 0
    detail: str = ""

    @property
    def simulated_years(self) -> float:
        return round(self.simulated_seconds / SECONDS_PER_YEAR, 2)

    @property
    def is_failure(self) -> bool:
        """Only genuine infrastructure faults are failures. Hitting
        Stripe's ceiling or the wall-clock budget are facts about how far
        the run got."""
        return self.reached == REACHED_INFRASTRUCTURE

    def as_note(self) -> str:
        if self.reached == REACHED_TARGET:
            return (
                f"advanced {self.periods_advanced} billing period(s) "
                f"({self.simulated_years} simulated years)"
            )
        if self.reached == REACHED_STRIPE_CEILING:
            return (
                f"reached Stripe's test-clock ceiling at simulated year "
                f"{self.simulated_years} after {self.periods_advanced} "
                f"advance(s){f' — {self.detail}' if self.detail else ''}"
            )
        if self.reached == REACHED_BUDGET:
            return (
                f"stopped at the wall-clock budget after "
                f"{self.periods_advanced} advance(s) "
                f"({self.simulated_years} simulated years)"
            )
        if self.reached == REACHED_NO_PERIOD:
            return (
                f"stopped after {self.periods_advanced} advance(s): "
                f"Stripe stopped reporting a billing period ({self.detail})"
            )
        return f"stopped: {self.detail}"


# --------------------------------------------------------------------------
# Ceiling detection
# --------------------------------------------------------------------------

CEILING_DISTANCE = "distance"  # per-advance distance limit — halving helps
CEILING_ABSOLUTE = "absolute"  # a wall — nothing further will work
CEILING_UNKNOWN = "unknown"  # the probe itself could not tell


@dataclass
class ProbeResult:
    kind: str
    frozen_time: int = 0
    detail: str = ""


class CeilingProbe:
    """
    Works out WHY an advance was rejected, by experiment rather than by
    reading Stripe's error text.

    A string-matched classifier would be a liability: the limit is
    undocumented, so the message can change at any time, and the failure
    mode would be a red nightly for a bug that does not exist.
    """

    TINY_ADVANCE_SECONDS = 3600

    def __init__(self, harness):
        self.harness = harness

    def classify(
        self, clock_id: str, attempted_ts: int, error: Exception
    ) -> ProbeResult:
        try:
            clock = guarded_call(stripe.test_helpers.TestClock.retrieve, clock_id)
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(
                kind=CEILING_UNKNOWN,
                detail=f"could not re-read the clock after rejection: {exc!r}",
            )

        frozen = int(clock.get("frozen_time") or 0)
        if clock.get("status") not in ("ready", None):
            return ProbeResult(
                kind=CEILING_UNKNOWN,
                frozen_time=frozen,
                detail=f"clock is {clock.get('status')}, not ready",
            )

        # Did the advance partially land? Then it is not a ceiling at all.
        if frozen >= attempted_ts:
            return ProbeResult(
                kind=CEILING_UNKNOWN,
                frozen_time=frozen,
                detail="the clock actually moved; the rejection was transient",
            )

        # The experiment: does a deliberately tiny advance still work?
        try:
            self.harness.advance_clock_to(clock_id, frozen + self.TINY_ADVANCE_SECONDS)
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(
                kind=CEILING_ABSOLUTE,
                frozen_time=frozen,
                detail=(
                    f"even a {self.TINY_ADVANCE_SECONDS}s advance was refused "
                    f"({exc!r}); original rejection: {error!r}"
                ),
            )

        return ProbeResult(
            kind=CEILING_DISTANCE,
            frozen_time=frozen + self.TINY_ADVANCE_SECONDS,
            detail=(
                "a small advance succeeded, so the limit is on distance per "
                "advance rather than an absolute wall"
            ),
        )


# --------------------------------------------------------------------------
# Simulated local time
# --------------------------------------------------------------------------


class QuiescenceError(RuntimeError):
    """Raised when process-global time would be patched while workers run."""


@contextmanager
def sim_now(simulated_now, *, pool=None):
    """
    Move LOCAL time to match the simulated clock, so the local-clock
    scheduled tasks are exercised rather than passing vacuously.

    Patches django.utils.timezone.now at its source, which is what every
    caller resolves through — services, tasks, and auto_now_add alike.
    That is process-global, so if a worker pool is supplied this ASSERTS
    it is parked rather than hoping: patching global time while another
    actor is mid-scenario would silently corrupt its results, and a
    silently corrupted result is worse than a crash.
    """
    if pool is not None and pool.active_count > 0:
        raise QuiescenceError(
            f"refusing to patch global time while {pool.active_count} worker(s) "
            "are still running — every other actor's results would be corrupted"
        )

    from unittest.mock import patch as _patch

    with _patch("django.utils.timezone.now", return_value=simulated_now):
        yield simulated_now


def run_local_clock_tasks(simulated_now, *, pool=None, tasks=None) -> dict:
    """
    Run the beat-scheduled tasks that are driven by LOCAL time, as if it
    were `simulated_now`.

    Without this an annual plan's monthly credit grants never fire during
    a simulation, and the run would reproduce Phase 0's Bug 2 rather than
    detect it.
    """
    from billing import tasks as billing_tasks

    if tasks is None:
        tasks = (
            billing_tasks.process_annual_plan_credit_grants,
            billing_tasks.expire_active_trials,
            billing_tasks.cleanup_expired_credit_buckets,
            billing_tasks.process_license_monthly_credit_refreshes,
            billing_tasks.reconcile_subscription_renewals,
        )

    summaries: dict = {}
    with sim_now(simulated_now, pool=pool):
        for task in tasks:
            name = getattr(task, "__name__", str(task))
            try:
                summaries[name] = task()
            except Exception as exc:  # noqa: BLE001 - one task must not stop the rest
                summaries[name] = f"RAISED: {exc!r}"
                logger.exception("[LIVE QA] local-clock task %s raised.", name)
    return summaries


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


@dataclass
class PeriodRecord:
    index: int
    frozen_time: int
    period_end_before: Optional[str] = None
    task_summaries: dict = field(default_factory=dict)


class LongHorizonRunner:
    """
    Advance one billing period at a time, draining and checking after
    each.

    One period at a time, not one big jump: every renewal is a real code
    path we want to execute, and a single leap to year ten would skip 119
    of them while looking like it covered a decade.
    """

    def __init__(
        self,
        harness,
        *,
        clock_id: str,
        customer_id: str,
        subscription_id: str,
        max_periods: int = 12,
        deadline=None,
        run_local_tasks: bool = True,
        on_period: Optional[Callable] = None,
    ):
        self.harness = harness
        self.clock_id = clock_id
        self.customer_id = customer_id
        self.subscription_id = subscription_id
        self.max_periods = max_periods
        self.deadline = deadline
        self.run_local_tasks = run_local_tasks
        self.on_period = on_period
        self.records: list = []

    # -- advancing -------------------------------------------------------

    def _advance_with_ceiling_handling(self, target_ts: int):
        """Returns (reached_ts, ProbeResult|None). A distance limit is
        retried with halved steps; an absolute wall gives up."""
        probe = CeilingProbe(self.harness)
        current_target = int(target_ts)

        for _ in range(MAX_HALVINGS + 1):
            try:
                clock = self.harness.advance_clock_to(self.clock_id, current_target)
                return int(clock.get("frozen_time") or current_target), None
            except LiveQATimeout:
                # A clock that never became ready is an infrastructure
                # problem, not a ceiling — do not paper over it.
                raise
            except Exception as exc:  # noqa: BLE001
                result = probe.classify(self.clock_id, current_target, exc)
                if result.kind != CEILING_DISTANCE:
                    return None, result

                frozen = result.frozen_time
                remaining = current_target - frozen
                if remaining <= MIN_USEFUL_STEP_SECONDS:
                    return None, ProbeResult(
                        kind=CEILING_ABSOLUTE,
                        frozen_time=frozen,
                        detail=(
                            "halving reached the minimum useful step without "
                            "the advance being accepted"
                        ),
                    )
                current_target = frozen + remaining // 2
                logger.info(
                    "[LIVE QA] halving the clock advance to %ss after a "
                    "distance-limited rejection.",
                    remaining // 2,
                )

        return None, ProbeResult(
            kind=CEILING_ABSOLUTE,
            detail=f"advance still refused after {MAX_HALVINGS} halvings",
        )

    # -- main loop -------------------------------------------------------

    def run(self) -> HorizonOutcome:
        start_frozen = self._current_frozen_time()
        frozen = start_frozen
        advanced = 0

        for index in range(1, self.max_periods + 1):
            if self.deadline is not None and self.deadline.expired:
                return self._outcome(REACHED_BUDGET, advanced, start_frozen, frozen)

            period_end = self._stripe_period_end()
            if period_end is None:
                return self._outcome(
                    REACHED_NO_PERIOD,
                    advanced,
                    start_frozen,
                    frozen,
                    detail=(
                        "extract_subscription_billing_period returned None for a "
                        "real Stripe subscription — either the subscription has "
                        "ended, or Stripe moved the field (the C1 failure)"
                    ),
                )

            target = int(period_end.timestamp()) + ADVANCE_OVERSHOOT_SECONDS
            if target <= frozen:
                # Already past this boundary; nothing to do without
                # spinning. Treat as reaching the target.
                return self._outcome(REACHED_TARGET, advanced, start_frozen, frozen)

            reached, probe = self._advance_with_ceiling_handling(target)
            if reached is None:
                kind = (
                    REACHED_STRIPE_CEILING
                    if probe and probe.kind == CEILING_ABSOLUTE
                    else REACHED_INFRASTRUCTURE
                )
                return self._outcome(
                    kind,
                    advanced,
                    start_frozen,
                    frozen,
                    detail=(probe.detail if probe else "advance failed"),
                )

            frozen = reached
            advanced += 1

            self.harness.drain_events(customer_id=self.customer_id)

            record = PeriodRecord(index=index, frozen_time=frozen)
            if self.run_local_tasks:
                from datetime import datetime
                from datetime import timezone as dt_timezone

                record.task_summaries = run_local_clock_tasks(
                    datetime.fromtimestamp(frozen, tz=dt_timezone.utc)
                )
                # Local tasks can themselves change billing state (an
                # annual credit grant, a reconcile), so re-check after.
                if getattr(self.harness, "invariants", None) is not None:
                    self.harness.invariants.checkpoint(
                        self.customer_id, f"after local tasks, period {index}"
                    )

            self.records.append(record)
            if self.on_period is not None:
                self.on_period(record)

        return self._outcome(REACHED_TARGET, advanced, start_frozen, frozen)

    # -- helpers ---------------------------------------------------------

    def _current_frozen_time(self) -> int:
        try:
            clock = guarded_call(stripe.test_helpers.TestClock.retrieve, self.clock_id)
            return int(clock.get("frozen_time") or int(time.time()))
        except Exception as exc:  # noqa: BLE001
            raise LiveQAInfrastructureError(
                f"could not read test clock {self.clock_id}: {exc!r}"
            ) from exc

    def _stripe_period_end(self):
        stripe_sub = self.harness.retrieve_subscription(self.subscription_id)
        _, period_end = extract_subscription_billing_period(stripe_sub)
        return period_end

    def _outcome(
        self, reached, advanced, start_frozen, frozen, detail=""
    ) -> HorizonOutcome:
        return HorizonOutcome(
            reached=reached,
            periods_advanced=advanced,
            simulated_seconds=max(0, frozen - start_frozen),
            final_frozen_time=frozen,
            detail=detail,
        )
