"""
Guards that decide when an invoice is allowed to drive a RENEWAL.

Two layers, two distinct failure modes, both locked here.

LAYER 1 — the webhook (StripeWebhookHandler)
  Stripe fires invoice.payment_succeeded for upgrade prorations and
  initial charges too, and does not guarantee delivery order. Three
  conditions must hold before a renewal runs:
    1. billing_reason is a real new-period reason. Otherwise an upgrade's
       proration invoice grants a whole extra cycle of credits and rolls
       the billing period forward.
    2. The local period has elapsed (redelivery / reconcile-already-won).
    3. The row is still active — a retried delivery arriving after
       customer.subscription.deleted must never resurrect a cancelled
       subscription, which it would, because renewal creates a fresh
       active row.
  In every skipped case the money is still recorded.

LAYER 2 — the reconcile sweeps (billing.tasks)
  These used to accept any `latest_invoice` with status="paid". That is
  normally the PREVIOUS cycle's invoice, which is of course still paid —
  so whenever Stripe had not actually renewed, the sweep "reconciled" a
  renewal that never happened, granting an unpaid-for cycle and pushing
  local billing_cycle_end a month past Stripe's real period (after which
  the genuine webhook is swallowed by the idempotency guard). A renewal
  now requires a paid invoice whose period extends beyond the local cycle
  end.

MOCKING CONVENTION: patch attributes ON the real `stripe` module so
`stripe.error.*` stays a real exception class.
"""

from datetime import timedelta
from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    BillingTransaction,
    BillingTransactionType,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import StripeWebhookHandler
from billing.tasks import (
    _find_new_period_paid_invoice,
    _invoice_period_end,
    process_license_renewals,
    reconcile_subscription_renewals,
)
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

STRIPE_SUB_ID = "sub_guard_test"


def make_plan(
    name, tier, price_id, category=PlanCategory.INDIVIDUAL, credits=10_000_000
):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=category,
        tier=tier,
        interval=BillingInterval.MONTHLY,
        price_cents=999,
        monthly_credits=credits,
        stripe_price_id=price_id,
        carry_over_percent=0,
        carry_over_expiry_months=1,
        is_active=True,
    )


def invoice_payload(
    billing_reason="subscription_cycle",
    *,
    invoice_id="in_guard_1",
    status="paid",
    period_end=None,
    amount_paid=999,
):
    payload = {
        "id": invoice_id,
        "status": status,
        "billing_reason": billing_reason,
        "amount_paid": amount_paid,
        "currency": "usd",
        "hosted_invoice_url": "https://stripe.test/invoice",
        "subscription": STRIPE_SUB_ID,
    }
    if period_end is not None:
        payload["period_end"] = int(period_end.timestamp())
    return payload


def stripe_sub_payload(price_id, status="active", latest_invoice="in_guard_1"):
    """Matches what sync_price and the reconcile sweep read."""
    return {
        "id": STRIPE_SUB_ID,
        "status": status,
        "latest_invoice": latest_invoice,
        "items": {"data": [{"id": "si_1", "price": {"id": price_id}}]},
    }


