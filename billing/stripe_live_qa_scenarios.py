"""
billing/stripe_live_qa_scenarios.py
===================================
The scenarios themselves, plus the runner.

Each scenario drives REAL Stripe test-mode objects through REAL billing
code and returns a `CheckRecorder`. Scenarios record every failed
assertion rather than raising on the first one: this runs nightly, and a
suite that hides the second bug until you re-run it costs a day per bug.

WHAT EACH SCENARIO IS ACTUALLY GUARDING
---------------------------------------
These are not generic smoke tests. Each one targets a specific bug class
that mocks structurally cannot catch:

  renewals            C1/C2. Reads the billing period off a REAL
                      Subscription and REAL invoices, three cycles deep.
                      This is the assertion that would have caught the
                      2025-03-31 field move on the day it shipped.
  trial_conversion    The trial -> paid handoff, where the local row is
                      mutated in place and the period must come from
                      Stripe's invoice rather than wall-clock arithmetic.
  immediate_upgrade   Cycle integrity: a same-interval upgrade must NOT
                      move the billing anchor. Stripe is the authority on
                      whether it moved.
  deferred_downgrade  A real SubscriptionSchedule phase transition, which
                      only exists on Stripe's side until it fires.
  failed_renewal      Dunning. The one that matters most for money: a
                      failed charge must never grant credits.

CREDIT ASSERTIONS
-----------------
Every scenario that involves money checks credits, because "the customer
paid and got nothing" and "the customer did not pay and got everything"
are the two failures that cost real money. A period that looks right
while the wallet is wrong is still a broken renewal.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .imports import stripe
from .models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    PendingChangeType,
    PlanTier,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from .services import SubscriptionService
from .stripe_live_qa import (
    CARD_FAILS_ON_CHARGE,
    CARD_OK,
    CheckRecorder,
    LiveQAConfigurationError,
    LiveQAHarness,
    ScenarioResult,
    SuiteResult,
    assert_live_qa_enabled,
    guarded_call,
    require_plan,
)
from .stripe_service import (
    StripeSubscriptionMutationService,
    StripeSubscriptionScheduleService,
    extract_subscription_billing_period,
)

logger = logging.getLogger(__name__)

RENEWAL_CYCLES = 3

# Land clearly PAST the boundary, not exactly on it. Stripe bills at the
# boundary instant, so advancing to exactly `current_period_end` races
# the renewal we are trying to observe.
ADVANCE_OVERSHOOT_SECONDS = 3600


@dataclass
class Subscriber:
    """One QA subscriber. `local()` RE-FETCHES deliberately.

    Renewal creates a NEW UserSubscription row and deactivates the old
    one, so any reference held across an advance is stale by definition.
    Every assertion in this module reads through `local()` for that
    reason.
    """

    user: object
    clock_id: str
    customer_id: str
    stripe_subscription_id: str
    plan: SubscriptionPlan

    def local(self):
        return (
            UserSubscription.objects.filter(user=self.user, is_active=True)
            .select_related("plan", "pending_plan")
            .first()
        )

    def wallet(self):
        return CreditWallet.objects.filter(user=self.user).first()

    def monthly_bucket_count(self) -> int:
        wallet = self.wallet()
        if wallet is None:
            return 0
        return CreditBucket.objects.filter(
            wallet=wallet, bucket_type=CreditBucketType.MONTHLY
        ).count()


def _establish_subscriber(
    harness: LiveQAHarness,
    rec: CheckRecorder,
    *,
    plan: SubscriptionPlan,
    label: str,
    card: str = CARD_OK,
    **subscription_kwargs,
) -> Subscriber:
    """
    Build the state a real paid subscriber has, the way
    `checkout.session.completed` builds it.

    Checkout itself needs a browser (see stripe_live_qa module docstring),
    so we create the Stripe subscription directly and then perform the
    same local activation the webhook performs. Everything downstream of
    this point is the genuine production path.
    """
    user = harness.create_local_user(label)
    clock = harness.create_test_clock(label)
    customer = harness.create_customer(email=user.email, clock_id=clock["id"])
    harness.attach_card(customer_id=customer["id"], token=card)
    stripe_sub = harness.create_subscription(
        customer_id=customer["id"],
        price_id=plan.stripe_price_id,
        **subscription_kwargs,
    )

    period_start, period_end = extract_subscription_billing_period(stripe_sub)
    rec.expect(
        "C1 guard: billing period is readable from a real Stripe Subscription",
        period_start is not None and period_end is not None,
        f"extract_subscription_billing_period({stripe_sub['id']}) -> "
        f"({period_start}, {period_end}). None here means Stripe moved the "
        f"field again — exactly the C1 failure.",
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

    # Without this, any later service call that resolves a customer would
    # create a SECOND Stripe customer — one with no test clock, which
    # cannot be advanced and would silently break the scenario.
    CreditWallet.objects.filter(user=user).update(stripe_customer_id=customer["id"])

    # Tell the invariant runner (when there is one — the single-threaded
    # harness has none) which local objects belong to this Stripe
    # customer. Everything else about invariant evaluation is automatic:
    # it happens on every drain_events, which every scenario already does
    # after every state-changing step.
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


def _stripe_period_end(harness: LiveQAHarness, subscription_id: str):
    stripe_sub = harness.retrieve_subscription(subscription_id)
    _, period_end = extract_subscription_billing_period(stripe_sub)
    return stripe_sub, period_end


def _advance_past_boundary(harness: LiveQAHarness, sub: Subscriber, period_end) -> None:
    harness.advance_clock_to(
        sub.clock_id, int(period_end.timestamp()) + ADVANCE_OVERSHOOT_SECONDS
    )
    harness.drain_events(customer_id=sub.customer_id)


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------


def scenario_renewals(harness: LiveQAHarness) -> CheckRecorder:
    """Subscribe, then renew three consecutive cycles against real Stripe."""
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    sub = _establish_subscriber(harness, rec, plan=plan, label="renew")
    harness.drain_events(customer_id=sub.customer_id)

    baseline_buckets = sub.monthly_bucket_count()

    for cycle in range(1, RENEWAL_CYCLES + 1):
        _, period_end = _stripe_period_end(harness, sub.stripe_subscription_id)
        if period_end is None:
            rec.expect(
                f"cycle {cycle}: Stripe period readable before advancing",
                False,
                "extract_subscription_billing_period returned None",
            )
            break

        before = sub.local()
        if not rec.expect(
            f"cycle {cycle}: local subscription exists before advancing",
            before is not None,
        ):
            break
        before_end = before.billing_cycle_end

        _advance_past_boundary(harness, sub, period_end)

        after = sub.local()
        if not rec.expect(
            f"cycle {cycle}: an active local subscription survives the renewal",
            after is not None,
            "no active UserSubscription row after renewal — the customer "
            "paid and now has nothing",
        ):
            break

        _, stripe_end = _stripe_period_end(harness, sub.stripe_subscription_id)

        rec.expect(
            f"cycle {cycle}: local billing cycle moved forward",
            after.billing_cycle_end > before_end,
            f"before={before_end.isoformat()}, after={after.billing_cycle_end.isoformat()}",
        )
        rec.expect_close(
            f"cycle {cycle}: local cycle end is anchored to Stripe's period (C2)",
            after.billing_cycle_end,
            stripe_end,
        )
        rec.expect_equal(
            f"cycle {cycle}: subscription still ACTIVE",
            after.stripe_status,
            StripeSubscriptionStatus.ACTIVE,
        )
        rec.expect_equal(
            f"cycle {cycle}: Stripe subscription id survived the new row",
            after.stripe_subscription_id,
            sub.stripe_subscription_id,
        )
        rec.expect_equal(
            f"cycle {cycle}: a fresh monthly credit bucket was granted",
            sub.monthly_bucket_count(),
            baseline_buckets + cycle,
        )

    return rec


def scenario_trial_conversion(harness: LiveQAHarness) -> CheckRecorder:
    """A Stripe-side trial that converts to paid when the clock passes it."""
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)

    user = harness.create_local_user("trial")
    clock = harness.create_test_clock("trial")
    customer = harness.create_customer(email=user.email, clock_id=clock["id"])
    harness.attach_card(customer_id=customer["id"], token=CARD_OK)
    stripe_sub = harness.create_subscription(
        customer_id=customer["id"],
        price_id=plan.stripe_price_id,
        trial_period_days=SubscriptionService.TRIAL_DURATION_DAYS,
    )

    trial_sub = SubscriptionService.activate_free_trial(user, plan)
    trial_sub.stripe_subscription_id = stripe_sub["id"]
    trial_sub.stripe_customer_id = customer["id"]
    trial_sub.stripe_status = StripeSubscriptionStatus.TRIALING
    trial_sub.save(
        update_fields=[
            "stripe_subscription_id",
            "stripe_customer_id",
            "stripe_status",
            "updated_at",
        ]
    )
    CreditWallet.objects.filter(user=user).update(stripe_customer_id=customer["id"])

    sub = Subscriber(
        user=user,
        clock_id=clock["id"],
        customer_id=customer["id"],
        stripe_subscription_id=stripe_sub["id"],
        plan=plan,
    )
    harness.drain_events(customer_id=customer["id"])

    starting = sub.local()
    rec.expect(
        "local row starts as a trial",
        bool(starting and starting.is_trial),
        f"is_trial={getattr(starting, 'is_trial', None)!r}",
    )

    trial_end = stripe_sub.get("trial_end")
    if not rec.expect(
        "Stripe reports a trial_end on the created subscription",
        bool(trial_end),
        f"trial_end={trial_end!r}",
    ):
        return rec

    harness.advance_clock_to(sub.clock_id, int(trial_end) + ADVANCE_OVERSHOOT_SECONDS)
    harness.drain_events(customer_id=sub.customer_id)

    converted = sub.local()
    if not rec.expect(
        "an active local subscription survives trial conversion",
        converted is not None,
    ):
        return rec

    _, stripe_end = _stripe_period_end(harness, sub.stripe_subscription_id)

    rec.expect(
        "trial flag cleared after conversion",
        converted.is_trial is False,
        f"is_trial={converted.is_trial!r}",
    )
    rec.expect_close(
        "converted cycle end is anchored to Stripe's period (C2)",
        converted.billing_cycle_end,
        stripe_end,
    )
    rec.expect(
        "a paid monthly credit bucket exists after conversion",
        sub.monthly_bucket_count() >= 1,
        f"monthly buckets={sub.monthly_bucket_count()}",
    )
    return rec


def scenario_immediate_upgrade(harness: LiveQAHarness) -> CheckRecorder:
    """A same-interval upgrade must not move the billing anchor."""
    rec = CheckRecorder()
    standard = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    pro = require_plan(tier=PlanTier.PRO, interval=BillingInterval.MONTHLY)

    sub = _establish_subscriber(harness, rec, plan=standard, label="upgrade")
    harness.drain_events(customer_id=sub.customer_id)

    before = sub.local()
    if not rec.expect("local subscription exists before upgrade", before is not None):
        return rec
    anchor_before = before.billing_cycle_end

    stripe_sub = harness.retrieve_subscription(sub.stripe_subscription_id)
    items = (stripe_sub.get("items") or {}).get("data") or []
    if not rec.expect(
        "Stripe subscription exposes an item to swap",
        bool(items),
        f"items.data={items!r}",
    ):
        return rec

    StripeSubscriptionMutationService._apply_upgrade_directly(
        before, pro, items[0]["id"]
    )
    harness.drain_events(customer_id=sub.customer_id)

    after = sub.local()
    if not rec.expect("local subscription exists after upgrade", after is not None):
        return rec

    rec.expect_equal("local plan swapped to the upgraded plan", after.plan_id, pro.id)
    rec.expect_close(
        "billing anchor did NOT move on a same-interval upgrade",
        after.billing_cycle_end,
        anchor_before,
        tolerance_seconds=5,
    )

    refreshed = harness.retrieve_subscription(sub.stripe_subscription_id)
    refreshed_items = (refreshed.get("items") or {}).get("data") or []
    stripe_price = (
        (refreshed_items[0].get("price") or {}).get("id") if refreshed_items else None
    )
    rec.expect_equal(
        "Stripe subscription is now on the upgraded price",
        stripe_price,
        pro.stripe_price_id,
    )
    return rec


def scenario_deferred_downgrade(harness: LiveQAHarness) -> CheckRecorder:
    """A scheduled downgrade must apply exactly at the period boundary."""
    rec = CheckRecorder()
    standard = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    pro = require_plan(tier=PlanTier.PRO, interval=BillingInterval.MONTHLY)

    sub = _establish_subscriber(harness, rec, plan=pro, label="downgrade")
    harness.drain_events(customer_id=sub.customer_id)

    current = sub.local()
    if not rec.expect(
        "local subscription exists before scheduling", current is not None
    ):
        return rec

    schedule_id = StripeSubscriptionScheduleService.schedule_plan_change_on_stripe(
        current, standard
    )
    rec.expect(
        "Stripe returned a SubscriptionSchedule id",
        bool(schedule_id),
        f"schedule_id={schedule_id!r}",
    )
    SubscriptionService.schedule_plan_change(
        sub.user,
        standard,
        PendingChangeType.DOWNGRADE,
        "Live QA deferred downgrade.",
        stripe_schedule_id=schedule_id,
    )

    pending = sub.local()
    rec.expect_equal(
        "pending plan recorded locally before the boundary",
        pending.pending_plan_id,
        standard.id,
    )
    rec.expect_equal("plan is unchanged until the boundary", pending.plan_id, pro.id)

    _, period_end = _stripe_period_end(harness, sub.stripe_subscription_id)
    if not rec.expect(
        "Stripe period readable before advancing", period_end is not None
    ):
        return rec

    _advance_past_boundary(harness, sub, period_end)

    after = sub.local()
    if not rec.expect(
        "an active local subscription survives the downgrade boundary",
        after is not None,
    ):
        return rec

    rec.expect_equal("downgrade applied at the boundary", after.plan_id, standard.id)
    rec.expect(
        "pending plan cleared after it was applied",
        after.pending_plan_id is None,
        f"pending_plan_id={after.pending_plan_id!r}",
    )
    return rec


def scenario_failed_renewal(harness: LiveQAHarness) -> CheckRecorder:
    """A declined renewal must not renew the cycle or grant credits."""
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)

    # Start with a card that works, so the subscription reaches a normal
    # active state, then swap in one that fails on charge. That models a
    # card expiring mid-subscription — the common real-world case.
    sub = _establish_subscriber(harness, rec, plan=plan, label="dunning")
    harness.drain_events(customer_id=sub.customer_id)

    failing = harness.attach_card(
        customer_id=sub.customer_id, token=CARD_FAILS_ON_CHARGE, set_default=True
    )
    guarded_call(
        stripe.Subscription.modify,
        sub.stripe_subscription_id,
        default_payment_method=failing["id"],
    )

    before = sub.local()
    if not rec.expect(
        "local subscription exists before the failed renewal", before is not None
    ):
        return rec
    before_end = before.billing_cycle_end
    before_buckets = sub.monthly_bucket_count()

    _, period_end = _stripe_period_end(harness, sub.stripe_subscription_id)
    if not rec.expect(
        "Stripe period readable before advancing", period_end is not None
    ):
        return rec

    _advance_past_boundary(harness, sub, period_end)

    after = sub.local()
    if not rec.expect(
        "local subscription row still exists after a failed renewal",
        after is not None,
        "a failed payment must not delete the subscription",
    ):
        return rec

    rec.expect_close(
        "billing cycle did NOT advance on a failed payment",
        after.billing_cycle_end,
        before_end,
        tolerance_seconds=5,
    )
    rec.expect_equal(
        "no credits were granted for an unpaid renewal",
        sub.monthly_bucket_count(),
        before_buckets,
    )

    refreshed = harness.retrieve_subscription(sub.stripe_subscription_id)
    rec.expect(
        "Stripe moved the subscription out of active on non-payment",
        refreshed.get("status") in {"past_due", "unpaid", "incomplete", "canceled"},
        f"stripe status={refreshed.get('status')!r}",
    )
    rec.expect(
        "local stripe_status reflects the failure",
        after.stripe_status
        in {
            StripeSubscriptionStatus.PAST_DUE,
            StripeSubscriptionStatus.UNPAID,
            StripeSubscriptionStatus.CANCELED,
        },
        f"local stripe_status={after.stripe_status!r}",
    )
    return rec


SCENARIOS = {
    "renewals": scenario_renewals,
    "trial_conversion": scenario_trial_conversion,
    "immediate_upgrade": scenario_immediate_upgrade,
    "deferred_downgrade": scenario_deferred_downgrade,
    "failed_renewal": scenario_failed_renewal,
}


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def run_suite(scenario_names=None, *, keep_objects: bool = False) -> SuiteResult:
    """
    Run scenarios against real Stripe test mode and always clean up.

    One scenario's failure never stops the others: the point of a nightly
    run is a complete picture by morning, not the first thing that broke.
    """
    assert_live_qa_enabled()

    names = list(scenario_names) if scenario_names else list(SCENARIOS)
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        raise LiveQAConfigurationError(
            f"Unknown scenario(s): {', '.join(sorted(unknown))}. "
            f"Available: {', '.join(sorted(SCENARIOS))}."
        )

    harness = LiveQAHarness()
    result = SuiteResult(run_id=harness.run_id)
    logger.info("[LIVE QA %s] Starting scenarios: %s", harness.run_id, ", ".join(names))

    try:
        for name in names:
            started = time.monotonic()
            scenario_result = ScenarioResult(name=name)
            try:
                recorder = SCENARIOS[name](harness)
                scenario_result.checks = recorder.checks
                scenario_result.passed = recorder.passed
            except Exception as exc:  # noqa: BLE001 - one scenario must not end the run
                scenario_result.passed = False
                scenario_result.error = repr(exc)
                logger.exception(
                    "[LIVE QA %s] Scenario %s raised.", harness.run_id, name
                )
            scenario_result.duration_seconds = time.monotonic() - started
            result.scenarios.append(scenario_result)
    finally:
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

    return result
