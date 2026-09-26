"""
billing/tests/test_subscription_reactivation.py
=================================================
Coverage for auto-resuming a subscription scheduled to cancel:

- SubscriptionReactivationServiceTestCase: the shared core
  (SubscriptionReactivationService.reactivate_if_cancelling) in isolation.
- SelectPlanAutoResumeTestCase: IndividualPlanChangeService.select_plan
  auto-resuming a cancelling subscription before applying upgrade/
  downgrade/upgrade_scheduled/lateral_scheduled, instead of rejecting the
  plan change and telling the user to call resume first.
- SubscriptionResumeTestCase: the standalone `resume` endpoint, refactored
  to share the same core logic — regression coverage proving its external
  behavior (status codes, response shape, message text) is unchanged.
- AutoResumeThenRenewalTestCase: the connection between the two features
  above and billing/tests/test_renewal_guards.py's webhook-driven renewal
  logic. Neither test file alone proves that a subscription auto-resumed
  today will actually renew at its next billing cycle — this class chains
  reactivation into StripeWebhookHandler._handle_individual_invoice_succeeded
  end-to-end to close that gap.

All Stripe API calls are mocked via `@patch("stripe.Subscription")` /
`@patch("stripe.SubscriptionSchedule")` / etc., patching attributes on the
real `stripe` module directly so `stripe.error.*` stays the real exception
classes (see test_subscription_upgrade.py's module docstring for why).
"""

from datetime import timedelta
from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from billing.models import (
    BillingInterval,
    BillingTransaction,
    BillingTransactionType,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import (
    IndividualPlanChangeService,
    StripeWebhookHandler,
    SubscriptionExpiredDuringRequest,
    SubscriptionReactivationService,
)
from users.models import UserTypes

CustomUser = get_user_model()


class FakeStripeObject(dict):
    """Minimal stand-in for Stripe's response objects (dict + attr access)."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


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


class SubscriptionReactivationServiceTestCase(TestCase):
    """SubscriptionReactivationService.reactivate_if_cancelling in isolation."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="reactivate@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(
            "PRO", PlanTier.PRO, 2999, 30_000_000, "price_pro_reactivate"
        )
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            auto_renew=False,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + relativedelta(months=1),
            stripe_subscription_id="sub_reactivate_1",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    @patch("stripe.Subscription")
    def test_noop_when_no_stripe_subscription_id(self, mock_subscription):
        self.sub.stripe_subscription_id = ""
        self.sub.save(update_fields=["stripe_subscription_id"])

        result = SubscriptionReactivationService.reactivate_if_cancelling(self.sub)

        self.assertFalse(result.changed)
        mock_subscription.retrieve.assert_not_called()

    @patch("stripe.Subscription")
    def test_noop_when_not_cancelling(self, mock_subscription):
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=False
        )

        result = SubscriptionReactivationService.reactivate_if_cancelling(self.sub)

        self.assertFalse(result.changed)
        self.assertEqual(result.warnings, [])
        mock_subscription.modify.assert_not_called()
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.auto_renew)

    @patch("stripe.Subscription")
    def test_clears_and_flips_auto_renew(self, mock_subscription):
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.return_value = None

        result = SubscriptionReactivationService.reactivate_if_cancelling(self.sub)

        self.assertTrue(result.changed)
        self.assertTrue(result.stripe_changed)
        self.assertTrue(result.local_changed)
        mock_subscription.modify.assert_called_once_with(
            "sub_reactivate_1", cancel_at_period_end=False
        )
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.auto_renew)

    @patch("stripe.Subscription")
    def test_blocks_fully_canceled(self, mock_subscription):
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="canceled", cancel_at_period_end=False
        )

        with self.assertRaises(ValueError) as ctx:
            SubscriptionReactivationService.reactivate_if_cancelling(self.sub)

        self.assertIn("already been fully canceled", str(ctx.exception))
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_surfaces_past_due_warning(self, mock_subscription):
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="past_due", cancel_at_period_end=False
        )

        result = SubscriptionReactivationService.reactivate_if_cancelling(self.sub)

        self.assertFalse(result.changed)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("outstanding payment issue", result.warnings[0])

    @patch("stripe.Subscription")
    def test_compensating_rollback_on_save_failure(self, mock_subscription):
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.return_value = None

        with patch.object(UserSubscription, "save", side_effect=Exception("db boom")):
            with self.assertRaises(Exception) as ctx:
                SubscriptionReactivationService.reactivate_if_cancelling(self.sub)

        self.assertEqual(str(ctx.exception), "db boom")
        self.assertEqual(mock_subscription.modify.call_count, 2)
        mock_subscription.modify.assert_any_call(
            "sub_reactivate_1", cancel_at_period_end=False
        )
        mock_subscription.modify.assert_any_call(
            "sub_reactivate_1", cancel_at_period_end=True
        )

    @patch("stripe.Subscription")
    def test_raises_expired_during_request_when_row_superseded(self, mock_subscription):
        def _deactivate_during_modify(*args, **kwargs):
            UserSubscription.objects.filter(pk=self.sub.pk).update(is_active=False)
            return None

        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.side_effect = _deactivate_during_modify

        with self.assertRaises(SubscriptionExpiredDuringRequest):
            SubscriptionReactivationService.reactivate_if_cancelling(self.sub)