class IndividualInvoiceRenewalGuardTests(TestCase):
    """Layer 1, individual track."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="guard@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan("STANDARD", PlanTier.STANDARD, "price_standard")
        self.pro_plan = make_plan("PRO", PlanTier.PRO, "price_pro")

        self.now = timezone.now()
        # An elapsed cycle: renewal is due as far as timing is concerned.
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            billing_cycle_start=self.now - relativedelta(months=1),
            billing_cycle_end=self.now - timedelta(minutes=5),
            next_credit_grant_at=self.now - timedelta(minutes=5),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_guard"}
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=self.plan.monthly_credits,
            used_credits=0,
            expires_at=self.sub.billing_cycle_end,
        )

    def _handle(self, invoice, sub=None):
        with patch.object(real_stripe, "Subscription") as mock_sub:
            mock_sub.retrieve.return_value = stripe_sub_payload(
                self.plan.stripe_price_id
            )
            StripeWebhookHandler._handle_individual_invoice_succeeded(
                sub or self.sub, invoice["billing_reason"], invoice
            )

    def _active_subs(self):
        return UserSubscription.objects.filter(user=self.user, is_active=True)

    # -- the renewal must still work -----------------------------------

    def test_genuine_renewal_still_renews(self):
        """The whole point: legitimate cycle invoices must not be blocked."""
        self._handle(invoice_payload("subscription_cycle"))

        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_active, "old row should be superseded")

        new_sub = self._active_subs().get()
        self.assertNotEqual(new_sub.id, self.sub.id)
        self.assertGreater(new_sub.billing_cycle_end, timezone.now())
        self.assertTrue(
            BillingTransaction.objects.filter(
                stripe_invoice_id="in_guard_1",
                transaction_type=BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE,
            ).exists()
        )

    def test_legacy_subscription_billing_reason_still_renews(self):
        """Older Stripe API versions spell it 'subscription'."""
        self._handle(invoice_payload("subscription"))

        self.assertNotEqual(self._active_subs().get().id, self.sub.id)

    def test_trial_conversion_still_converts(self):
        self.sub.is_trial = True
        self.sub.trial_end = self.now - timedelta(minutes=5)
        self.sub.save(update_fields=["is_trial", "trial_end"])
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.TRIAL,
            total_credits=5_000_000,
            used_credits=0,
            expires_at=self.sub.trial_end,
        )

        self._handle(invoice_payload("subscription_cycle"))

        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_trial)
        self.assertTrue(self.sub.is_active, "trial converts in place")
        self.assertTrue(
            BillingTransaction.objects.filter(
                transaction_type=(
                    BillingTransactionType.INDIVIDUAL_TRIAL_CONVERSION_CHARGE
                )
            ).exists()
        )

    # -- guard 1: billing_reason ---------------------------------------

    def test_upgrade_proration_invoice_does_not_renew(self):
        """
        The regression. An immediate upgrade's proration invoice arriving
        after the cycle end used to run a full rollover-and-renewal,
        granting an extra cycle of credits off a non-renewal invoice.
        """
        self._handle(invoice_payload("subscription_update"))

        self.assertEqual(self._active_subs().get().id, self.sub.id)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_active)
        self.assertLess(self.sub.billing_cycle_end, timezone.now())
        # The charge is still recorded, tagged as an upgrade.
        self.assertTrue(
            BillingTransaction.objects.filter(
                stripe_invoice_id="in_guard_1",
                transaction_type=BillingTransactionType.INDIVIDUAL_UPGRADE_CHARGE,
            ).exists()
        )

    def test_initial_creation_invoice_does_not_renew(self):
        """subscription_create is checkout.session.completed's job."""
        self._handle(invoice_payload("subscription_create"))

        self.assertEqual(self._active_subs().get().id, self.sub.id)

    def test_skipped_invoice_still_syncs_price_and_status(self):
        """Preserved behaviour from the old already-renewed branch."""
        self.sub.stripe_status = StripeSubscriptionStatus.PAST_DUE
        self.sub.save(update_fields=["stripe_status"])

        with patch.object(real_stripe, "Subscription") as mock_sub:
            mock_sub.retrieve.return_value = stripe_sub_payload("price_something_else")
            StripeWebhookHandler._handle_individual_invoice_succeeded(
                self.sub, "subscription_update", invoice_payload("subscription_update")
            )

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.stripe_status, StripeSubscriptionStatus.ACTIVE)
        mock_sub.modify.assert_called_once()

    # -- guard 2: period not elapsed -----------------------------------

    def test_redelivery_within_a_live_period_does_not_renew(self):
        self.sub.billing_cycle_end = self.now + relativedelta(months=1)
        self.sub.save(update_fields=["billing_cycle_end"])

        self._handle(invoice_payload("subscription_cycle"))

        self.assertEqual(self._active_subs().get().id, self.sub.id)
        self.assertTrue(
            BillingTransaction.objects.filter(stripe_invoice_id="in_guard_1").exists()
        )

    # -- guard 3: never resurrect --------------------------------------

    def test_late_invoice_after_cancellation_does_not_resurrect(self):
        """
        Stripe does not guarantee ordering: a retried payment_succeeded
        can arrive after customer.subscription.deleted. Renewing would
        create a brand-new active row for a cancelled customer.
        """
        self.sub.is_active = False
        self.sub.stripe_status = StripeSubscriptionStatus.CANCELED
        self.sub.save(update_fields=["is_active", "stripe_status"])

        self._handle(invoice_payload("subscription_cycle"))

        self.assertFalse(self._active_subs().exists(), "must not resurrect")
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_active)
        self.assertEqual(
            self.sub.stripe_status,
            StripeSubscriptionStatus.CANCELED,
            "an inactive row must not be relabelled ACTIVE",
        )
        # Money that genuinely moved is still recorded for audit.
        self.assertTrue(
            BillingTransaction.objects.filter(stripe_invoice_id="in_guard_1").exists()
        )

    def test_cancelled_subscription_does_not_get_a_stripe_price_sync(self):
        self.sub.is_active = False
        self.sub.save(update_fields=["is_active"])

        with patch.object(real_stripe, "Subscription") as mock_sub:
            StripeWebhookHandler._handle_individual_invoice_succeeded(
                self.sub, "subscription_cycle", invoice_payload("subscription_cycle")
            )

        mock_sub.retrieve.assert_not_called()
        mock_sub.modify.assert_not_called()


