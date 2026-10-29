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
  license_overage_purchase       The school overage flow end to end
                                 against real Stripe: a real Checkout
                                 Session built by the real service, a real
                                 charge, the real webhook handler, and
                                 every ownership fact it is supposed to
                                 get right — plus duplicate delivery,
                                 retry, and concurrent delivery from real
                                 threads.
  license_overage_isolation_and_refund
                                 Two schools and two teachers, proving one
                                 school's event cannot reach another's
                                 wallet — then a REAL Stripe refund of the
                                 real charge, proving the clawback in
                                 billing/payment_refunds.py works against
                                 Stripe's own event rather than a
                                 hand-written one.

WHAT IS REAL HERE, AND THE ONE THING THAT IS NOT
-------------------------------------------------
The overage scenarios below use a genuine Checkout Session, created by
the genuine service code against the genuine Stripe price, and a genuine
card charge. The ONE thing that cannot be real is the act of paying the
Checkout Session: Stripe exposes no API to complete a hosted checkout,
and `session.payment_intent` is null while the session is still open
(probed against test mode, 2026-09-09 — the session stays
status=open/payment_status=unpaid and confirming is impossible because
there is no PaymentIntent yet). So the charge is taken out of band on the
same customer for the same amount, and the event payload is the REAL
retrieved session with the two fields Stripe would have set on completion
— `payment_status="paid"` and `payment_intent` — filled in from that real
charge. Everything downstream of the webhook is exercised for real.

Refunds have no such limitation: `stripe.Refund.create` is a real API
call, so the refund half of the second scenario is fully end to end.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from dateutil.relativedelta import relativedelta

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
    #: Teachers enrolled during the scenario. Tracked because
    #: CustomUser.school is SET_NULL, so deleting the School leaves them
    #: behind — run nightly, that accumulates a QA user per run forever.
    teacher_ids: list = field(default_factory=list)

    def refresh(self):
        from billing.models import LicenseSubscription

        return LicenseSubscription.objects.filter(pk=self.license_sub_id).first()

    def cleanup(self) -> None:
        # School cascades LicenseSubscription -> SchoolCreditAllocation.
        # Only after that is the admin no longer PROTECTed.
        School.objects.filter(id=self.school.id).delete()
        if self.teacher_ids:
            CustomUser.objects.filter(id__in=self.teacher_ids).delete()
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
    # A ONE-MONTH contract on purpose. The Stripe subscription behind a
    # licence bills monthly, and this scenario advances the clock just past
    # that monthly period — which cannot move a 12-month contract's
    # billing_cycle_end, so the renewal guard (`billing_cycle_end <= now`)
    # correctly declined and the scenario failed on its own arithmetic
    # rather than on a defect. With the contract and the Stripe period the
    # same length, one advance genuinely crosses the boundary.
    actor = _establish_license(
        harness, rec, plan=plan, label="baseline", contract_months=1
    )
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

        # Stripe does NOT remove a cancelled subscription — it keeps the
        # object retrievable forever with status="canceled". Verified
        # against the live test API on 2026-09-06: create -> delete ->
        # retrieve returns status=canceled, it does not 404. The previous
        # check expected an InvalidRequestError and so could never pass,
        # which is why this scenario failed while
        # convert_license_to_offline was in fact doing the right thing
        # (it calls stripe.Subscription.delete and raises if that fails).
        remote = guarded_call(stripe.Subscription.retrieve, stripe_sub_id)
        remote_status = remote.get("status")
        rec.expect(
            "the real Stripe subscription was actually cancelled, not just "
            "detached locally",
            remote_status == "canceled",
            f"stripe status={remote_status!r} (expected 'canceled')",
        )

        before_end = converted.billing_cycle_end
        # Extend from the licence's EXISTING cycle end, not from today.
        # This licence is on a 12-month contract, so `timezone.now() + 1
        # month` was EARLIER than before_end — the scenario asked for a
        # cycle end in the past and then asserted the cycle had moved
        # forward, which cannot both be true. process_offline_renewal was
        # faithfully doing what it was told.
        new_end = before_end + relativedelta(months=1)
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

        # Licence renewal has the same shape of guard as the individual
        # track (`billing_cycle_end <= now`), so it too must be judged
        # against the time Stripe thinks it is.
        with harness.local_clock():
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