class SelectPlanAutoResumeTestCase(TestCase):
    """
    IndividualPlanChangeService.select_plan auto-resuming a cancelling
    subscription for each of the four plan-change branches, instead of
    rejecting with "resume your subscription first".
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="autoresume@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.standard_plan = make_plan(
            "STANDARD", PlanTier.STANDARD, 999, 10_000_000, "price_ar_standard"
        )
        self.pro_plan = make_plan("PRO", PlanTier.PRO, 2999, 30_000_000, "price_ar_pro")
        self.pro_annual_plan = make_plan(
            "PRO_ANNUAL",
            PlanTier.PRO,
            29_999,
            30_000_000,
            "price_ar_pro_annual",
            interval=BillingInterval.ANNUAL,
        )
        self.power_plan = make_plan(
            "POWER", PlanTier.POWER, 9999, 90_000_000, "price_ar_power"
        )
        CreditWallet.objects.get_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_ar_1"}
        )

    def _make_sub(self, plan, stripe_subscription_id="sub_ar_1"):
        return UserSubscription.objects.create(
            user=self.user,
            plan=plan,
            is_active=True,
            auto_renew=False,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + relativedelta(months=1),
            stripe_subscription_id=stripe_subscription_id,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_downgrade_auto_resumes_and_note_stays_clean(
        self, mock_subscription, mock_schedule
    ):
        sub = self._make_sub(self.pro_plan)
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.return_value = None
        mock_schedule.create.return_value = FakeStripeObject(
            id="sched_ar_1",
            phases=[{"start_date": int(sub.billing_cycle_start.timestamp())}],
        )

        result = IndividualPlanChangeService.select_plan(self.user, self.standard_plan)

        self.assertEqual(result["action"], "downgrade_scheduled")
        self.assertIn("undone the scheduled cancellation", result["message"])
        mock_subscription.modify.assert_called_once_with(
            "sub_ar_1", cancel_at_period_end=False
        )
        mock_schedule.create.assert_called_once()

        sub.refresh_from_db()
        self.assertTrue(sub.auto_renew)
        self.assertNotIn("undone the scheduled cancellation", sub.pending_change_note)

    @patch("stripe.checkout.Session")
    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_upgrade_auto_resumes(self, mock_subscription, mock_invoice, mock_checkout):
        self._make_sub(self.standard_plan)
        mock_subscription.retrieve.side_effect = [
            FakeStripeObject(status="active", cancel_at_period_end=True),
            {
                "id": "sub_ar_1",
                "customer": "cus_ar_1",
                "items": {
                    "data": [{"id": "si_ar_1", "price": {"id": "price_ar_standard"}}]
                },
            },
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.create_preview.return_value = {"total": 1500}
        mock_checkout.create.return_value = type(
            "FakeSession", (), {"id": "cs_ar_1", "url": "https://checkout.example"}
        )()

        result = IndividualPlanChangeService.select_plan(
            self.user,
            self.pro_plan,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        self.assertEqual(result["action"], "upgrade_checkout")
        self.assertIn("undone the scheduled cancellation", result["message"])
        mock_subscription.modify.assert_called_once_with(
            "sub_ar_1", cancel_at_period_end=False
        )
        mock_invoice.create_preview.assert_called_once()

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_upgrade_scheduled_auto_resumes(self, mock_subscription, mock_schedule):
        sub = self._make_sub(self.pro_annual_plan)
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.return_value = None
        mock_schedule.create.return_value = FakeStripeObject(
            id="sched_ar_2",
            phases=[{"start_date": int(sub.billing_cycle_start.timestamp())}],
        )

        result = IndividualPlanChangeService.select_plan(self.user, self.power_plan)

        self.assertEqual(result["action"], "upgrade_scheduled")
        self.assertIn("undone the scheduled cancellation", result["message"])
        mock_subscription.modify.assert_called_once_with(
            "sub_ar_1", cancel_at_period_end=False
        )

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_lateral_scheduled_auto_resumes(self, mock_subscription, mock_schedule):
        sub = self._make_sub(self.pro_annual_plan)
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.return_value = None
        mock_schedule.create.return_value = FakeStripeObject(
            id="sched_ar_3",
            phases=[{"start_date": int(sub.billing_cycle_start.timestamp())}],
        )

        result = IndividualPlanChangeService.select_plan(self.user, self.pro_plan)

        self.assertEqual(result["action"], "lateral_change_scheduled")
        self.assertIn("undone the scheduled cancellation", result["message"])
        mock_subscription.modify.assert_called_once_with(
            "sub_ar_1", cancel_at_period_end=False
        )

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_downgrade_blocked_when_fully_canceled(
        self, mock_subscription, mock_schedule
    ):
        sub = self._make_sub(self.pro_plan)
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="canceled", cancel_at_period_end=False
        )

        with self.assertRaises(ValueError) as ctx:
            IndividualPlanChangeService.select_plan(self.user, self.standard_plan)

        self.assertIn("already been fully canceled", str(ctx.exception))
        mock_schedule.create.assert_not_called()
        mock_subscription.modify.assert_not_called()

        sub.refresh_from_db()
        self.assertFalse(sub.auto_renew)


class SubscriptionResumeTestCase(APITestCase):
    """
    SubscriptionManagementViewSet.resume — refactored to share
    SubscriptionReactivationService with select_plan's auto-resume path.
    This locks down its external behavior (previously untested) so the
    refactor can't silently change it.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="resume-endpoint@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan("PRO", PlanTier.PRO, 2999, 30_000_000, "price_resume_pro")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("subscription-resume")

    def _make_sub(self, **overrides):
        defaults = {
            "user": self.user,
            "plan": self.plan,
            "is_active": True,
            "auto_renew": False,
            "is_trial": False,
            "billing_cycle_start": timezone.now(),
            "billing_cycle_end": timezone.now() + relativedelta(months=1),
            "stripe_subscription_id": "sub_resume_1",
            "stripe_status": StripeSubscriptionStatus.ACTIVE,
        }
        defaults.update(overrides)
        return UserSubscription.objects.create(**defaults)

    @patch("stripe.Subscription")
    def test_successful_resume(self, mock_subscription):
        self._make_sub()
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.return_value = None

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "resumed")
        self.assertIn("resumed and will renew normally", response.data["message"])
        mock_subscription.modify.assert_called_once_with(
            "sub_resume_1", cancel_at_period_end=False
        )

    @patch("stripe.Subscription")
    def test_already_active_noop(self, mock_subscription):
        self._make_sub(auto_renew=True)
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=False
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "already_active")
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_fully_canceled_rejection(self, mock_subscription):
        self._make_sub()
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="canceled", cancel_at_period_end=False
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already been fully canceled", response.data["detail"])

    @patch("stripe.Subscription")
    def test_past_due_warning_surfaces(self, mock_subscription):
        self._make_sub()
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="past_due", cancel_at_period_end=True
        )
        mock_subscription.modify.return_value = None

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["warnings"]), 1)
        self.assertIn("outstanding payment issue", response.data["warnings"][0])

    @patch("stripe.Subscription")
    def test_trial_cancelling_message(self, mock_subscription):
        self._make_sub(is_trial=True)
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.return_value = None

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("trial will continue as normal", response.data["message"])

    @patch("stripe.Subscription")
    def test_trial_not_cancelling_message(self, mock_subscription):
        self._make_sub(is_trial=True)
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=False
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("don't require resuming", response.data["message"])

    @patch("stripe.Subscription")
    def test_already_renewed_race(self, mock_subscription):
        sub = self._make_sub()

        def _renew_during_modify(*args, **kwargs):
            UserSubscription.objects.filter(pk=sub.pk).update(is_active=False)
            UserSubscription.objects.create(
                user=self.user,
                plan=self.plan,
                is_active=True,
                auto_renew=True,
                billing_cycle_start=timezone.now(),
                billing_cycle_end=timezone.now() + relativedelta(months=1),
                stripe_subscription_id="sub_resume_2",
                stripe_status=StripeSubscriptionStatus.ACTIVE,
            )
            return None

        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.side_effect = _renew_during_modify

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "already_renewed")

    def test_no_active_subscription(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["status"], "inactive")

    def test_billing_period_already_ended(self):
        self._make_sub(billing_cycle_end=timezone.now() - relativedelta(days=1))

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already ended", response.data["detail"])


