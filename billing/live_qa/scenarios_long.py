"""
billing/live_qa/scenarios_long.py
=================================
Long-horizon scenarios: years of simulated billing, on real Stripe test
clocks.

TWO SHAPES, BECAUSE THEY STRESS DIFFERENT THINGS
------------------------------------------------
A MONTHLY plan buys renewal COUNT — 120 advances is 120 executions of the
real renewal path, which is where accumulation bugs, drifting anchors and
unbounded row growth show up.

An ANNUAL plan buys calendar DISTANCE for only ~10 advances. That is the
cheap way to reach Feb-29 anniversaries and multi-year date arithmetic,
and it is also the fastest way to discover Stripe's test-clock ceiling.

Running only one of them would leave half the long-horizon bug classes
uncovered.

WHY THESE NEED THE LOCAL CLOCK TOO
----------------------------------
An annual plan's monthly credit top-ups are driven entirely by a
LOCAL-clock Celery task, not by Stripe. Advancing only Stripe's clock
would leave the subscriber with one month of credits for a paid year —
which is exactly Phase 0's Bug 2. The simulation would REPRODUCE that bug
rather than detect it. LongHorizonRunner therefore runs the local-clock
tasks under simulated time after every period.
"""

from __future__ import annotations

import logging

from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    PlanTier,
    UserSubscription,
)
from billing.stripe_live_qa import CheckRecorder, require_plan
from billing.stripe_live_qa_scenarios import (
    TIER_DEEP,
    _establish_subscriber,
    register_scenarios,
)

from .clock import LongHorizonRunner

logger = logging.getLogger(__name__)

# Deliberately configurable: a nightly smoke of this scenario wants 2-3,
# the weekly deep run wants 120.
DEFAULT_MONTHLY_PERIODS = 24
DEFAULT_ANNUAL_PERIODS = 4


def _run_horizon(harness, sub, rec, *, max_periods, label):
    runner = LongHorizonRunner(
        harness,
        clock_id=sub.clock_id,
        customer_id=sub.customer_id,
        subscription_id=sub.stripe_subscription_id,
        max_periods=max_periods,
    )
    outcome = runner.run()

    # Ceiling and budget outcomes are NOTES, not failures — they describe
    # how far the run got. Only infrastructure faults fail.
    rec.expect(
        f"{label}: the horizon run completed without an infrastructure fault",
        not outcome.is_failure,
        outcome.as_note(),
    )
    rec.expect(
        f"{label}: at least one billing period was actually simulated",
        outcome.periods_advanced >= 1,
        outcome.as_note(),
    )
    logger.info("[LIVE QA %s] %s: %s", harness.run_id, label, outcome.as_note())
    return outcome


def scenario_long_horizon_monthly(harness) -> CheckRecorder:
    """Many consecutive renewals — the renewal-count shape.

    Every invariant runs after every period automatically, so this is
    really "execute the renewal path N times and assert nothing ever
    drifts, regresses or silently stops granting credits".
    """
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    sub = _establish_subscriber(harness, rec, plan=plan, label="horizon-monthly")
    harness.drain_events(customer_id=sub.customer_id)

    before = sub.local()
    outcome = _run_horizon(
        harness, sub, rec, max_periods=DEFAULT_MONTHLY_PERIODS, label="monthly horizon"
    )

    after = sub.local()
    if not rec.expect(
        "monthly horizon: an active subscription survives the whole run",
        after is not None,
        "no active UserSubscription remains — the customer has been paying "
        "into nothing",
    ):
        return rec

    rec.expect(
        "monthly horizon: the billing cycle advanced across the run",
        before is None or after.billing_cycle_end > before.billing_cycle_end,
        f"start={getattr(before, 'billing_cycle_end', None)}, "
        f"end={after.billing_cycle_end}",
    )

    # Row growth is only visible after many cycles: activate_subscription
    # deactivates and CREATES a row every renewal.
    rows = UserSubscription.objects.filter(user=sub.user).count()
    active = UserSubscription.objects.filter(user=sub.user, is_active=True).count()
    rec.expect_equal(
        "monthly horizon: exactly one subscription row is active at the end",
        active,
        1,
        f"{rows} total row(s) accumulated across {outcome.periods_advanced} renewals",
    )

    monthly_buckets = CreditBucket.objects.filter(
        wallet__user=sub.user, bucket_type=CreditBucketType.MONTHLY
    ).count()
    rec.expect(
        "monthly horizon: a credit grant accompanied every renewal",
        monthly_buckets >= outcome.periods_advanced,
        f"{monthly_buckets} monthly bucket(s) for "
        f"{outcome.periods_advanced} renewal(s)",
    )
    return rec


def scenario_long_horizon_annual(harness) -> CheckRecorder:
    """Multiple annual cycles — the calendar-distance shape.

    This is the scenario that locks Phase 0's Bug 1 and Bug 2 against real
    Stripe: the monthly credit cadence inside an annual contract is driven
    by a local-clock task, and both bugs made that cadence silently stop
    after month one.
    """
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.ANNUAL)
    sub = _establish_subscriber(harness, rec, plan=plan, label="horizon-annual")
    harness.drain_events(customer_id=sub.customer_id)

    local = sub.local()
    rec.expect(
        "annual horizon: the subscription has a monthly credit grant date",
        local is not None and local.next_credit_grant_at is not None,
        f"next_credit_grant_at={getattr(local, 'next_credit_grant_at', None)} — "
        "a NULL here means the monthly grant task can never pick this "
        "subscription up (Phase 0 Bug 2)",
    )

    outcome = _run_horizon(
        harness, sub, rec, max_periods=DEFAULT_ANNUAL_PERIODS, label="annual horizon"
    )

    after = sub.local()
    if not rec.expect(
        "annual horizon: an active subscription survives the whole run",
        after is not None,
    ):
        return rec

    # Across N annual cycles the subscriber should have received roughly
    # 12 monthly grants per year. Asserted as a floor rather than an exact
    # count: the grant task runs on a schedule, so the final partial month
    # legitimately varies.
    monthly_buckets = CreditBucket.objects.filter(
        wallet__user=sub.user, bucket_type=CreditBucketType.MONTHLY
    ).count()
    expected_floor = max(1, outcome.periods_advanced)
    rec.expect(
        "annual horizon: monthly credit grants continued through the contract",
        monthly_buckets > expected_floor,
        f"{monthly_buckets} monthly bucket(s) after "
        f"{outcome.periods_advanced} annual cycle(s); a value equal to the "
        "cycle count means the monthly cadence inside the year never ran",
    )
    return rec


register_scenarios(
    {
        "long_horizon_monthly": scenario_long_horizon_monthly,
        "long_horizon_annual": scenario_long_horizon_annual,
    },
    tier=TIER_DEEP,
)
