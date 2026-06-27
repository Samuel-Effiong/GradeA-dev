"""
billing/tests/test_subscription_upgrade.py
=============================================
Tests for SubscriptionManagementViewSet.upgrade() and
StripeSubscriptionMutationService.change_plan().

All Stripe API calls are mocked via `@patch("stripe.Subscription")` /
`@patch("stripe.Invoice")` — patching the attributes on the real `stripe`
module directly (rather than replacing the whole module reference inside
stripe_service.py) deliberately leaves `stripe.error.*` untouched as the
real exception classes. Patching the whole module would make any
`except stripe.error.StripeError` in the implementation raise a TypeError
the moment an exception needs to match against it, since a MagicMock
attribute isn't a real exception class.

NOTE: user/wallet creation in setUp() assumes `CustomUser.objects.create_user`
and a `UserTypes.TEACHER` choice exist as shown elsewhere in this codebase.
Adjust to match your actual user factory/manager if it differs — these
tests haven't been run against your live environment from here, so treat
this as thorough scaffolding to verify locally, not guaranteed first-run-green.
"""

from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from billing.models import (
    CreditBucketType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from users.models import UserTypes

CustomUser = get_user_model()

UPGRADE_URL = "/api/v1/subscription/upgrade"


def make_plan(
    name,
    tier,
    price_cents,
    monthly_credits,
    stripe_price_id,
    category=PlanCategory.INDIVIDUAL,
):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=category,
        tier=tier,
        price_cents=price_cents,
        monthly_credits=monthly_credits,
        stripe_price_id=stripe_price_id,
        is_active=True,
    )


