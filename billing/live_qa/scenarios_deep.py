"""
billing/live_qa/scenarios_deep.py
==================================
DEEP-tier scenarios beyond the many-cycle horizon runs in
scenarios_long.py — each one forces a specific calendar or scale edge
case that a randomly-timed nightly run would only hit by luck.

WHAT EACH ONE IS ACTUALLY GUARDING
-----------------------------------
  month_end_anchor_divergence   Stripe PRESERVES a subscription's billing
                                 anchor day (the 31st stays the 31st,
                                 clamped only in short months). dateutil's
                                 relativedelta CLAMPS instead (Jan 31 ->
                                 Feb 28 -> Mar 28 forever, never returning
                                 to 31). Every renewal already reads the
                                 period straight from Stripe rather than
                                 computing it — this scenario forces the
                                 anchor onto the 31st specifically so that
                                 divergence, if it existed, could not hide
                                 behind a randomly-timed run.
  leap_year_annual_anniversary  Same idea for an annual plan anchored on
                                 Feb 29: the anniversary must land on
                                 Feb 28 in non-leap years and back on
                                 Feb 29 when a leap year is reached again,
                                 exactly as Stripe computes it.
  carry_over_and_max_bank       Uses the REAL configured plan's
                                 carry_over_percent / max_bank (never
                                 invented values — this suite runs
                                 against real, priced plans) as an oracle:
                                 computes the expected rollover with the
                                 same formula production code uses, then
                                 proves a REAL Stripe-driven renewal
                                 produces that exact number, with an
                                 expiry anchored to the real renewal
                                 instant.
  renewal_history_growth_stays_linear
                                 Many consecutive renewals must leave
                                 EXACTLY one active subscription row and
                                 EXACTLY one un-retired MONTHLY bucket —
                                 never more. A bug that double-processes
                                 an event or leaves a bucket stuck
                                 unprocessed only shows up at scale, which
                                 is why this runs far more cycles than the
                                 general long-horizon scenario needs to.
  invoice_lookback_finds_renewal_behind_a_proration
                                 _find_new_period_paid_invoice's fallback
                                 scan exists precisely because "the newest
                                 invoice can legitimately be an upgrade
                                 proration that landed after the cycle
                                 invoice" (its own docstring). This forces
                                 exactly that ordering on real Stripe and
                                 proves the sweep still finds the renewal
                                 invoice behind it. The scan's page size
                                 is bounded (_INVOICE_LOOKBACK_LIMIT) —
                                 that boundary is proven deterministically
                                 in test_invoice_lookback_boundary.py
                                 instead of here, since reproducing it on
                                 real Stripe would mean manufacturing ten-
                                 plus real paid invoices per run for a
                                 fact that a mock proves exactly as well.
"""

from __future__ import annotations

import logging
import time
from calendar import monthrange
from datetime import datetime
from datetime import timezone as dt_timezone

from dateutil.relativedelta import relativedelta
from django.utils import timezone as dj_timezone

from billing.imports import stripe
from billing.models import (
    BillingInterval,
    CreditBucketType,
    PlanTier,
    StripeSubscriptionStatus,
)
from billing.services import SubscriptionService
from billing.stripe_live_qa import CARD_OK, CheckRecorder, guarded_call, require_plan
from billing.stripe_live_qa_scenarios import (
    ADVANCE_OVERSHOOT_SECONDS,
    TIER_DEEP,
    Subscriber,
    _advance_past_boundary,
    _establish_subscriber,
    _stripe_period_end,
    register_scenarios,
)
from billing.stripe_service import extract_subscription_billing_period

from .clock import LongHorizonRunner

logger = logging.getLogger(__name__)


