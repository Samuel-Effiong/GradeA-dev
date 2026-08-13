"""
billing/live_qa/scenarios_license.py
=====================================
The license/school track: one Stripe subscription serving many teachers,
managed by a school admin rather than the teachers themselves.

WHY A SEPARATE HELPER RATHER THAN REUSING _establish_subscriber
-----------------------------------------------------------------
License creation goes through checkout too (needs a browser, same reason
the individual track bypasses it), but everything else about the shape
is different: a School + a SCHOOL_ADMIN user instead of a lone TEACHER,
per-seat quantity pricing instead of a flat price, and admin_user is
PROTECTed on LicenseSubscription — deleting the admin while a license
still references them raises, so cleanup here deletes the School FIRST
(which cascades the LicenseSubscription and its SchoolCreditAllocations)
and only then the admin/teacher users.

WHAT EACH SCENARIO IS ACTUALLY GUARDING
-----------------------------------------
  license_lifecycle_baseline    Create -> the admin gets their own
                                 analytics allocation for free -> one real
                                 Stripe renewal grants a fresh monthly
                                 cycle to every active allocation, not
                                 just the admin's.
  seat_quantity_proration       update_seats() is the one license
                                 mutation that talks to Stripe directly
                                 (real Subscription.modify + an
                                 always_invoice charge on increase, no
                                 charge on decrease) rather than going
                                 through a service class like the
                                 individual track's upgrade path.
  license_cancellation_and_offline_conversion
                                 Two distinct exits: cancel_license_
                                 subscription is LOCAL ONLY (auto_renew
                                 flips off, Stripe keeps billing until
                                 the sweep's non-auto-renew branch cancels
                                 it) versus convert_license_to_offline,
                                 which deletes the real Stripe
                                 subscription immediately and hands the
                                 school to process_offline_renewal — a
                                 manual, human-triggered renewal path
                                 that process_license_renewals explicitly
                                 excludes OFFLINE licenses from, so
                                 nothing here ever needs Stripe again.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from billing.imports import stripe
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    PlanCategory,
    PlanTier,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.stripe_live_qa import (
    CARD_OK,
    CheckRecorder,
    LiveQAConfigurationError,
    LiveQAHarness,
    guarded_call,
    qa_email_domain,
)
from billing.stripe_live_qa_scenarios import TIER_FAST, register_scenarios
from classrooms.models import School
from users.models import CustomUser, UserTypes

logger = logging.getLogger(__name__)

# License seat/proration behaviour is identical whether there are 3 seats
# or 300 — a small quantity keeps each real Stripe charge tiny without
# testing anything different.
DEFAULT_MAX_SEATS = 3


def _require_license_plan(*, tier=PlanTier.PRO) -> SubscriptionPlan:
    """Real, Stripe-wired LICENSE-category plan. Never creates one, for
    the same reason require_plan() in stripe_live_qa.py doesn't."""
    plan = (
        SubscriptionPlan.objects.filter(
            category=PlanCategory.LICENSE,
            tier=tier,
            is_active=True,
        )
        .exclude(stripe_price_id__isnull=True)
        .exclude(stripe_price_id="")
        .order_by("price_cents")
        .first()
    )
    if plan is None:
        raise LiveQAConfigurationError(
            f"No active LICENSE plan with tier={tier} and a stripe_price_id "
            "is configured. The license live-QA scenarios need one real "
            "priced license plan to run against."
        )
    return plan


@dataclass
class LicenseActor:
    """Bundles everything a license scenario needs, plus its own
    teardown — license cleanup has an ordering constraint
    (admin_user.on_delete=PROTECT) that the generic harness teardown
    does not know about, so each scenario owns its own cleanup rather
    than relying on harness.cleanup() for these objects."""

    school: School
    admin: CustomUser
    license_sub_id: object
    clock_id: str
    customer_id: str

    def refresh(self):
        from billing.models import LicenseSubscription

        return LicenseSubscription.objects.filter(pk=self.license_sub_id).first()

    def cleanup(self) -> None:
        # School cascades LicenseSubscription -> SchoolCreditAllocation.
        # Only after that is the admin no longer PROTECTed.
        School.objects.filter(id=self.school.id).delete()
        CustomUser.objects.filter(id=self.admin.id).delete()