def _invoice_payload(
    stripe_subscription_id,
    billing_reason="subscription_cycle",
    *,
    invoice_id="in_renewal_1",
    period_end=None,
    amount_paid=999,
):
    payload = {
        "id": invoice_id,
        "status": "paid",
        "billing_reason": billing_reason,
        "amount_paid": amount_paid,
        "currency": "usd",
        "hosted_invoice_url": "https://stripe.test/invoice",
        "subscription": stripe_subscription_id,
    }
    if period_end is not None:
        payload["period_end"] = int(period_end.timestamp())
    return payload


def _stripe_sub_payload(stripe_subscription_id, price_id, status="active"):
    """Matches what StripeSubscriptionMutationService.sync_price reads."""
    return {
        "id": stripe_subscription_id,
        "status": status,
        "latest_invoice": "in_renewal_1",
        "items": {"data": [{"id": "si_renewal_1", "price": {"id": price_id}}]},
    }


class AutoResumeThenRenewalTestCase(TestCase):
    """
    Chains auto-resume (SubscriptionReactivationService, exercised via both
    the `resume` endpoint and select_plan's auto-resume path) into the next
    billing cycle's invoice.payment_succeeded webhook
    (StripeWebhookHandler._handle_individual_invoice_succeeded, locked down
    in isolation by test_renewal_guards.py). Neither existing test suite
    proves the two compose correctly — that a subscription un-cancelled
    today still renews normally at its next cycle boundary, with auto_renew
    carrying forward and credits actually granted. That's what these tests
    close.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="resume-then-renew@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(
            "PRO", PlanTier.PRO, 2999, 30_000_000, "price_resume_renew_pro"
        )
        self.stripe_subscription_id = "sub_resume_renew_1"
        self.now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            auto_renew=False,
            is_trial=False,
            billing_cycle_start=self.now - relativedelta(months=1),
            billing_cycle_end=self.now + relativedelta(days=2),
            next_credit_grant_at=self.now + relativedelta(days=2),
            stripe_subscription_id=self.stripe_subscription_id,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_resume_renew_1"}
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=self.plan.monthly_credits,
            used_credits=0,
            expires_at=self.sub.billing_cycle_end,
        )

    def _fire_renewal_webhook(self, sub, period_end=None):
        with patch.object(real_stripe, "Subscription") as mock_sub:
            mock_sub.retrieve.return_value = _stripe_sub_payload(
                self.stripe_subscription_id, self.plan.stripe_price_id
            )
            invoice = _invoice_payload(
                self.stripe_subscription_id,
                invoice_id=f"in_renewal_{sub.billing_cycle_end.timestamp()}",
                period_end=period_end,
            )
            StripeWebhookHandler._handle_individual_invoice_succeeded(
                sub, invoice["billing_reason"], invoice
            )

    @patch("stripe.Subscription")
    def test_auto_resumed_subscription_renews_on_next_cycle(self, mock_subscription):
        # Step 1: the subscription is scheduled to cancel on Stripe's side.
        # Auto-resume it via the exact shared core both `resume` and
        # `select_plan` use.
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.return_value = None

        result = SubscriptionReactivationService.reactivate_if_cancelling(self.sub)
        self.assertTrue(result.changed)
        mock_subscription.modify.assert_called_once_with(
            self.stripe_subscription_id, cancel_at_period_end=False
        )

        self.sub.refresh_from_db()
        self.assertTrue(self.sub.auto_renew, "resume must flip auto_renew locally")

        # Step 2: time passes to (and past) the cycle boundary — simulated
        # the same way test_renewal_guards.py does, by moving the local
        # billing_cycle_end into the past rather than mocking the clock.
        self.sub.billing_cycle_end = timezone.now() - timedelta(minutes=5)
        self.sub.next_credit_grant_at = self.sub.billing_cycle_end
        self.sub.save(update_fields=["billing_cycle_end", "next_credit_grant_at"])

        # Step 3: Stripe actually renews and fires the real webhook.
        self._fire_renewal_webhook(self.sub)

        # The old row is superseded; a fresh active row exists with an
        # extended cycle and auto_renew still True (the default for a
        # freshly activated subscription — proving the resume's effect
        # wasn't a one-shot flag that renewal quietly discards).
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_active, "old row should be superseded")

        new_sub = UserSubscription.objects.get(user=self.user, is_active=True)
        self.assertNotEqual(new_sub.id, self.sub.id)
        self.assertGreater(new_sub.billing_cycle_end, timezone.now())
        self.assertTrue(new_sub.auto_renew)
        self.assertEqual(new_sub.stripe_subscription_id, self.stripe_subscription_id)
        self.assertEqual(new_sub.stripe_status, StripeSubscriptionStatus.ACTIVE)

        self.assertTrue(
            BillingTransaction.objects.filter(
                stripe_subscription_id=self.stripe_subscription_id,
                transaction_type=BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE,
            ).exists()
        )
        self.assertTrue(
            CreditBucket.objects.filter(
                wallet=self.wallet,
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=self.plan.monthly_credits,
            )
            .exclude(expires_at=self.sub.billing_cycle_end)
            .exists(),
            "a fresh monthly credit bucket should have been granted for the new cycle",
        )

    @patch("stripe.Subscription")
    def test_auto_resumed_subscription_keeps_renewing_across_multiple_cycles(
        self, mock_subscription
    ):
        """
        Guards against a narrower bug: auto_renew flipping True once but
        somehow not surviving past the FIRST post-resume renewal (e.g. if
        a future change to activate_subscription() ever stopped defaulting
        new rows to auto_renew=True). Runs two renewals back to back.
        """
        mock_subscription.retrieve.return_value = FakeStripeObject(
            status="active", cancel_at_period_end=True
        )
        mock_subscription.modify.return_value = None
        SubscriptionReactivationService.reactivate_if_cancelling(self.sub)

        current = self.sub
        for _ in range(2):
            current.refresh_from_db()
            current.billing_cycle_end = timezone.now() - timedelta(minutes=5)
            current.next_credit_grant_at = current.billing_cycle_end
            current.save(update_fields=["billing_cycle_end", "next_credit_grant_at"])

            self._fire_renewal_webhook(current)

            new_sub = UserSubscription.objects.get(user=self.user, is_active=True)
            self.assertNotEqual(new_sub.id, current.id)
            self.assertTrue(new_sub.auto_renew)
            current = new_sub

        self.assertEqual(
            BillingTransaction.objects.filter(
                stripe_subscription_id=self.stripe_subscription_id,
                transaction_type=BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE,
            ).count(),
            2,
        )

    def test_resume_via_endpoint_then_renewal_succeeds(self):
        """
        End-to-end from the actual user-facing `resume` endpoint (not just
        the shared service in isolation) through to the subscription
        genuinely renewing at its next cycle.
        """
        client = APIClient()
        client.force_authenticate(user=self.user)

        with patch("stripe.Subscription") as mock_subscription:
            mock_subscription.retrieve.return_value = FakeStripeObject(
                status="active", cancel_at_period_end=True
            )
            mock_subscription.modify.return_value = None
            response = client.post(reverse("subscription-resume"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "resumed")

        self.sub.refresh_from_db()
        self.assertTrue(self.sub.auto_renew)

        self.sub.billing_cycle_end = timezone.now() - timedelta(minutes=5)
        self.sub.next_credit_grant_at = self.sub.billing_cycle_end
        self.sub.save(update_fields=["billing_cycle_end", "next_credit_grant_at"])

        self._fire_renewal_webhook(self.sub)

        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_active)
        new_sub = UserSubscription.objects.get(user=self.user, is_active=True)
        self.assertGreater(new_sub.billing_cycle_end, timezone.now())
        self.assertTrue(new_sub.auto_renew)
