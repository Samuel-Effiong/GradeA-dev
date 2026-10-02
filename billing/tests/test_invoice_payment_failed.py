"""
billing/tests/test_invoice_payment_failed.py
==============================================
Coverage for StripeWebhookHandler.handle_invoice_payment_failed
(billing/stripe_service.py) — previously untested end to end, despite
being the only thing standing between a declined renewal and a
subscription that silently keeps behaving as if it were paid.

The handler is `@transaction.atomic` and has three jobs:
  1. Record the failed charge as a BillingTransaction (money always gets
     an audit row, even when it didn't succeed).
  2. For a TRIAL subscription — expire the trial (card declined at trial
     end means no conversion to paid).
  3. For a PAID subscription — flip stripe_status to PAST_DUE, leaving
     the subscription otherwise intact (past-due is not cancellation).

Scope note: these tests focus on the INDIVIDUAL subscription track. The
LicenseInvoicePaymentFailedTests cases are included only to pin the
individual-vs-license routing at the top of the handler; the license
track's own payment-failure semantics are otherwise still uncovered.

Reachability note for the trial cases: as the code stands, no live path
produces a row that is both is_trial=True and carries a
stripe_subscription_id — trials are local-only, and
finalize_trial_to_paid_conversion clears is_trial in the same save that
attaches the Stripe id. The one function that WOULD produce that state,
_handle_individual_trial, has been deleted (its sole builder,
create_individual_trial_session, had no callers). The trial cases below
guard handle_invoice_payment_failed's own defensive is_trial branch,
which is deliberately kept as belt-and-braces — see its comment in
stripe_service.py — in case Stripe-native trials (trial_period_days)
are adopted later, where the declined-card invoice would land exactly
at trial_end.

All Stripe API calls are mocked by patching attributes on the real
`stripe` module, leaving `stripe.error.*` as the real exception classes
(see test_subscription_upgrade.py's module docstring for why).
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    BillingTransaction,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.stripe_service import StripeWebhookHandler
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

STRIPE_SUB_ID = "sub_payfail_1"


def make_plan(
    name,
    tier,
    price_cents,
    monthly_credits,
    stripe_price_id,
    category=PlanCategory.INDIVIDUAL,
    interval=BillingInterval.MONTHLY,
):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=category,
        tier=tier,
        interval=interval,
        price_cents=price_cents,
        monthly_credits=monthly_credits,
        stripe_price_id=stripe_price_id,
        is_active=True,
    )


def invoice_payload(
    billing_reason="subscription_cycle",
    *,
    invoice_id="in_payfail_1",
    subscription_id=STRIPE_SUB_ID,
    amount_due=2999,
    currency="usd",
    use_parent_shape=False,
):
    """
    Builds an invoice.payment_failed event payload.

    `use_parent_shape` emits the post-2025-03-31 ("basil") Stripe layout
    where the subscription id lives at
    parent.subscription_details.subscription instead of at the top level.
    """
    payload = {
        "id": invoice_id,
        "billing_reason": billing_reason,
        "amount_due": amount_due,
        "currency": currency,
    }
    if subscription_id is None:
        return payload
    if use_parent_shape:
        payload["parent"] = {"subscription_details": {"subscription": subscription_id}}
    else:
        payload["subscription"] = subscription_id
    return payload


class PaidSubscriptionPaymentFailedTests(TestCase):
    """
    A declined renewal (or upgrade proration) on a normal paid
    individual subscription.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="payfail-paid@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(
            "PRO", PlanTier.PRO, 2999, 30_000_000, "price_payfail_pro"
        )
        CreditWallet.objects.get_or_create(user=self.user)
        now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            auto_renew=True,
            is_trial=False,
            billing_cycle_start=now - relativedelta(months=1),
            billing_cycle_end=now - timedelta(minutes=5),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    def test_marks_subscription_past_due(self):
        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.stripe_status, StripeSubscriptionStatus.PAST_DUE)

    def test_does_not_deactivate_or_stop_renewal(self):
        """
        PAST_DUE is not cancellation. Stripe keeps retrying the invoice,
        so the local row must stay active and still set to renew —
        otherwise a recovered payment would have nothing to renew onto.
        """
        original_cycle_end = self.sub.billing_cycle_end

        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_active)
        self.assertTrue(self.sub.auto_renew)
        self.assertFalse(self.sub.is_trial)
        self.assertEqual(self.sub.plan_id, self.plan.id)
        self.assertEqual(self.sub.billing_cycle_end, original_cycle_end)

    def test_records_failed_billing_transaction(self):
        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(txn.status, BillingTransactionStatus.FAILED)
        self.assertEqual(txn.source, BillingTransactionSource.INDIVIDUAL)
        self.assertEqual(txn.amount_cents, 2999)
        self.assertEqual(txn.currency, "usd")
        self.assertEqual(txn.user_id, self.user.id)
        self.assertEqual(txn.user_subscription_id, self.sub.id)
        self.assertEqual(txn.stripe_subscription_id, STRIPE_SUB_ID)

    def test_renewal_failure_uses_subscription_charge_type(self):
        StripeWebhookHandler.handle_invoice_payment_failed(
            invoice_payload("subscription_cycle")
        )

        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(
            txn.transaction_type,
            BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE,
        )

    def test_upgrade_proration_failure_uses_upgrade_charge_type(self):
        """
        billing_reason="subscription_update" is Stripe's marker for an
        immediate-upgrade proration invoice, which must be attributed as
        an upgrade charge rather than a routine subscription charge —
        otherwise the invoices view mislabels it for the user.
        """
        StripeWebhookHandler.handle_invoice_payment_failed(
            invoice_payload("subscription_update")
        )

        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(
            txn.transaction_type,
            BillingTransactionType.INDIVIDUAL_UPGRADE_CHARGE,
        )

    def test_redelivery_does_not_duplicate_the_transaction(self):
        """
        Stripe retries a failed webhook for ~3 days. The BillingTransaction
        upsert is keyed on stripe_invoice_id, so redelivery must update
        the existing row rather than adding a second one — a duplicate
        here would double-count in any revenue/failure reporting.
        """
        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())
        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        self.assertEqual(
            BillingTransaction.objects.filter(stripe_invoice_id="in_payfail_1").count(),
            1,
        )

    def test_missing_amount_and_currency_fall_back_to_defaults(self):
        payload = invoice_payload()
        del payload["amount_due"]
        del payload["currency"]

        StripeWebhookHandler.handle_invoice_payment_failed(payload)

        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(txn.amount_cents, 0)
        self.assertEqual(txn.currency, "usd")

    def test_null_amount_due_is_treated_as_zero(self):
        StripeWebhookHandler.handle_invoice_payment_failed(
            invoice_payload(amount_due=None)
        )

        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(txn.amount_cents, 0)

    def test_resolves_subscription_from_new_api_parent_shape(self):
        """
        Stripe moved invoice.subscription to
        invoice.parent.subscription_details.subscription in API version
        2025-03-31. If only the legacy location were read, every payment
        failure on a newer API version would silently no-op — no PAST_DUE,
        no audit row, nothing.
        """
        StripeWebhookHandler.handle_invoice_payment_failed(
            invoice_payload(use_parent_shape=True)
        )

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.stripe_status, StripeSubscriptionStatus.PAST_DUE)
        self.assertTrue(
            BillingTransaction.objects.filter(stripe_invoice_id="in_payfail_1").exists()
        )

    def test_invoice_with_no_subscription_is_a_noop(self):
        StripeWebhookHandler.handle_invoice_payment_failed(
            invoice_payload(subscription_id=None)
        )

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.stripe_status, StripeSubscriptionStatus.ACTIVE)
        self.assertEqual(BillingTransaction.objects.count(), 0)

    def test_unknown_subscription_id_is_a_noop(self):
        StripeWebhookHandler.handle_invoice_payment_failed(
            invoice_payload(subscription_id="sub_does_not_exist")
        )

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.stripe_status, StripeSubscriptionStatus.ACTIVE)
        self.assertEqual(BillingTransaction.objects.count(), 0)

    def test_matches_an_inactive_row_too(self):
        """
        The lookup filters on stripe_subscription_id only — NOT on
        is_active. A late-arriving failure for an already-superseded row
        still records the money against it rather than vanishing.
        """
        self.sub.is_active = False
        self.sub.save(update_fields=["is_active"])

        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.stripe_status, StripeSubscriptionStatus.PAST_DUE)
        self.assertTrue(
            BillingTransaction.objects.filter(stripe_invoice_id="in_payfail_1").exists()
        )