def _establish_license(
    harness: LiveQAHarness,
    rec: CheckRecorder,
    *,
    plan: SubscriptionPlan,
    label: str,
    max_seats: int = DEFAULT_MAX_SEATS,
    contract_months: int = 12,
    card: str = CARD_OK,
) -> LicenseActor:
    """
    Mirrors StripeWebhookHandler._handle_license_create: a real Stripe
    subscription with quantity=max_seats, created directly (checkout
    needs a browser) and then the exact local activation the webhook
    would perform.
    """
    run_id = harness.run_id
    school = School.objects.create(name=f"Live QA School {run_id} {label}"[:255])
    admin = CustomUser.objects.create_user(
        email=f"liveqa-{run_id}-{label}-admin@{qa_email_domain()}",
        password=uuid.uuid4().hex,  # nosec B106 - random, never used to log in
        user_type=UserTypes.SCHOOL_ADMIN,
        school=school,
    )

    clock = harness.create_test_clock(f"license-{label}")
    customer = harness.create_customer(email=admin.email, clock_id=clock["id"])
    harness.attach_card(customer_id=customer["id"], token=card)
    stripe_sub = harness.create_subscription(
        customer_id=customer["id"],
        price_id=plan.stripe_price_id,
        items=[{"price": plan.stripe_price_id, "quantity": max_seats}],
    )
    rec.expect(
        "Stripe accepted the per-seat quantity on the license subscription",
        (stripe_sub.get("items") or {}).get("data", [{}])[0].get("quantity")
        == max_seats,
        f"items.data[0].quantity={(stripe_sub.get('items') or {}).get('data', [{}])[0].get('quantity')!r}",
    )

    license_sub = LicenseSubscriptionService.create_license_subscription(
        school=school,
        plan=plan,
        admin_user=admin,
        teacher_emails=None,
        contract_months=contract_months,
        max_seats=max_seats,
    )
    license_sub.stripe_subscription_id = stripe_sub["id"]
    license_sub.stripe_customer_id = customer["id"]
    from billing.models import StripeSubscriptionStatus

    license_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
    license_sub.save(
        update_fields=[
            "stripe_subscription_id",
            "stripe_customer_id",
            "stripe_status",
            "updated_at",
        ]
    )

    return LicenseActor(
        school=school,
        admin=admin,
        license_sub_id=license_sub.id,
        clock_id=clock["id"],
        customer_id=customer["id"],
    )


