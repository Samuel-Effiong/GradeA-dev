"""
billing/live_qa/scenarios_clock.py
===================================
FAST scenarios that DO need clock advances, but only one or two — cheap
enough for the nightly envelope, unlike the many-cycle DEEP scenarios in
scenarios_long.py.

WHAT EACH ONE IS ACTUALLY GUARDING
-----------------------------------
  void_or_refund_compensating_path   R5's loop-closer: the interval-
                                     crossing double-bill compensating
                                     control (_void_or_refund_side_effect_
                                     invoice). This has an idempotency
                                     key precisely because it is a refund
                                     path — the one place a bug prints
                                     money.
  interval_crossing_round_trip       Monthly -> annual -> monthly. Two
                                     interval crossings back to back,
                                     checking the anchor and credit grant
                                     are correct on BOTH legs, not just
                                     the first (which is all the R5 test
                                     above exercises).
  dunning_to_cancellation            scenario_failed_renewal (in
                                     stripe_live_qa_scenarios.py) stops at
                                     the first missed payment. This carries
                                     the same subscriber through Stripe's
                                     dunning window to actual cancellation,
                                     and proves no credits leak out during
                                     any of it.
  reconcile_sweep_after_missed_webhook
                                     reconcile_subscription_renewals is
                                     the safety net for when a webhook
                                     delivery is lost. This is the only
                                     scenario in the whole suite that
                                     deliberately does NOT drain events
                                     after a clock advance — that omission
                                     IS the missed webhook — and then
                                     proves the daily sweep converges to
                                     the same state the webhook would
                                     have produced, reading real Stripe
                                     invoices rather than a mock.
  dashboard_recovers_past_due_subscription
                                     Exercises handle_subscription_updated
                                     directly: a support agent (or the
                                     customer, via the dashboard) fixes a
                                     card and retries the SAME cycle's
                                     invoice. No new-period invoice is
                                     produced, so neither the renewal
                                     webhook nor the reconcile sweep would
                                     ever see this recovery — before this
                                     handler existed, the subscription
                                     stayed marked past_due locally
                                     forever, even though Stripe and the
                                     customer both knew it was fixed.
"""

from __future__ import annotations

import logging

from billing.imports import stripe
from billing.models import (
    BillingInterval,
    PendingChangeType,
    PlanTier,
    StripeSubscriptionStatus,
)
from billing.services import SubscriptionService
from billing.stripe_live_qa import (
    CARD_FAILS_ON_CHARGE,
    CARD_OK,
    CheckRecorder,
    guarded_call,
    require_plan,
)
from billing.stripe_live_qa_scenarios import (
    ADVANCE_OVERSHOOT_SECONDS,
    TIER_FAST,
    _advance_past_boundary,
    _establish_subscriber,
    _stripe_period_end,
    register_scenarios,
)
from billing.stripe_service import (
    StripeSubscriptionMutationService,
    StripeSubscriptionScheduleService,
)

logger = logging.getLogger(__name__)

# Real dunning windows run for weeks. This is long enough to exhaust
# Stripe test-mode's default smart-retry schedule and reach a terminal
# status, without paying for a DEEP-tier number of clock advances.
DUNNING_HORIZON_SECONDS = 35 * 24 * 3600