class TrialPaymentFailedTests(TestCase):
    """
    A card declined at the end of a free trial. The trial must be
    expired (no conversion to paid), its unused credits forfeited.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="payfail-trial@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(
            "STANDARD", PlanTier.STANDARD, 999, 10_000_000, "price_payfail_std"
        )
        self.trial_sub = SubscriptionService.activate_free_trial(self.user, self.plan)
        self.trial_sub.stripe_subscription_id = STRIPE_SUB_ID
        self.trial_sub.save(update_fields=["stripe_subscription_id"])

    def _force_trial_ended(self):
        past = timezone.now() - timedelta(days=1)
        self.trial_sub.trial_end = past
        self.trial_sub.billing_cycle_end = past
        self.trial_sub.save(update_fields=["trial_end", "billing_cycle_end"])

    def test_ended_trial_is_expired_not_marked_past_due(self):
        self._force_trial_ended()

        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        self.trial_sub.refresh_from_db()
        self.assertFalse(self.trial_sub.is_active)
        self.assertFalse(self.trial_sub.is_trial)
        # expire_trial owns this path — the PAST_DUE branch must NOT run.
        self.assertNotEqual(
            self.trial_sub.stripe_status, StripeSubscriptionStatus.PAST_DUE
        )

    def test_ended_trial_forfeits_unused_credits(self):
        self._force_trial_ended()

        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)
        self.assertTrue(bucket.is_processed)
        self.assertTrue(
            CreditLedger.objects.filter(
                user_id=self.user.id,
                bucket=bucket,
                ledger_type=CreditLedgerType.EXPIRE,
            ).exists()
        )

    def test_ended_trial_still_records_the_failed_transaction(self):
        self._force_trial_ended()

        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(txn.status, BillingTransactionStatus.FAILED)
        self.assertEqual(txn.user_id, self.user.id)

    def test_trial_with_no_trial_end_expires_without_raising(self):
        """
        expire_trial's "has the trial ended?" guard is written as
        `trial_end and trial_end > now`, so a trial row with a null
        trial_end short-circuits past it. Locked in because the guard's
        null-handling is easy to tighten by accident into a raise.
        """
        self.trial_sub.trial_end = None
        self.trial_sub.save(update_fields=["trial_end"])

        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        self.trial_sub.refresh_from_db()
        self.assertFalse(self.trial_sub.is_active)
        self.assertFalse(self.trial_sub.is_trial)


class TrialPaymentFailureBeforeTrialEndTests(TestCase):
    """
    Regression cover for the webhook retry storm.

    handle_invoice_payment_failed used to call expire_trial() with the
    default force=False, which RAISES while trial_end is still in the
    future. Because the handler is @transaction.atomic and
    billing/webhooks.py:_record_and_dispatch turns any handler exception
    into HTTP 500, a payment failure arriving even slightly early meant:

      - the BillingTransaction audit row rolled back (no record of the
        failed charge at all),
      - HTTP 500 to Stripe,
      - Stripe retrying with backoff for ~3 days,
      - every retry failing IDENTICALLY, because trial_end never moves.

    Clock skew between Stripe and this app, or ordinary webhook latency,
    is enough to land in that window. Stripe telling us the card was
    declined is authoritative regardless of our own clock, so the handler
    now expires with force=True.

    These tests fail if that force=True is ever dropped.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="payfail-early-trial@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(
            "STANDARD", PlanTier.STANDARD, 999, 10_000_000, "price_payfail_early"
        )
        self.trial_sub = SubscriptionService.activate_free_trial(self.user, self.plan)
        # Trial genuinely still running — this is the skew/latency case.
        self.trial_sub.stripe_subscription_id = STRIPE_SUB_ID
        self.trial_sub.trial_end = timezone.now() + timedelta(minutes=5)
        self.trial_sub.save(update_fields=["stripe_subscription_id", "trial_end"])

    def test_early_failure_expires_the_trial_instead_of_raising(self):
        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        self.trial_sub.refresh_from_db()
        self.assertFalse(self.trial_sub.is_active)
        self.assertFalse(self.trial_sub.is_trial)

    def test_early_failure_still_leaves_an_audit_row(self):
        """
        The rolled-back audit row was the worst part of the old behavior:
        a declined charge that left no trace anywhere to diagnose from.
        """
        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(txn.status, BillingTransactionStatus.FAILED)
        self.assertEqual(txn.user_id, self.user.id)

    def test_early_failure_forfeits_unused_trial_credits(self):
        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)
        self.assertTrue(bucket.is_processed)
        self.assertTrue(
            CreditLedger.objects.filter(
                user_id=self.user.id,
                bucket=bucket,
                ledger_type=CreditLedgerType.EXPIRE,
            ).exists()
        )

    def test_stripe_retries_converge_instead_of_looping(self):
        """
        The retry-storm proof, inverted: three deliveries of the same
        event, none raising, so Stripe gets its 200 on the first attempt
        and the event resolves instead of retrying for ~3 days. The
        repeat deliveries must also be harmless — no duplicate audit row,
        and no resurrecting the trial that the first call ended.
        """
        for _ in range(3):
            StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        self.assertEqual(
            BillingTransaction.objects.filter(stripe_invoice_id="in_payfail_1").count(),
            1,
        )
        self.trial_sub.refresh_from_db()
        self.assertFalse(self.trial_sub.is_active)
        self.assertFalse(self.trial_sub.is_trial)

    def test_credits_are_forfeited_only_once_across_retries(self):
        """
        expire_trial writes an EXPIRE ledger row for the unused balance.
        A redelivery must not write a second one, or the forfeiture is
        double-counted in the credit audit trail.
        """
        for _ in range(3):
            StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)
        self.assertEqual(
            CreditLedger.objects.filter(
                user_id=self.user.id,
                bucket=bucket,
                ledger_type=CreditLedgerType.EXPIRE,
            ).count(),
            1,
        )