def _next_month_end_timestamp(
    after_ts: int, day: int, *, months_ahead: int = 60
) -> int:
    """
    Unix timestamp for the next month, after `after_ts`, whose last day is
    at least `day` — i.e. the next month in which day `day` genuinely
    exists (day=31 -> next 31-day month; day=29 -> next leap February).

    Scans up to `months_ahead` months forward. A leap February can be up
    to ~48 months away in the worst case, so the default comfortably
    covers it.
    """
    dt = datetime.fromtimestamp(after_ts, tz=dt_timezone.utc)
    year, month = dt.year, dt.month
    for _ in range(months_ahead):
        month += 1
        if month > 12:
            month = 1
            year += 1
        if monthrange(year, month)[1] >= day:
            return int(
                datetime(year, month, day, 12, 0, 0, tzinfo=dt_timezone.utc).timestamp()
            )
    raise RuntimeError(
        f"no month with a day-{day} within {months_ahead} months of {after_ts}"
    )


def _establish_subscriber_at(
    harness, rec: CheckRecorder, *, plan, label: str, start_timestamp: int, card=CARD_OK
) -> Subscriber:
    """
    Like stripe_live_qa_scenarios._establish_subscriber, except the test
    clock is advanced to `start_timestamp` BEFORE the customer and
    subscription are created — so the subscription's billing anchor lands
    on that exact calendar date instead of whenever the suite happened to
    run.
    """
    user = harness.create_local_user(label)
    clock = harness.create_test_clock(label)
    harness.advance_clock_to(clock["id"], start_timestamp)
    customer = harness.create_customer(email=user.email, clock_id=clock["id"])
    harness.attach_card(customer_id=customer["id"], token=card)
    stripe_sub = harness.create_subscription(
        customer_id=customer["id"], price_id=plan.stripe_price_id
    )

    period_start, period_end = extract_subscription_billing_period(stripe_sub)
    rec.expect(
        "billing period is readable from the anchor-forced subscription",
        period_start is not None and period_end is not None,
        f"extract_subscription_billing_period({stripe_sub['id']}) -> "
        f"({period_start}, {period_end})",
    )

    user_sub = SubscriptionService.activate_subscription(
        user, plan, period_start=period_start, period_end=period_end
    )
    user_sub.stripe_subscription_id = stripe_sub["id"]
    user_sub.stripe_customer_id = customer["id"]
    user_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
    user_sub.save(
        update_fields=[
            "stripe_subscription_id",
            "stripe_customer_id",
            "stripe_status",
            "updated_at",
        ]
    )

    from billing.models import CreditWallet

    CreditWallet.objects.filter(user=user).update(stripe_customer_id=customer["id"])

    register_actor = getattr(harness, "register_actor", None)
    if register_actor is not None:
        register_actor(customer["id"], user=user, subscription_id=stripe_sub["id"])

    return Subscriber(
        user=user,
        clock_id=clock["id"],
        customer_id=customer["id"],
        stripe_subscription_id=stripe_sub["id"],
        plan=plan,
    )


def scenario_month_end_anchor_divergence(harness) -> CheckRecorder:
    """A subscription anchored on the 31st must track Stripe's REAL
    anchor across short months, never a clamped-forever local guess."""
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)

    start = _next_month_end_timestamp(int(time.time()), 31)
    sub = _establish_subscriber_at(
        harness, rec, plan=plan, label="month-end", start_timestamp=start
    )
    harness.drain_events(customer_id=sub.customer_id)

    # A manual cycle-by-cycle loop rather than LongHorizonRunner: what
    # matters here is the day-of-month Stripe reports at EVERY cycle
    # (March, the second cycle from a Jan-31 anchor, is already a 31-day
    # month), not just the final state.
    MONTH_END_CYCLES = 4
    saw_day_31_again = False
    for cycle in range(1, MONTH_END_CYCLES + 1):
        _, period_end = _stripe_period_end(harness, sub.stripe_subscription_id)
        if not rec.expect(
            f"cycle {cycle}: Stripe period readable before advancing",
            period_end is not None,
        ):
            return rec

        _advance_past_boundary(harness, sub, period_end)

        after = sub.local()
        if not rec.expect(
            f"cycle {cycle}: an active local subscription survives the renewal",
            after is not None,
        ):
            return rec

        _, stripe_end = _stripe_period_end(harness, sub.stripe_subscription_id)
        rec.expect_close(
            f"cycle {cycle}: local cycle end matches Stripe's real anchor, "
            "not a clamped-forever guess",
            after.billing_cycle_end,
            stripe_end,
        )
        if stripe_end is not None and stripe_end.day == 31:
            saw_day_31_again = True

    # From a Jan-31 anchor, the SECOND renewal already lands in March (a
    # 31-day month) — Stripe preserves the anchor day; relativedelta-style
    # clamping never returns to 31 once it has clamped once. Four cycles
    # is enough to guarantee at least one 31-day month regardless of
    # which month the run actually started in.
    rec.expect(
        "the anchor returned to day 31 in a later 31-day month — proving "
        "it is read fresh from Stripe each cycle, not clamped forever "
        "after the first short month",
        saw_day_31_again,
        "no cycle in this run reported day 31, across "
        f"{MONTH_END_CYCLES} consecutive renewals from a day-31 anchor",
    )
    return rec