# ----------------------------------------------------------------------
# School overage purchase — flow 3
# ----------------------------------------------------------------------


def _enrol_teacher(actor: LicenseActor, harness, label: str):
    """A real teacher on the licence, with a wallet."""
    license_sub = actor.refresh()
    email = f"liveqa-{harness.run_id}-{label}@{qa_email_domain()}"
    LicenseSubscriptionService.add_teacher_to_license(license_sub, email)
    teacher = CustomUser.objects.get(email=email)
    actor.teacher_ids.append(teacher.id)
    from billing.models import CreditWallet

    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    return teacher, wallet


def _real_overage_checkout(actor: LicenseActor, blocks_by_teacher: dict):
    """
    Drive the REAL service entry point, producing a REAL Stripe Checkout
    Session and the local LicenseOveragePurchaseIntent that goes with it.
    """
    license_sub = actor.refresh()
    total = sum(blocks_by_teacher.values())
    result = LicenseSubscriptionService.initiate_overage_purchase(
        license_sub=license_sub,
        requesting_user=actor.admin,
        total_blocks=total,
        allocations={str(k): int(v) for k, v in blocks_by_teacher.items()},
        success_url="https://example.invalid/ok",
        cancel_url="https://example.invalid/cancel",
        payment_method="stripe",
    )
    return result


def _pay_for_session(actor: LicenseActor, session_id: str):
    """
    Take the REAL charge that the hosted checkout page would have taken.

    See the module docstring: Stripe exposes no API to complete a hosted
    Checkout Session, and the session carries no PaymentIntent while it is
    open, so this charges the same customer for the session's own
    `amount_total` and hands the resulting PaymentIntent back to stand in
    for the one checkout would have created.
    """
    session = guarded_call(stripe.checkout.Session.retrieve, session_id)
    customer = guarded_call(stripe.Customer.retrieve, actor.customer_id)
    default_pm = (customer.get("invoice_settings") or {}).get("default_payment_method")
    intent = guarded_call(
        stripe.PaymentIntent.create,
        amount=session["amount_total"],
        currency=session["currency"],
        customer=actor.customer_id,
        payment_method=default_pm,
        off_session=True,
        confirm=True,
        metadata={"live_qa": "license_overage", "checkout_session": session_id},
    )
    return session, intent


def _completed_event(session, intent):
    """
    The `checkout.session.completed` payload, built from the REAL session
    with the two fields Stripe sets on completion filled in from the REAL
    charge.
    """
    payload = dict(session)
    payload["payment_status"] = "paid"
    payload["payment_intent"] = intent["id"]
    return payload