class LicenseInvoicePaymentFailedTests(TestCase):
    """
    Pins the individual-vs-license routing at the top of the handler.
    The license track's own payment-failure semantics are otherwise
    still uncovered — out of scope here.
    """

    def setUp(self):
        self.school = School.objects.create(name="Payfail School")
        self.admin = CustomUser.objects.create_user(
            email="payfail-admin@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.plan = make_plan(
            "PRO_LICENSE",
            PlanTier.PRO,
            9999,
            20_000_000,
            "price_payfail_license",
            category=PlanCategory.LICENSE,
        )
        now = timezone.now()
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            is_active=True,
            auto_renew=True,
            billing_cycle_start=now - relativedelta(months=1),
            billing_cycle_end=now - timedelta(minutes=5),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
            billing_method=LicenseBillingMethod.STRIPE,
        )

    def test_license_subscription_marked_past_due(self):
        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        self.license.refresh_from_db()
        self.assertEqual(self.license.stripe_status, StripeSubscriptionStatus.PAST_DUE)
        self.assertTrue(self.license.is_active)

    def test_license_failure_is_recorded_against_the_license_source(self):
        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(txn.source, BillingTransactionSource.LICENSE)
        self.assertEqual(txn.status, BillingTransactionStatus.FAILED)
        self.assertEqual(txn.license_subscription_id, self.license.id)
        self.assertEqual(
            txn.transaction_type,
            BillingTransactionType.LICENSE_SUBSCRIPTION_CHARGE,
        )

    def test_individual_subscription_takes_precedence_over_license(self):
        """
        Both tracks are looked up by the same stripe_subscription_id, and
        the individual branch returns early. If a stray id ever collided,
        the license row must be left completely alone.
        """
        user = CustomUser.objects.create_user(
            email="payfail-collision@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        CreditWallet.objects.get_or_create(user=user)
        individual_plan = make_plan(
            "PRO", PlanTier.PRO, 2999, 30_000_000, "price_payfail_collide"
        )
        now = timezone.now()
        UserSubscription.objects.create(
            user=user,
            plan=individual_plan,
            is_active=True,
            auto_renew=True,
            billing_cycle_start=now - relativedelta(months=1),
            billing_cycle_end=now - timedelta(minutes=5),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

        StripeWebhookHandler.handle_invoice_payment_failed(invoice_payload())

        self.license.refresh_from_db()
        self.assertEqual(self.license.stripe_status, StripeSubscriptionStatus.ACTIVE)
        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(txn.source, BillingTransactionSource.INDIVIDUAL)