class LicenseInvoiceRenewalGuardTests(TestCase):
    """Layer 1, license track."""

    def setUp(self):
        self.school = School.objects.create(name="Guard School")
        self.admin = CustomUser.objects.create_user(
            email="admin@guard.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.plan = make_plan(
            PlanType.PRO_LICENSE,
            PlanTier.PRO,
            "price_license",
            category=PlanCategory.LICENSE,
            credits=20_000_000,
        )
        now = timezone.now()
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            is_active=True,
            auto_renew=True,
            billing_cycle_start=now - relativedelta(months=12),
            billing_cycle_end=now - timedelta(minutes=5),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
            billing_method=LicenseBillingMethod.STRIPE,
        )

    def test_cycle_invoice_renews_an_active_license(self):
        with patch(
            "billing.stripe_service.LicenseSubscriptionService.process_license_renewal"
        ) as mock_renew:
            StripeWebhookHandler.handle_invoice_payment_succeeded(
                invoice_payload("subscription_cycle")
            )

        mock_renew.assert_called_once()

    def test_non_renewal_invoice_does_not_renew_but_is_recorded(self):
        with patch(
            "billing.stripe_service.LicenseSubscriptionService.process_license_renewal"
        ) as mock_renew:
            StripeWebhookHandler.handle_invoice_payment_succeeded(
                invoice_payload("subscription_update")
            )

        mock_renew.assert_not_called()
        self.license.refresh_from_db()
        self.assertEqual(self.license.stripe_status, StripeSubscriptionStatus.ACTIVE)
        self.assertTrue(
            BillingTransaction.objects.filter(stripe_invoice_id="in_guard_1").exists()
        )

    def test_inactive_license_is_never_revived(self):
        self.license.is_active = False
        self.license.stripe_status = StripeSubscriptionStatus.CANCELED
        self.license.save(update_fields=["is_active", "stripe_status"])

        with patch(
            "billing.stripe_service.LicenseSubscriptionService.process_license_renewal"
        ) as mock_renew:
            StripeWebhookHandler.handle_invoice_payment_succeeded(
                invoice_payload("subscription_cycle")
            )

        mock_renew.assert_not_called()
        self.license.refresh_from_db()
        self.assertFalse(self.license.is_active)
        self.assertEqual(self.license.stripe_status, StripeSubscriptionStatus.CANCELED)
        self.assertTrue(
            BillingTransaction.objects.filter(stripe_invoice_id="in_guard_1").exists()
        )