def scenario_leap_year_annual_anniversary(harness) -> CheckRecorder:
    """An annual plan anchored on Feb 29 must land on Feb 28 in non-leap
    years and back on Feb 29 when a leap year is reached again."""
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.ANNUAL)

    start = _next_month_end_timestamp(int(time.time()), 29)
    sub = _establish_subscriber_at(
        harness, rec, plan=plan, label="leap-year", start_timestamp=start
    )
    harness.drain_events(customer_id=sub.customer_id)

    runner = LongHorizonRunner(
        harness,
        clock_id=sub.clock_id,
        customer_id=sub.customer_id,
        subscription_id=sub.stripe_subscription_id,
        max_periods=4,
    )
    outcome = runner.run()
    rec.expect(
        "leap-year horizon completed without an infrastructure fault",
        not outcome.is_failure,
        outcome.as_note(),
    )

    after = sub.local()
    if not rec.expect(
        "an active local subscription survives the leap-year horizon",
        after is not None,
    ):
        return rec

    _, stripe_end = _stripe_period_end(harness, sub.stripe_subscription_id)
    rec.expect_close(
        "local anniversary matches Stripe's real one across leap-year " "boundaries",
        after.billing_cycle_end,
        stripe_end,
    )
    rec.expect(
        "the anniversary landed on Feb 28 or 29, exactly what a "
        "Feb-29-anchored annual subscription should show",
        after.billing_cycle_end.month == 2 and after.billing_cycle_end.day in (28, 29),
        f"billing_cycle_end={after.billing_cycle_end.isoformat()}",
    )
    return rec


def scenario_carry_over_and_max_bank(harness) -> CheckRecorder:
    """The real renewal path must produce exactly the rollover the plan's
    OWN formula predicts, anchored to the real renewal instant — using
    whatever carry_over_percent/max_bank this plan is actually configured
    with, never an invented value."""
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    sub = _establish_subscriber(harness, rec, plan=plan, label="carryover")
    harness.drain_events(customer_id=sub.customer_id)

    from billing.models import CreditBucket

    wallet = sub.wallet()
    if not rec.expect("wallet exists before the renewal", wallet is not None):
        return rec

    bucket = (
        CreditBucket.objects.filter(
            wallet=wallet, bucket_type=CreditBucketType.MONTHLY, is_processed=False
        )
        .order_by("-created_at")
        .first()
    )
    if not rec.expect(
        "an un-retired monthly bucket exists to roll over", bucket is not None
    ):
        return rec

    # Leave a known, deterministic fraction unused.
    bucket.used_credits = bucket.total_credits // 4
    bucket.save(update_fields=["used_credits", "updated_at"])
    unused_credits = bucket.total_credits - bucket.used_credits

    expected_amount, expected_meta = wallet.compute_capped_rollover(
        plan, unused_credits, now=dj_timezone.now()
    )

    _, period_end = _stripe_period_end(harness, sub.stripe_subscription_id)
    if not rec.expect(
        "Stripe period readable before advancing", period_end is not None
    ):
        return rec

    _advance_past_boundary(harness, sub, period_end)

    after_wallet = sub.wallet()
    carry_bucket = (
        CreditBucket.objects.filter(
            wallet=after_wallet, bucket_type=CreditBucketType.CARRY_OVER
        )
        .order_by("-created_at")
        .first()
    )

    if expected_amount <= 0:
        rec.expect(
            f"carry_over_percent={plan.carry_over_percent}: no rollover was "
            "expected, and none was created",
            carry_bucket is None or carry_bucket.total_credits == 0,
            f"carry_bucket={carry_bucket!r}",
        )
        return rec

    if not rec.expect(
        "a CARRY_OVER bucket was created for a plan whose formula predicts " "one",
        carry_bucket is not None,
        f"expected {expected_amount} credits per the plan's own formula "
        f"({expected_meta})",
    ):
        return rec

    rec.expect_equal(
        "the real renewal's rollover matches the plan's own formula exactly",
        carry_bucket.total_credits,
        expected_amount,
        f"formula metadata: {expected_meta}",
    )

    expected_expiry = dj_timezone.now() + relativedelta(
        months=plan.carry_over_expiry_months
    )
    rec.expect_close(
        "the carry-over bucket expires carry_over_expiry_months after the "
        "REAL renewal instant",
        carry_bucket.expires_at,
        expected_expiry,
        tolerance_seconds=600,
    )
    return rec