def scenario_license_lifecycle_baseline(harness) -> CheckRecorder:
    """Create a license, then renew it once for real and prove every
    active allocation (not just the admin's) gets refreshed."""
    rec = CheckRecorder()
    plan = _require_license_plan()
    actor = _establish_license(harness, rec, plan=plan, label="baseline")
    try:
        harness.drain_events(customer_id=actor.customer_id)

        license_sub = actor.refresh()
        if not rec.expect(
            "license subscription exists after creation", license_sub is not None
        ):
            return rec

        admin_allocation = SchoolCreditAllocation.objects.filter(
            license_subscription=license_sub,
            user=actor.admin,
            is_admin_allocation=True,
        ).first()
        rec.expect(
            "the admin received their own analytics allocation for free",
            admin_allocation is not None,
            f"admin_allocation={admin_allocation!r}",
        )

        before_allocations = list(
            SchoolCreditAllocation.objects.filter(
                license_subscription=license_sub, is_active=True
            )
        )
        before_grant_times = {a.id: a.next_credit_grant_at for a in before_allocations}

        from billing.stripe_service import extract_subscription_billing_period

        stripe_sub = harness.retrieve_subscription(license_sub.stripe_subscription_id)
        _, period_end = extract_subscription_billing_period(stripe_sub)
        if not rec.expect(
            "Stripe reports a period end to advance past", period_end is not None
        ):
            return rec

        harness.advance_clock_to(actor.clock_id, int(period_end.timestamp()) + 3600)
        harness.drain_events(customer_id=actor.customer_id)

        renewed = actor.refresh()
        if not rec.expect(
            "the license row survives the real Stripe renewal", renewed is not None
        ):
            return rec
        rec.expect(
            "the license billing cycle advanced",
            renewed.billing_cycle_end > license_sub.billing_cycle_end,
            f"before={license_sub.billing_cycle_end.isoformat()}, "
            f"after={renewed.billing_cycle_end.isoformat()}",
        )

        after_allocations = SchoolCreditAllocation.objects.filter(
            license_subscription=renewed, is_active=True
        )
        stale = [
            a
            for a in after_allocations
            if before_grant_times.get(a.id) == a.next_credit_grant_at
        ]
        rec.expect(
            "every active allocation refreshed on renewal, not only the " "admin's",
            not stale,
            f"{len(stale)} of {after_allocations.count()} allocation(s) show "
            f"no change in next_credit_grant_at after a real renewal",
        )
        return rec
    finally:
        actor.cleanup()


def scenario_seat_quantity_proration(harness) -> CheckRecorder:
    """update_seats talks to Stripe directly: a real charge on increase,
    no charge on decrease, and Stripe's quantity must match ours either
    way."""
    rec = CheckRecorder()
    plan = _require_license_plan()
    actor = _establish_license(
        harness, rec, plan=plan, label="seats", max_seats=DEFAULT_MAX_SEATS
    )
    try:
        harness.drain_events(customer_id=actor.customer_id)
        license_sub = actor.refresh()
        if not rec.expect(
            "license exists before a seat change", license_sub is not None
        ):
            return rec

        increased = LicenseSubscriptionService.update_seats(
            license_sub, DEFAULT_MAX_SEATS + 2
        )
        rec.expect_equal(
            "local max_seats reflects the increase",
            increased.max_seats,
            DEFAULT_MAX_SEATS + 2,
        )

        stripe_sub = harness.retrieve_subscription(license_sub.stripe_subscription_id)
        items = (stripe_sub.get("items") or {}).get("data") or []
        stripe_quantity = items[0].get("quantity") if items else None
        rec.expect_equal(
            "Stripe's real quantity matches the increase",
            stripe_quantity,
            DEFAULT_MAX_SEATS + 2,
        )

        from billing.models import BillingTransaction, BillingTransactionType

        charge = BillingTransaction.objects.filter(
            license_subscription_id=actor.license_sub_id,
            transaction_type=BillingTransactionType.LICENSE_SEAT_CHANGE_CHARGE,
        ).exists()
        rec.expect(
            "a real proration charge was recorded for the seat increase",
            charge,
        )

        decreased = LicenseSubscriptionService.update_seats(
            increased, DEFAULT_MAX_SEATS
        )
        rec.expect_equal(
            "local max_seats reflects the decrease",
            decreased.max_seats,
            DEFAULT_MAX_SEATS,
        )
        refreshed = harness.retrieve_subscription(license_sub.stripe_subscription_id)
        items = (refreshed.get("items") or {}).get("data") or []
        stripe_quantity = items[0].get("quantity") if items else None
        rec.expect_equal(
            "Stripe's real quantity matches the decrease too",
            stripe_quantity,
            DEFAULT_MAX_SEATS,
        )
        return rec
    finally:
        actor.cleanup()