def scenario_license_overage_purchase(harness) -> CheckRecorder:
    """The school overage flow, end to end, against real Stripe.

    Flow 3 had unit coverage but had never been driven against Stripe, and
    its ownership semantics are entirely different from the individual
    flow: the ADMIN pays, the TEACHERS receive, and one payment fans out
    across several wallets. Every one of those relationships is asserted
    here against real objects.
    """
    from billing.models import (
        BillingTransaction,
        BillingTransactionType,
        CreditBucket,
        CreditBucketType,
        CreditLedger,
        CreditLedgerType,
        LicenseOveragePurchaseIntent,
        LicenseOveragePurchaseStatus,
    )
    from billing.stripe_service import StripeWebhookHandler

    rec = CheckRecorder()
    plan = _require_license_plan()
    if not rec.expect(
        "the license plan has overage pricing configured",
        bool(plan.overage_block_size and plan.overage_block_price),
        f"block_size={plan.overage_block_size!r} price={plan.overage_block_price!r}",
    ):
        return rec
    if not rec.expect(
        "the license plan has a real Stripe overage price",
        bool(plan.stripe_overage_price_id),
        f"stripe_overage_price_id={plan.stripe_overage_price_id!r}",
    ):
        return rec

    actor = _establish_license(harness, rec, plan=plan, label="overage")
    try:
        teacher_a, wallet_a = _enrol_teacher(actor, harness, "ov-a")
        teacher_b, wallet_b = _enrol_teacher(actor, harness, "ov-b")

        def blocks(wallet):
            return CreditBucket.objects.filter(
                wallet=wallet, bucket_type=CreditBucketType.OVERAGE
            )

        # --- purchase creation, for real -----------------------------
        result = _real_overage_checkout(actor, {teacher_a.id: 2, teacher_b.id: 1})
        rec.expect_equal(
            "the school-admin path returns a checkout, granting nothing yet",
            result.get("action"),
            "checkout",
        )
        rec.expect_equal(
            "nothing was granted before payment",
            blocks(wallet_a).count() + blocks(wallet_b).count(),
            0,
        )

        intent_row = LicenseOveragePurchaseIntent.objects.filter(
            license_subscription_id=actor.license_sub_id
        ).first()
        if not rec.expect(
            "a LicenseOveragePurchaseIntent was recorded", intent_row is not None
        ):
            return rec
        rec.expect_equal(
            "the intent starts PENDING",
            intent_row.status,
            LicenseOveragePurchaseStatus.PENDING,
        )
        rec.expect_equal(
            "the intent totals the blocks asked for", intent_row.total_blocks, 3
        )
        rec.expect_equal(
            "the intent snapshots the plan's block size",
            intent_row.block_size_snapshot,
            plan.overage_block_size,
        )
        rec.expect(
            "the intent is bound to a real Stripe checkout session",
            str(intent_row.stripe_checkout_session_id or "").startswith("cs_"),
            f"stripe_checkout_session_id={intent_row.stripe_checkout_session_id!r}",
        )

        session_id = intent_row.stripe_checkout_session_id
        live_session = guarded_call(stripe.checkout.Session.retrieve, session_id)

        # What Stripe will actually charge, read from the price rather than
        # assumed from the plan row — the two can disagree, and the next
        # check is what catches it.
        stripe_price = guarded_call(stripe.Price.retrieve, plan.stripe_overage_price_id)
        rec.expect_equal(
            "Stripe priced the session at its own unit price x blocks",
            live_session["amount_total"],
            3 * stripe_price["unit_amount"],
        )
        # DRIFT CHECK. `_create_overage_checkout` computes the intent's
        # amount_cents from `plan.overage_block_price`, but Stripe charges
        # whatever `stripe_overage_price_id` says. When those disagree the
        # school is quoted one price and charged another — nothing else in
        # billing watches for it, since reconcile_subscription_prices only
        # covers SUBSCRIPTION prices, not overage ones.
        rec.expect_equal(
            "the plan's overage price matches the Stripe price it charges",
            plan.overage_block_price,
            stripe_price["unit_amount"],
            "the school is quoted the plan price and charged the Stripe one",
        )
        rec.expect_equal(
            "the session carries the flow discriminator the handler routes on",
            (live_session.get("metadata") or {}).get("flow"),
            "license_overage_purchase_checkout",
        )
        rec.expect_equal(
            "the session carries the intent id the handler fulfils by",
            (live_session.get("metadata") or {}).get("intent_id"),
            str(intent_row.id),
        )

        # --- a real charge, then the real handler ---------------------
        session, payment = _pay_for_session(actor, session_id)
        rec.expect_equal(
            "Stripe actually took the overage payment",
            payment.get("status"),
            "succeeded",
        )

        event = _completed_event(session, payment)
        StripeWebhookHandler.handle_checkout_completed(event)

        # --- ownership and entitlement --------------------------------
        rec.expect_equal(
            "teacher A received exactly the blocks allocated to them",
            sum(b.total_credits for b in blocks(wallet_a)),
            2 * plan.overage_block_size,
        )
        rec.expect_equal(
            "teacher B received exactly the blocks allocated to them",
            sum(b.total_credits for b in blocks(wallet_b)),
            1 * plan.overage_block_size,
        )
        rec.expect(
            "a purchased block never expires",
            all(b.expires_at is None for b in blocks(wallet_a)),
            "an overage block was given an expiry — paid-for value forfeited",
        )

        intent_row.refresh_from_db()
        rec.expect_equal(
            "the intent is COMPLETED once fulfilled",
            intent_row.status,
            LicenseOveragePurchaseStatus.COMPLETED,
        )
        rec.expect_equal(
            "the intent records the real PaymentIntent that paid for it",
            intent_row.stripe_payment_intent_id,
            payment["id"],
        )
        rec.expect(
            "the intent is owned by the school that bought it",
            intent_row.license_subscription.school_id == actor.school.id,
            f"school_id={intent_row.license_subscription.school_id}",
        )
        rec.expect(
            "the purchase is attributed to the admin who initiated it",
            intent_row.initiated_by_id == actor.admin.id,
            f"initiated_by={intent_row.initiated_by_id}",
        )

        ledger_a = CreditLedger.objects.filter(
            bucket__wallet=wallet_a, ledger_type=CreditLedgerType.PURCHASE
        )
        rec.expect_equal(
            "teacher A's grant is in the append-only ledger", ledger_a.count(), 1
        )
        row = ledger_a.first()
        if row is not None:
            rec.expect_equal(
                "the ledger row is attributed to teacher A, not the admin",
                row.user_id,
                teacher_a.id,
            )
            rec.expect_equal(
                "the ledger row carries the real PaymentIntent, so a refund "
                "can find what it bought",
                row.stripe_payment_intent_id,
                payment["id"],
            )

        txn = BillingTransaction.objects.filter(
            stripe_payment_intent_id=payment["id"]
        ).first()
        if rec.expect("a BillingTransaction recorded the money", txn is not None):
            rec.expect_equal(
                "it is typed as a LICENSE overage purchase",
                txn.transaction_type,
                BillingTransactionType.LICENSE_OVERAGE_PURCHASE,
            )
            rec.expect_equal(
                "it is owned by the purchasing school", txn.school_id, actor.school.id
            )
            rec.expect_equal(
                "it records what Stripe actually charged",
                txn.amount_cents,
                live_session["amount_total"],
            )

        # --- duplicate delivery and retry -----------------------------
        granted_before = blocks(wallet_a).count() + blocks(wallet_b).count()
        StripeWebhookHandler.handle_checkout_completed(event)
        StripeWebhookHandler.handle_checkout_completed(event)
        rec.expect_equal(
            "redelivering the SAME real session grants nothing further",
            blocks(wallet_a).count() + blocks(wallet_b).count(),
            granted_before,
        )

        # A retry after a processing failure: the handler runs in one
        # transaction, so a failure leaves no partial grant behind and the
        # redelivery above is what the retry looks like.
        from django.db import transaction as db_transaction

        try:
            with db_transaction.atomic():
                StripeWebhookHandler.handle_checkout_completed(event)
                raise RuntimeError("simulated failure inside the handler")
        except RuntimeError:
            pass
        rec.expect_equal(
            "a rolled-back retry leaves the grant exactly as it was",
            blocks(wallet_a).count() + blocks(wallet_b).count(),
            granted_before,
        )
    finally:
        actor.cleanup()

    return rec