def scenario_renewal_history_growth_stays_linear(harness) -> CheckRecorder:
    """Many renewals must leave exactly one active row and exactly one
    un-retired MONTHLY bucket — a double-processing bug only shows up at
    a scale the general long-horizon scenario doesn't need to reach."""
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    sub = _establish_subscriber(harness, rec, plan=plan, label="growth")
    harness.drain_events(customer_id=sub.customer_id)

    runner = LongHorizonRunner(
        harness,
        clock_id=sub.clock_id,
        customer_id=sub.customer_id,
        subscription_id=sub.stripe_subscription_id,
        max_periods=36,
    )
    outcome = runner.run()
    rec.expect(
        "the growth horizon completed without an infrastructure fault",
        not outcome.is_failure,
        outcome.as_note(),
    )
    if not rec.expect(
        "enough periods ran to make a growth-rate check meaningful",
        outcome.periods_advanced >= 12,
        outcome.as_note(),
    ):
        return rec

    from billing.models import BillingTransaction, CreditBucket, UserSubscription

    n = outcome.periods_advanced

    total_rows = UserSubscription.objects.filter(user=sub.user).count()
    active_rows = UserSubscription.objects.filter(user=sub.user, is_active=True).count()
    rec.expect_equal(
        "exactly one subscription row is active after many renewals",
        active_rows,
        1,
        f"{total_rows} total row(s) across {n} renewal(s)",
    )
    rec.expect_equal(
        "subscription row count grew EXACTLY linearly (one per renewal, "
        "plus the original) — not double-processed",
        total_rows,
        n + 1,
    )

    wallet = sub.wallet()
    monthly_buckets = CreditBucket.objects.filter(
        wallet=wallet, bucket_type=CreditBucketType.MONTHLY
    )
    rec.expect_equal(
        "MONTHLY bucket count grew exactly linearly too",
        monthly_buckets.count(),
        n + 1,
    )
    unretired = monthly_buckets.filter(is_processed=False).count()
    rec.expect_equal(
        "at most one MONTHLY bucket is left un-retired (the current one) — "
        "no stuck buckets accumulating from a failed retirement step",
        unretired,
        1,
        f"{unretired} un-retired MONTHLY bucket(s) out of {monthly_buckets.count()}",
    )

    txns = BillingTransaction.objects.filter(user_subscription__user=sub.user).count()
    rec.expect(
        "billing transaction rows did not silently duplicate across "
        "renewals (the C3 idempotency ledger held under repeated events)",
        txns <= n + 1,
        f"{txns} transaction row(s) for {n} renewal(s) — more than one per "
        f"renewal (plus the original) means an event was processed twice",
    )
    return rec