class SubscriptionUpgradeTestCase(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="teacher@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.standard_plan = make_plan(
            "STANDARD",
            PlanTier.STANDARD,
            price_cents=999,
            monthly_credits=10_000_000,
            stripe_price_id="price_standard",
        )
        self.pro_plan = make_plan(
            "PRO",
            PlanTier.PRO,
            price_cents=2999,
            monthly_credits=30_000_000,
            stripe_price_id="price_pro",
        )

        now = timezone.now()
        self.current_sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.standard_plan,
            is_active=True,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=1),
            stripe_subscription_id="sub_test_123",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_test_123"}
        )

    @staticmethod
    def _stripe_subscription_payload(
        price_id, item_id="si_test_1", latest_invoice="in_test_1"
    ):
        return {
            "id": "sub_test_123",
            "items": {"data": [{"id": item_id, "price": {"id": price_id}}]},
            "latest_invoice": latest_invoice,
        }

    # ------------------------------------------------------------------
    # Validation — these must reject BEFORE touching Stripe at all
    # ------------------------------------------------------------------

    @patch("stripe.Subscription")
    def test_upgrade_requires_plan_field(self, mock_subscription):
        response = self.client.post(UPGRADE_URL, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_upgrade_rejects_nonexistent_plan(self, mock_subscription):
        response = self.client.post(
            UPGRADE_URL, {"plan": "00000000-0000-0000-0000-000000000000"}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_upgrade_rejects_inactive_plan(self, mock_subscription):
        self.pro_plan.is_active = False
        self.pro_plan.save(update_fields=["is_active"])
        response = self.client.post(UPGRADE_URL, {"plan": str(self.pro_plan.id)})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_upgrade_rejects_license_category_plan(self, mock_subscription):
        license_plan = make_plan(
            "PRO_LICENSE",
            PlanTier.PRO,
            price_cents=9999,
            monthly_credits=30_000_000,
            stripe_price_id="price_license_pro",
            category=PlanCategory.LICENSE,
        )
        response = self.client.post(UPGRADE_URL, {"plan": str(license_plan.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_upgrade_rejects_when_no_active_subscription(self, mock_subscription):
        self.current_sub.is_active = False
        self.current_sub.save(update_fields=["is_active"])
        response = self.client.post(UPGRADE_URL, {"plan": str(self.pro_plan.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("checkout", response.data["detail"])
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_upgrade_rejects_trial_subscriptions(self, mock_subscription):
        self.current_sub.is_trial = True
        self.current_sub.save(update_fields=["is_trial"])
        response = self.client.post(UPGRADE_URL, {"plan": str(self.pro_plan.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("convert-trial", response.data["detail"])
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_upgrade_rejects_same_plan(self, mock_subscription):
        response = self.client.post(UPGRADE_URL, {"plan": str(self.standard_plan.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already on this plan", response.data["detail"])
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_upgrade_rejects_cheaper_plan(self, mock_subscription):
        # Pro is active; "upgrading" to Standard would actually be a downgrade.
        self.current_sub.plan = self.pro_plan
        self.current_sub.save(update_fields=["plan"])
        response = self.client.post(UPGRADE_URL, {"plan": str(self.standard_plan.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("downgrade", response.data["detail"])
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_upgrade_rejects_subscription_with_no_stripe_id(self, mock_subscription):
        self.current_sub.stripe_subscription_id = None
        self.current_sub.save(update_fields=["stripe_subscription_id"])
        response = self.client.post(UPGRADE_URL, {"plan": str(self.pro_plan.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("contact support", response.data["detail"])
        mock_subscription.modify.assert_not_called()

    # ------------------------------------------------------------------
    # Success path — invoice pays immediately
    # ------------------------------------------------------------------

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_successful_upgrade_grants_credits_and_updates_plan(
        self, mock_subscription, mock_invoice
    ):
        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro"),
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "paid",
            "payment_intent": None,
        }

        response = self.client.post(UPGRADE_URL, {"plan": str(self.pro_plan.id)})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # activate_subscription()'s existing pattern: deactivate old row,
        # create a new one pointing at the new plan.
        new_sub = UserSubscription.objects.get(user=self.user, is_active=True)
        self.assertEqual(new_sub.plan_id, self.pro_plan.id)
        self.assertEqual(new_sub.stripe_subscription_id, "sub_test_123")
        self.assertEqual(new_sub.stripe_status, StripeSubscriptionStatus.ACTIVE)

        old_sub = UserSubscription.objects.get(pk=self.current_sub.pk)
        self.assertFalse(old_sub.is_active)

        monthly_bucket = self.wallet.buckets.filter(
            bucket_type=CreditBucketType.MONTHLY
        ).first()
        self.assertIsNotNone(monthly_bucket)
        self.assertEqual(monthly_bucket.total_credits, self.pro_plan.monthly_credits)

        mock_subscription.modify.assert_called_once()
        _, kwargs = mock_subscription.modify.call_args
        self.assertEqual(kwargs["proration_behavior"], "always_invoice")
        self.assertEqual(kwargs["items"][0]["price"], "price_pro")

        # No revert call should have happened on the success path.
        self.assertEqual(mock_subscription.modify.call_count, 1)
        mock_invoice.void_invoice.assert_not_called()

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_upgrade_succeeds_when_no_proration_invoice_is_generated(
        self, mock_subscription, mock_invoice
    ):
        # E.g. the two plans happen to be priced identically — Stripe
        # doesn't generate an invoice at all in that case.
        after = self._stripe_subscription_payload("price_pro", latest_invoice=None)
        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            after,
        ]
        mock_subscription.modify.return_value = None

        response = self.client.post(UPGRADE_URL, {"plan": str(self.pro_plan.id)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_invoice.retrieve.assert_not_called()

    # ------------------------------------------------------------------
    # Failure paths — must revert the Stripe price AND grant no credits
    # ------------------------------------------------------------------

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_declined_invoice_reverts_price_and_grants_no_credits(
        self, mock_subscription, mock_invoice
    ):
        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro"),
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "open",
            "payment_intent": {"status": "requires_payment_method"},
        }

        response = self.client.post(UPGRADE_URL, {"plan": str(self.pro_plan.id)})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("payment failed", response.data["detail"])

        # Local plan must NOT have changed.
        self.current_sub.refresh_from_db()
        self.assertTrue(self.current_sub.is_active)
        self.assertEqual(self.current_sub.plan_id, self.standard_plan.id)

        # No credits granted for a plan that was never actually paid for.
        self.assertFalse(
            self.wallet.buckets.filter(
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=self.pro_plan.monthly_credits,
            ).exists()
        )

        # Subscription item must have been reverted back to the old price.
        self.assertEqual(mock_subscription.modify.call_count, 2)
        revert_call = mock_subscription.modify.call_args_list[-1]
        self.assertEqual(revert_call.kwargs["items"][0]["price"], "price_standard")
        self.assertEqual(revert_call.kwargs["proration_behavior"], "none")

        # Unpaid invoice should have been voided.
        mock_invoice.void_invoice.assert_called_once_with("in_test_1")

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_requires_action_gives_clear_message_and_reverts(
        self, mock_subscription, mock_invoice
    ):
        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro"),
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "open",
            "payment_intent": {"status": "requires_action"},
        }

        response = self.client.post(UPGRADE_URL, {"plan": str(self.pro_plan.id)})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("authentication", response.data["detail"])

        self.current_sub.refresh_from_db()
        self.assertEqual(self.current_sub.plan_id, self.standard_plan.id)

        revert_call = mock_subscription.modify.call_args_list[-1]
        self.assertEqual(revert_call.kwargs["items"][0]["price"], "price_standard")

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_card_declined_synchronously_during_modify(
        self, mock_subscription, mock_invoice
    ):
        # Stripe can reject some cards immediately during modify() itself,
        # rather than via the resulting invoice — uses the REAL exception
        # class since stripe.error is untouched by these patches.
        mock_subscription.retrieve.return_value = self._stripe_subscription_payload(
            "price_standard"
        )
        mock_subscription.modify.side_effect = real_stripe.error.CardError(
            message="Your card was declined.",
            param=None,
            code="card_declined",
        )

        response = self.client.post(UPGRADE_URL, {"plan": str(self.pro_plan.id)})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("declined", response.data["detail"])

        self.current_sub.refresh_from_db()
        self.assertEqual(self.current_sub.plan_id, self.standard_plan.id)
        mock_invoice.retrieve.assert_not_called()

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_revert_failure_is_logged_not_raised(self, mock_subscription, mock_invoice):
        """
        If the revert call ITSELF fails after a declined invoice, the
        original "payment failed" error should still surface to the user —
        not be replaced by the revert's own StripeError. The inconsistent
        Stripe state is logged for manual reconciliation instead.
        """
        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro"),
        ]
        mock_subscription.modify.side_effect = [
            None,  # the original upgrade modify() call succeeds
            real_stripe.error.APIConnectionError("network blip during revert"),
        ]
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "open",
            "payment_intent": {"status": "requires_payment_method"},
        }

        response = self.client.post(UPGRADE_URL, {"plan": str(self.pro_plan.id)})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("payment failed", response.data["detail"])