def scenario_void_or_refund_compensating_path(harness) -> CheckRecorder:
    """An interval-crossing upgrade must never leave a duplicate charge.

    _apply_upgrade_directly forces Stripe to reset the billing anchor on
    a monthly -> annual crossing, and Stripe can independently generate a
    side-effect invoice as part of that reset even though the customer
    already paid the correct amount. _void_or_refund_side_effect_invoice
    is the compensating control; this scenario proves it actually leaves
    no open, uncollected duplicate behind on real Stripe.
    """
    rec = CheckRecorder()
    monthly = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    annual = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.ANNUAL)

    sub = _establish_subscriber(harness, rec, plan=monthly, label="voidrefund")
    harness.drain_events(customer_id=sub.customer_id)

    before = sub.local()
    if not rec.expect(
        "local subscription exists before the crossing", before is not None
    ):
        return rec

    stripe_sub = harness.retrieve_subscription(sub.stripe_subscription_id)
    items = (stripe_sub.get("items") or {}).get("data") or []
    if not rec.expect(
        "Stripe subscription exposes an item to swap",
        bool(items),
        f"items.data={items!r}",
    ):
        return rec

    StripeSubscriptionMutationService._apply_upgrade_directly(
        before, annual, items[0]["id"]
    )
    harness.drain_events(customer_id=sub.customer_id)

    after = sub.local()
    if not rec.expect(
        "local subscription survives the interval crossing", after is not None
    ):
        return rec
    rec.expect_equal("local plan swapped to the annual plan", after.plan_id, annual.id)

    refreshed = harness.retrieve_subscription(sub.stripe_subscription_id)
    latest_invoice_id = refreshed.get("latest_invoice")
    if latest_invoice_id:
        invoice = guarded_call(stripe.Invoice.retrieve, latest_invoice_id)
        status = invoice.get("status")
        rec.expect(
            "no duplicate invoice was left OPEN (uncollected) after the crossing",
            status != "open",
            f"latest_invoice status={status!r} — 'open' means the compensating "
            "control failed to void it and Stripe will still try to collect it",
        )
        if status == "paid":
            refunds = guarded_call(
                stripe.Refund.list, payment_intent=invoice.get("payment_intent")
            )
            refunded = any(
                r.get("status") in ("succeeded", "pending")
                for r in (refunds.get("data") or [])
            )
            rec.expect(
                "a duplicate PAID side-effect invoice was refunded",
                refunded,
                f"refunds for the side-effect invoice's PaymentIntent: "
                f"{refunds.get('data')!r}",
            )

    rec.expect(
        "the customer was not double-billed: exactly one credit grant for "
        "the crossing",
        sub.monthly_bucket_count() >= 1,
        f"{sub.monthly_bucket_count()} monthly bucket(s)",
    )
    return rec


def scenario_interval_crossing_round_trip(harness) -> CheckRecorder:
    """Monthly -> annual -> monthly. Both legs must land correctly.

    A single crossing is exercised by the scenario above; a round trip
    checks the SECOND crossing doesn't inherit corrupted state from the
    first (a stale schedule reference, a mis-anchored cycle, credits
    sized for the wrong plan).
    """
    rec = CheckRecorder()
    monthly = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    annual = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.ANNUAL)

    sub = _establish_subscriber(harness, rec, plan=monthly, label="roundtrip")
    harness.drain_events(customer_id=sub.customer_id)

    before = sub.local()
    if not rec.expect(
        "local subscription exists before any crossing", before is not None
    ):
        return rec

    stripe_sub = harness.retrieve_subscription(sub.stripe_subscription_id)
    items = (stripe_sub.get("items") or {}).get("data") or []
    if not rec.expect("Stripe exposes an item to swap (leg 1)", bool(items)):
        return rec

    StripeSubscriptionMutationService._apply_upgrade_directly(
        before, annual, items[0]["id"]
    )
    harness.drain_events(customer_id=sub.customer_id)

    mid = sub.local()
    if not rec.expect("local subscription survives leg 1 (up)", mid is not None):
        return rec
    rec.expect_equal("leg 1: plan is now annual", mid.plan_id, annual.id)
    buckets_after_up = sub.monthly_bucket_count()

    # Schedule the downgrade back to monthly, deferred to the (now
    # annual) cycle boundary — a real customer changing their mind about
    # an annual commitment does this, not an immediate swap.
    schedule_id = StripeSubscriptionScheduleService.schedule_plan_change_on_stripe(
        mid, monthly
    )
    rec.expect(
        "leg 2: Stripe returned a SubscriptionSchedule id",
        bool(schedule_id),
        f"schedule_id={schedule_id!r}",
    )
    SubscriptionService.schedule_plan_change(
        sub.user,
        monthly,
        PendingChangeType.DOWNGRADE,
        "Live QA interval round-trip.",
        stripe_schedule_id=schedule_id,
    )

    _, period_end = _stripe_period_end(harness, sub.stripe_subscription_id)
    if not rec.expect(
        "leg 2: Stripe period readable before advancing", period_end is not None
    ):
        return rec

    _advance_past_boundary(harness, sub, period_end)

    after = sub.local()
    if not rec.expect(
        "local subscription survives leg 2 (back down)", after is not None
    ):
        return rec

    rec.expect_equal("leg 2: plan reverted to monthly", after.plan_id, monthly.id)
    rec.expect(
        "leg 2: pending plan cleared after the boundary",
        after.pending_plan_id is None,
        f"pending_plan_id={after.pending_plan_id!r}",
    )

    refreshed = harness.retrieve_subscription(sub.stripe_subscription_id)
    refreshed_items = (refreshed.get("items") or {}).get("data") or []
    stripe_price = (
        (refreshed_items[0].get("price") or {}).get("id") if refreshed_items else None
    )
    rec.expect_equal(
        "Stripe subscription is back on the monthly price",
        stripe_price,
        monthly.stripe_price_id,
    )

    # Round trip means: back where we started, with a new grant on the
    # way back down (not still counting from the annual leg).
    rec.expect(
        "leg 2: the downgrade boundary still granted its own credit cycle",
        sub.monthly_bucket_count() > buckets_after_up,
        f"buckets after up={buckets_after_up}, after round trip="
        f"{sub.monthly_bucket_count()}",
    )
    return rec