def scenario_invoice_lookback_finds_renewal_behind_a_proration(
    harness,
) -> CheckRecorder:
    """reconcile_subscription_renewals must still find the renewal
    invoice when a newer, non-qualifying proration invoice exists on top
    of it — its own docstring calls this out as a real, expected case."""
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    sub = _establish_subscriber(harness, rec, plan=plan, label="lookback")
    harness.drain_events(customer_id=sub.customer_id)

    before = sub.local()
    if not rec.expect(
        "local subscription exists before the missed webhook", before is not None
    ):
        return rec
    before_end = before.billing_cycle_end
    before_buckets = sub.monthly_bucket_count()

    _, period_end = _stripe_period_end(harness, sub.stripe_subscription_id)
    if not rec.expect(
        "Stripe period readable before advancing", period_end is not None
    ):
        return rec

    harness.advance_clock_to(
        sub.clock_id, int(period_end.timestamp()) + ADVANCE_OVERSHOOT_SECONDS
    )
    # No drain_events: the renewal invoice exists on Stripe but has not
    # been processed locally yet — exactly the condition the sweep exists
    # for.

    stripe_sub = harness.retrieve_subscription(sub.stripe_subscription_id)
    items = (stripe_sub.get("items") or {}).get("data") or []
    if not rec.expect("Stripe exposes an item to bump", bool(items)):
        return rec

    # A standard, well-documented way to force an immediate, PAID,
    # non-renewal invoice on the same subscription: a quantity change
    # with proration_behavior="always_invoice". This lands strictly AFTER
    # the renewal invoice already sitting on Stripe's side, which is
    # exactly the ordering _find_new_period_paid_invoice's fallback scan
    # exists to handle.
    guarded_call(
        stripe.Subscription.modify,
        sub.stripe_subscription_id,
        items=[{"id": items[0]["id"], "quantity": 2}],
        proration_behavior="always_invoice",
    )
    guarded_call(
        stripe.Subscription.modify,
        sub.stripe_subscription_id,
        items=[{"id": items[0]["id"], "quantity": 1}],
        proration_behavior="always_invoice",
    )

    refreshed = harness.retrieve_subscription(sub.stripe_subscription_id)
    latest_id = refreshed.get("latest_invoice")
    latest_invoice = (
        guarded_call(stripe.Invoice.retrieve, latest_id) if latest_id else None
    )
    rec.expect(
        "the newest invoice is now the non-renewal proration, not the "
        "renewal invoice — confirming the fallback scan is actually "
        "required for this run, not the fast path",
        latest_invoice is not None
        and latest_invoice.get("billing_reason") != "subscription_cycle",
        f"latest_invoice billing_reason="
        f"{(latest_invoice or {}).get('billing_reason')!r}",
    )

    from billing.tasks import reconcile_subscription_renewals

    summary = reconcile_subscription_renewals()
    logger.info("[LIVE QA %s] reconcile summary: %s", harness.run_id, summary)

    after = sub.local()
    if not rec.expect(
        "an active local subscription exists after the sweep", after is not None
    ):
        return rec

    rec.expect(
        "the sweep still found and applied the renewal, despite a newer "
        "non-qualifying invoice sitting on top of it",
        after.billing_cycle_end > before_end,
        f"before={before_end.isoformat()}, after={after.billing_cycle_end.isoformat()}",
    )
    rec.expect_equal(
        "exactly one new credit cycle was granted, not zero and not two",
        sub.monthly_bucket_count(),
        before_buckets + 1,
    )

    harness.drain_events(customer_id=sub.customer_id)
    return rec


register_scenarios(
    {
        "month_end_anchor_divergence": scenario_month_end_anchor_divergence,
        "leap_year_annual_anniversary": scenario_leap_year_annual_anniversary,
        "carry_over_and_max_bank": scenario_carry_over_and_max_bank,
        "renewal_history_growth_stays_linear": (
            scenario_renewal_history_growth_stays_linear
        ),
        "invoice_lookback_finds_renewal_behind_a_proration": (
            scenario_invoice_lookback_finds_renewal_behind_a_proration
        ),
    },
    tier=TIER_DEEP,
)