class InvoicePeriodHelperTests(TestCase):
    """Layer 2 primitives."""

    def test_reads_top_level_period_end(self):
        self.assertEqual(
            _invoice_period_end({"period_end": 1_800_000_000}), 1_800_000_000
        )

    def test_reads_max_line_item_period_end(self):
        invoice = {
            "lines": {
                "data": [
                    {"period": {"end": 1_800_000_000}},
                    {"period": {"end": 1_900_000_000}},
                ]
            }
        }

        self.assertEqual(_invoice_period_end(invoice), 1_900_000_000)

    def test_prefers_the_furthest_period_across_both_shapes(self):
        invoice = {
            "period_end": 1_800_000_000,
            "lines": {"data": [{"period": {"end": 1_900_000_000}}]},
        }

        self.assertEqual(_invoice_period_end(invoice), 1_900_000_000)

    def test_returns_none_when_no_period_information_exists(self):
        for invoice in (
            {},
            {"period_end": None},
            {"period_end": 0},
            {"lines": {}},
            {"lines": {"data": [{"period": {}}]}},
            {"period_end": True},
        ):
            with self.subTest(invoice=invoice):
                self.assertIsNone(_invoice_period_end(invoice))


class FindNewPeriodInvoiceTests(TestCase):
    def setUp(self):
        self.cycle_end = timezone.now()
        self.previous = invoice_payload(
            invoice_id="in_previous",
            period_end=self.cycle_end - timedelta(days=1),
        )
        self.renewal = invoice_payload(
            invoice_id="in_renewal",
            period_end=self.cycle_end + relativedelta(months=1),
        )

    def _find(self, latest=None):
        return _find_new_period_paid_invoice(
            STRIPE_SUB_ID, self.cycle_end, latest_invoice=latest
        )

    def test_accepts_a_latest_invoice_covering_a_new_period(self):
        with patch.object(real_stripe, "Invoice") as mock_invoice:
            found = self._find(latest=self.renewal)

        self.assertEqual(found["id"], "in_renewal")
        mock_invoice.list.assert_not_called()  # no extra API call needed

    def test_rejects_the_previous_cycles_paid_invoice(self):
        """The exact false positive that caused phantom renewals."""
        with patch.object(real_stripe, "Invoice") as mock_invoice:
            mock_invoice.list.return_value = {"data": [self.previous]}

            self.assertIsNone(self._find(latest=self.previous))

    def test_falls_back_to_listing_when_latest_is_not_the_renewal(self):
        """An upgrade proration can land after the cycle invoice."""
        upgrade = invoice_payload(
            "subscription_update",
            invoice_id="in_upgrade",
            period_end=self.cycle_end + relativedelta(months=1),
        )

        with patch.object(real_stripe, "Invoice") as mock_invoice:
            mock_invoice.list.return_value = {"data": [upgrade, self.renewal]}

            found = self._find(latest=upgrade)

        self.assertEqual(found["id"], "in_renewal")

    def test_ignores_unpaid_and_non_renewal_invoices(self):
        unpaid = invoice_payload(
            invoice_id="in_open",
            status="open",
            period_end=self.cycle_end + relativedelta(months=1),
        )

        with patch.object(real_stripe, "Invoice") as mock_invoice:
            mock_invoice.list.return_value = {"data": [unpaid]}

            self.assertIsNone(self._find(latest=unpaid))

    def test_invoice_without_period_information_never_qualifies(self):
        no_period = invoice_payload(invoice_id="in_no_period")

        with patch.object(real_stripe, "Invoice") as mock_invoice:
            mock_invoice.list.return_value = {"data": [no_period]}

            self.assertIsNone(self._find(latest=no_period))

    def test_stripe_list_failure_is_swallowed_and_blocks_renewal(self):
        with patch.object(real_stripe, "Invoice") as mock_invoice:
            mock_invoice.list.side_effect = real_stripe.error.APIConnectionError("down")

            self.assertIsNone(self._find(latest=self.previous))