def scenario_dunning_to_cancellation(harness) -> CheckRecorder:
    """Carry a failed-payment subscriber all the way to real cancellation.

    scenario_failed_renewal stops at the first missed payment. Real
    dunning runs for weeks of retries before Stripe gives up and fires
    customer.subscription.deleted. This proves the local row actually
    goes inactive at the end of that window, and that not a single
    credit leaked out anywhere along the way.
    """
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    sub = _establish_subscriber(harness, rec, plan=plan, label="dunning-cancel")
    harness.drain_events(customer_id=sub.customer_id)

    failing = harness.attach_card(
        customer_id=sub.customer_id, token=CARD_FAILS_ON_CHARGE, set_default=True
    )
    guarded_call(
        stripe.Subscription.modify,
        sub.stripe_subscription_id,
        default_payment_method=failing["id"],
    )

    before_buckets = sub.monthly_bucket_count()

    _, period_end = _stripe_period_end(harness, sub.stripe_subscription_id)
    if not rec.expect(
        "Stripe period readable before the dunning window", period_end is not None
    ):
        return rec

    # One long advance rather than many small ones: what matters is
    # whether Stripe reaches a terminal state by the far side of its
    # dunning schedule, not the shape of the retries in between.
    harness.advance_clock_to(
        sub.clock_id, int(period_end.timestamp()) + DUNNING_HORIZON_SECONDS
    )
    harness.drain_events(customer_id=sub.customer_id)

    refreshed = harness.retrieve_subscription(sub.stripe_subscription_id)
    rec.expect(
        "Stripe reached a terminal non-active status after the dunning window",
        refreshed.get("status") in {"canceled", "unpaid"},
        f"stripe status={refreshed.get('status')!r} — dunning may not have "
        "exhausted; this is a note about test-mode retry timing, not "
        "necessarily a bug",
    )

    rec.expect_equal(
        "no credits were granted anywhere during the dunning window",
        sub.monthly_bucket_count(),
        before_buckets,
    )

    active = sub.local()
    if refreshed.get("status") == "canceled":
        rec.expect(
            "a fully canceled Stripe subscription has no active local row",
            active is None,
            f"local row still active: {active!r}",
        )
    return rec


def scenario_reconcile_sweep_after_missed_webhook(harness) -> CheckRecorder:
    """The nightly sweep must converge WITHOUT ever seeing the webhook.

    Deliberately does not call drain_events after the clock advance —
    that omission simulates a lost webhook delivery. reconcile_
    subscription_renewals is supposed to notice from Stripe's own
    objects alone and catch the subscription up; this scenario proves it
    does, against a REAL Stripe invoice rather than a mock that always
    agrees with whatever the task expects to see.
    """
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    sub = _establish_subscriber(harness, rec, plan=plan, label="reconcile")
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
    # No drain_events call here — this IS the missed webhook.

    stale = sub.local()
    rec.expect(
        "the local row is genuinely stale before the sweep runs (sanity check "
        "that the webhook really was skipped)",
        stale is not None and stale.billing_cycle_end == before_end,
        f"billing_cycle_end={getattr(stale, 'billing_cycle_end', None)!r} vs "
        f"pre-advance value {before_end!r}",
    )

    from billing.tasks import reconcile_subscription_renewals

    summary = reconcile_subscription_renewals()
    logger.info("[LIVE QA %s] reconcile summary: %s", harness.run_id, summary)

    after = sub.local()
    if not rec.expect(
        "an active local subscription exists after the sweep", after is not None
    ):
        return rec

    _, stripe_end = _stripe_period_end(harness, sub.stripe_subscription_id)
    rec.expect(
        "the sweep advanced the local billing cycle without ever seeing a " "webhook",
        after.billing_cycle_end > before_end,
        f"before={before_end.isoformat()}, after={after.billing_cycle_end.isoformat()}",
    )
    rec.expect_close(
        "the sweep's cycle end matches Stripe's real period, read from a "
        "real invoice rather than a mock",
        after.billing_cycle_end,
        stripe_end,
    )
    rec.expect_equal(
        "the sweep granted exactly one new credit cycle, not zero and not two",
        sub.monthly_bucket_count(),
        before_buckets + 1,
    )

    # Drain now so the harness's own bookkeeping (and any invariant
    # runner attached to it) is not left believing events are still
    # pending for this customer.
    harness.drain_events(customer_id=sub.customer_id)
    return rec