def scenario_license_cancellation_and_offline_conversion(harness) -> CheckRecorder:
    """Two distinct exits from Stripe billing, checked back to back on
    separate licenses so neither's cleanup interferes with the other."""
    rec = CheckRecorder()
    plan = _require_license_plan()

    cancel_actor = _establish_license(harness, rec, plan=plan, label="cancel")
    try:
        harness.drain_events(customer_id=cancel_actor.customer_id)
        license_sub = cancel_actor.refresh()
        if not rec.expect(
            "license exists before cancellation", license_sub is not None
        ):
            return rec

        LicenseSubscriptionService.cancel_license_subscription(license_sub)
        cancelled = cancel_actor.refresh()
        rec.expect(
            "cancelling a STRIPE-billed license clears auto_renew but "
            "leaves is_active=True -- teachers keep access for the "
            "period they already paid for; deactivation happens later, "
            "via the real customer.subscription.deleted webhook",
            cancelled.is_active is True and cancelled.auto_renew is False,
            f"is_active={cancelled.is_active!r}, auto_renew={cancelled.auto_renew!r}",
        )
        stripe_sub = guarded_call(
            stripe.Subscription.retrieve, cancelled.stripe_subscription_id
        )
        rec.expect(
            "the real Stripe subscription is told to stop renewing "
            "(cancel_at_period_end) rather than being silently ignored",
            stripe_sub.get("cancel_at_period_end") is True,
            f"cancel_at_period_end={stripe_sub.get('cancel_at_period_end')!r}",
        )
    finally:
        cancel_actor.cleanup()

    offline_actor = _establish_license(harness, rec, plan=plan, label="offline")
    try:
        harness.drain_events(customer_id=offline_actor.customer_id)
        license_sub = offline_actor.refresh()
        if not rec.expect(
            "license exists before offline conversion", license_sub is not None
        ):
            return rec

        stripe_sub_id = license_sub.stripe_subscription_id
        converted = LicenseSubscriptionService.convert_license_to_offline(
            license_sub, performed_by=offline_actor.admin, notes="Live QA conversion"
        )
        rec.expect_equal(
            "billing_method flipped to OFFLINE",
            converted.billing_method,
            LicenseBillingMethod.OFFLINE,
        )
        rec.expect(
            "the local Stripe subscription reference was cleared",
            converted.stripe_subscription_id is None,
            f"stripe_subscription_id={converted.stripe_subscription_id!r}",
        )

        try:
            guarded_call(stripe.Subscription.retrieve, stripe_sub_id)
            stripe_gone = False
        except stripe.error.InvalidRequestError:
            stripe_gone = True
        rec.expect(
            "the real Stripe subscription was actually deleted, not just "
            "detached locally",
            stripe_gone,
        )

        before_end = converted.billing_cycle_end
        new_end = timezone.now() + relativedelta(months=1)
        renewed = LicenseSubscriptionService.process_offline_renewal(
            converted,
            performed_by=offline_actor.admin,
            new_billing_cycle_end=new_end,
            amount_paid_cents=plan.price_cents * DEFAULT_MAX_SEATS,
            payment_reference="live-qa-offline-payment",
        )
        rec.expect(
            "process_offline_renewal advanced the cycle with no Stripe "
            "involvement at all",
            renewed.billing_cycle_end > before_end,
            f"before={before_end.isoformat()}, after="
            f"{renewed.billing_cycle_end.isoformat()}",
        )

        from billing.tasks import process_license_renewals

        summary = process_license_renewals()
        logger.info(
            "[LIVE QA %s] process_license_renewals summary after offline "
            "conversion: %s",
            harness.run_id,
            summary,
        )
        untouched = offline_actor.refresh()
        rec.expect_equal(
            "the automated Stripe-renewal sweep leaves an OFFLINE license's "
            "cycle end exactly alone",
            untouched.billing_cycle_end,
            renewed.billing_cycle_end,
        )
    finally:
        offline_actor.cleanup()

    return rec


register_scenarios(
    {
        "license_lifecycle_baseline": scenario_license_lifecycle_baseline,
        "seat_quantity_proration": scenario_seat_quantity_proration,
        "license_cancellation_and_offline_conversion": (
            scenario_license_cancellation_and_offline_conversion
        ),
    },
    tier=TIER_FAST,
)