class ReconcileSubscriptionRenewalsTests(TestCase):
    """Layer 2, individual sweep."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="reconcile@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan("STANDARD", PlanTier.STANDARD, "price_standard")
        now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - relativedelta(months=1),
            billing_cycle_end=now - timedelta(minutes=5),
            next_credit_grant_at=now - timedelta(minutes=5),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)

    def _run(self, latest_invoice, listed=None):
        with patch.object(real_stripe, "Subscription") as mock_sub, patch.object(
            real_stripe, "Invoice"
        ) as mock_invoice:
            mock_sub.retrieve.return_value = stripe_sub_payload(
                self.plan.stripe_price_id
            )
            mock_invoice.retrieve.return_value = latest_invoice
            mock_invoice.list.return_value = {"data": listed or []}
            return reconcile_subscription_renewals.apply().get()

    def test_does_not_renew_off_the_previous_cycles_paid_invoice(self):
        """
        The regression: Stripe has NOT billed a new cycle, so renewing
        locally would grant credits the customer never paid for and push
        billing_cycle_end a month past Stripe's real period.
        """
        previous = invoice_payload(
            invoice_id="in_previous",
            period_end=self.sub.billing_cycle_end - timedelta(days=1),
        )

        summary = self._run(previous, listed=[previous])

        self.assertIn("1 skipped (no new-period invoice)", summary)
        self.assertEqual(UserSubscription.objects.filter(user=self.user).count(), 1)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_active)
        self.assertLess(self.sub.billing_cycle_end, timezone.now())

    def test_renews_when_stripe_really_billed_a_new_period(self):
        """The safety net must still fire when a webhook was genuinely missed."""
        renewal = invoice_payload(
            invoice_id="in_renewal",
            period_end=self.sub.billing_cycle_end + relativedelta(months=1),
        )

        summary = self._run(renewal, listed=[renewal])

        self.assertIn("1 renewed", summary)
        new_sub = UserSubscription.objects.get(user=self.user, is_active=True)
        self.assertNotEqual(new_sub.id, self.sub.id)
        self.assertGreater(new_sub.billing_cycle_end, timezone.now())

    def test_unpaid_invoice_still_marks_past_due(self):
        """Preserved behaviour."""
        unpaid = invoice_payload(invoice_id="in_open", status="open")

        summary = self._run(unpaid)

        self.assertIn("1 skipped (not paid)", summary)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.stripe_status, StripeSubscriptionStatus.PAST_DUE)


class ProcessLicenseRenewalsSweepTests(TestCase):
    """Layer 2, license sweep."""

    def setUp(self):
        self.school = School.objects.create(name="Sweep School")
        self.admin = CustomUser.objects.create_user(
            email="admin@sweep.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.plan = make_plan(
            PlanType.PRO_LICENSE,
            PlanTier.PRO,
            "price_license",
            category=PlanCategory.LICENSE,
            credits=20_000_000,
        )
        now = timezone.now()
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            is_active=True,
            auto_renew=True,
            billing_cycle_start=now - relativedelta(months=12),
            billing_cycle_end=now - timedelta(minutes=5),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
            billing_method=LicenseBillingMethod.STRIPE,
        )

    def _run(self, latest_invoice, listed=None):
        with patch.object(real_stripe, "Subscription") as mock_sub, patch.object(
            real_stripe, "Invoice"
        ) as mock_invoice, patch(
            "billing.tasks.LicenseSubscriptionService.process_license_renewal"
        ) as mock_renew:
            mock_sub.retrieve.return_value = stripe_sub_payload("price_license")
            mock_invoice.retrieve.return_value = latest_invoice
            mock_invoice.list.return_value = {"data": listed or []}
            summary = process_license_renewals.apply().get()
            return summary, mock_renew

    def test_does_not_renew_off_the_previous_cycles_paid_invoice(self):
        previous = invoice_payload(
            invoice_id="in_previous",
            period_end=self.license.billing_cycle_end - timedelta(days=1),
        )

        summary, mock_renew = self._run(previous, listed=[previous])

        mock_renew.assert_not_called()
        self.assertIn("1 skipped (no new-period invoice)", summary)

    def test_renews_when_stripe_really_billed_a_new_period(self):
        renewal = invoice_payload(
            invoice_id="in_renewal",
            period_end=self.license.billing_cycle_end + relativedelta(months=12),
        )

        summary, mock_renew = self._run(renewal, listed=[renewal])

        mock_renew.assert_called_once()
        self.assertIn("1 renewed", summary)