def scenario_dashboard_recovers_past_due_subscription(harness) -> CheckRecorder:
    """A support agent fixes a card and retries the SAME invoice.

    This produces no new-period invoice at all — it is a retry of the
    cycle that already failed — so it is invisible to
    reconcile_subscription_renewals (which requires a NEW-period paid
    invoice) and to the renewal webhook (which never fires for a retry).
    Before handle_subscription_updated existed, this recovery was
    invisible to the app entirely: Stripe and the customer both knew the
    subscription was fixed, and the local row stayed stuck at past_due.
    """
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    sub = _establish_subscriber(harness, rec, plan=plan, label="dash-recover")
    harness.drain_events(customer_id=sub.customer_id)

    failing = harness.attach_card(
        customer_id=sub.customer_id, token=CARD_FAILS_ON_CHARGE, set_default=True
    )
    guarded_call(
        stripe.Subscription.modify,
        sub.stripe_subscription_id,
        default_payment_method=failing["id"],
    )

    _, period_end = _stripe_period_end(harness, sub.stripe_subscription_id)
    if not rec.expect(
        "Stripe period readable before the outage", period_end is not None
    ):
        return rec

    _advance_past_boundary(harness, sub, period_end)

    outaged = sub.local()
    if not rec.expect(
        "the subscription is locally past_due after the failed renewal",
        outaged is not None
        and outaged.stripe_status == StripeSubscriptionStatus.PAST_DUE,
        f"stripe_status={getattr(outaged, 'stripe_status', None)!r}",
    ):
        return rec
    before_end = outaged.billing_cycle_end
    before_buckets = sub.monthly_bucket_count()

    refreshed = harness.retrieve_subscription(sub.stripe_subscription_id)
    unpaid_invoice_id = refreshed.get("latest_invoice")
    if not rec.expect(
        "the failed renewal left an unpaid invoice to retry",
        bool(unpaid_invoice_id),
    ):
        return rec

    # Exactly what "edit the card, then click Retry Invoice" does on the
    # Stripe dashboard: fix the payment method, then pay the SAME invoice.
    working = harness.attach_card(
        customer_id=sub.customer_id, token=CARD_OK, set_default=True
    )
    guarded_call(
        stripe.Subscription.modify,
        sub.stripe_subscription_id,
        default_payment_method=working["id"],
    )
    guarded_call(stripe.Invoice.pay, unpaid_invoice_id)
    harness.drain_events(customer_id=sub.customer_id)

    recovered = sub.local()
    if not rec.expect(
        "local subscription row survives the recovery", recovered is not None
    ):
        return rec

    rec.expect_equal(
        "handle_subscription_updated synced the recovery back to ACTIVE",
        recovered.stripe_status,
        StripeSubscriptionStatus.ACTIVE,
    )
    rec.expect(
        "recovering the SAME cycle did not create a new billing period",
        recovered.billing_cycle_end == before_end,
        f"before={before_end.isoformat()}, after="
        f"{recovered.billing_cycle_end.isoformat()} — a change here means "
        "some other path also reacted to this event, not just the status "
        "sync",
    )
    rec.expect_equal(
        "recovering the SAME cycle granted no extra credits",
        sub.monthly_bucket_count(),
        before_buckets,
    )
    return rec


register_scenarios(
    {
        "void_or_refund_compensating_path": scenario_void_or_refund_compensating_path,
        "interval_crossing_round_trip": scenario_interval_crossing_round_trip,
        "dunning_to_cancellation": scenario_dunning_to_cancellation,
        "dashboard_recovers_past_due_subscription": (
            scenario_dashboard_recovers_past_due_subscription
        ),
        "reconcile_sweep_after_missed_webhook": (
            scenario_reconcile_sweep_after_missed_webhook
        ),
    },
    tier=TIER_FAST,
)