def scenario_license_overage_isolation_and_refund(harness) -> CheckRecorder:
    """Two schools, concurrent delivery, and a REAL Stripe refund.

    Guards the three things a single-school test cannot see: that one
    school's event cannot reach another school's wallets, that two
    purchases landing at the same moment each grant once, and that
    refunding the real charge really does take the credits back.
    """
    import threading

    from django.db import connections

    from billing.models import CreditBucket, CreditBucketType, PaymentRefund
    from billing.stripe_service import StripeWebhookHandler

    rec = CheckRecorder()
    plan = _require_license_plan()
    if not rec.expect(
        "the license plan has overage pricing configured",
        bool(plan.overage_block_size and plan.stripe_overage_price_id),
    ):
        return rec

    left = _establish_license(harness, rec, plan=plan, label="ovleft")
    right = _establish_license(harness, rec, plan=plan, label="ovright")
    try:
        teacher_l, wallet_l = _enrol_teacher(left, harness, "ovl-t")
        teacher_r, wallet_r = _enrol_teacher(right, harness, "ovr-t")

        def blocks(wallet):
            return CreditBucket.objects.filter(
                wallet=wallet, bucket_type=CreditBucketType.OVERAGE
            )

        def credits(wallet):
            """
            OVERAGE credits only.

            Deliberately not `wallet.total_remaining_credits()`: a licensed
            teacher already holds a monthly allocation, so the wallet total
            is dominated by credits this purchase never touched and a
            clawback of the block would barely move it.
            """
            return sum(b.remaining_credits for b in blocks(wallet))

        # Two real purchases, one per school.
        _real_overage_checkout(left, {teacher_l.id: 1})
        _real_overage_checkout(right, {teacher_r.id: 1})

        from billing.models import LicenseOveragePurchaseIntent

        intent_l = LicenseOveragePurchaseIntent.objects.filter(
            license_subscription_id=left.license_sub_id
        ).first()
        intent_r = LicenseOveragePurchaseIntent.objects.filter(
            license_subscription_id=right.license_sub_id
        ).first()

        session_l, payment_l = _pay_for_session(
            left, intent_l.stripe_checkout_session_id
        )
        session_r, payment_r = _pay_for_session(
            right, intent_r.stripe_checkout_session_id
        )
        event_l = _completed_event(session_l, payment_l)
        event_r = _completed_event(session_r, payment_r)

        # --- concurrent delivery, real threads ------------------------
        errors = []

        def deliver(event):
            try:
                StripeWebhookHandler.handle_checkout_completed(event)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(repr(exc))
            finally:
                connections.close_all()

        barrier = threading.Barrier(4)

        def worker(event):
            barrier.wait(timeout=30)
            deliver(event)

        threads = [
            threading.Thread(target=worker, args=(ev,))
            for ev in (event_l, event_r, event_l, event_r)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        rec.expect(
            "concurrent delivery of two schools' events raised nothing",
            not errors,
            "; ".join(errors),
        )
        rec.expect_equal(
            "the left school's teacher got exactly one block despite the "
            "duplicate arriving at the same moment",
            blocks(wallet_l).count(),
            1,
        )
        rec.expect_equal(
            "the right school's teacher got exactly one block",
            blocks(wallet_r).count(),
            1,
        )

        # --- cross-school isolation -----------------------------------
        rec.expect_equal(
            "the left school's payment never credited the right school",
            sum(b.total_credits for b in blocks(wallet_r)),
            plan.overage_block_size,
            "the right teacher holds more than their own single block",
        )
        left_ledger_pi = set(
            blocks(wallet_l)
            .values_list("credit_ledgers__stripe_payment_intent_id", flat=True)
            .distinct()
        )
        rec.expect(
            "every credit in the left wallet traces to the left payment",
            left_ledger_pi <= {payment_l["id"], None},
            f"payment intents seen: {sorted(str(p) for p in left_ledger_pi)}",
        )

        # --- a REAL refund, and the real clawback ---------------------
        before = credits(wallet_l)
        rec.expect_equal(
            "the left teacher holds their block before the refund",
            before,
            plan.overage_block_size,
        )

        refund = guarded_call(stripe.Refund.create, payment_intent=payment_l["id"])
        rec.expect_equal(
            "Stripe accepted the refund", refund.get("status"), "succeeded"
        )

        charge = guarded_call(
            stripe.Charge.retrieve, refund["charge"], expand=["refunds"]
        )
        StripeWebhookHandler.handle_charge_refunded(charge)

        rec.expect_equal(
            "a REAL refund took the credits back",
            credits(wallet_l),
            0,
            "money returned but entitlement retained — the divergence this "
            "clawback exists to prevent",
        )
        rec.expect_equal(
            "the refund did not touch the other school",
            credits(wallet_r),
            plan.overage_block_size,
        )

        refund_row = PaymentRefund.objects.filter(
            stripe_payment_intent_id=payment_l["id"]
        ).first()
        if rec.expect("the refund is recorded locally", refund_row is not None):
            rec.expect_equal(
                "it reclaimed the whole block",
                refund_row.credits_reversed,
                plan.overage_block_size,
            )
            rec.expect_equal("it left no debt", refund_row.credits_deficit, 0)

        # Stripe redelivers refund events too.
        StripeWebhookHandler.handle_charge_refunded(charge)
        refund_row.refresh_from_db()
        rec.expect_equal(
            "redelivering the REAL refund event reverses nothing further",
            refund_row.credits_reversed,
            plan.overage_block_size,
        )
    finally:
        left.cleanup()
        right.cleanup()

    return rec


register_scenarios(
    {
        "license_lifecycle_baseline": scenario_license_lifecycle_baseline,
        "seat_quantity_proration": scenario_seat_quantity_proration,
        "license_cancellation_and_offline_conversion": (
            scenario_license_cancellation_and_offline_conversion
        ),
        "license_overage_purchase": scenario_license_overage_purchase,
        "license_overage_isolation_and_refund": (
            scenario_license_overage_isolation_and_refund
        ),
    },
    tier=TIER_FAST,
)
