"""
QA bug (owner, 2026-11-25): buying extra credits failed with Stripe's own
"No such customer ... a similar object exists in test mode" text, because
the wallet held a test-mode customer id and the overage checkout, unlike
the plan checkout, had no H-274 repair.

1(a) The overage checkout now uses the same repair: when Stripe says the
     saved CUSTOMER is missing, the stale id is replaced and the session is
     made once more. Any other Stripe failure answers with the approved
     checkout text, never Stripe's.
2(b) A teacher with no active subscription, or whose trial has ended, is
     refused before Stripe with "Choose a paid plan to buy more credits."

Stripe is always mocked here.

    python manage.py test billing.tests.test_overage_checkout_heal
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import stripe
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import (
    BillingInterval,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import CHECKOUT_FAILED_MESSAGE
from users.models import UserTypes

CustomUser = get_user_model()

PAID_PLAN_TEXT = "Choose a paid plan to buy more credits."
STALE = "cus_TestModeOnly"
FRESH = "cus_FreshLive"
BODY = {
    "success_url": "https://example.com/ok",
    "cancel_url": "https://example.com/cancel",
    "quantity": 1,
}


def missing_customer():
    return stripe.error.InvalidRequestError(
        f"No such customer: {STALE!r}; a similar object exists in test mode",
        "customer",
        code="resource_missing",
    )


class OverageCheckoutBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name=PlanType.PRO,
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            price_cents=2999,
            monthly_credits=30_000_000,
            stripe_price_id="price_overage_heal_plan",
            is_active=True,
            overage_block_size=500,
            overage_block_price=400,
            max_overage_blocks=10,
            stripe_overage_price_id="price_overage_heal",
        )
        self.user = CustomUser.objects.create_user(
            email="overage-heal@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.wallet.stripe_customer_id = STALE
        self.wallet.save(update_fields=["stripe_customer_id"])
        self.client.force_authenticate(user=self.user)

    def subscribe(self, **extra):
        now = timezone.now()
        fields = {
            "user": self.user,
            "plan": self.plan,
            "is_active": True,
            "is_trial": False,
            "billing_cycle_start": now,
            "billing_cycle_end": now + timedelta(days=30),
            "next_credit_grant_at": now + timedelta(days=30),
        }
        fields.update(extra)
        return UserSubscription.objects.create(**fields)

    def buy(self):
        with patch("billing.stripe_service.assert_overage_price_in_sync"):
            return self.client.post(
                reverse("subscription-purchase-overage"), BODY, format="json"
            )


class StaleCustomerTests(OverageCheckoutBase):
    def setUp(self):
        super().setUp()
        self.subscribe()

    def test_a_missing_customer_is_replaced_and_checkout_starts(self):
        session = MagicMock(id="cs_heal", url="https://checkout.example/cs_heal")
        create = MagicMock(side_effect=[missing_customer(), session])
        with patch(
            "billing.stripe_service.stripe.checkout.Session.create", create
        ), patch(
            "billing.stripe_service.stripe.Customer.create",
            return_value=MagicMock(id=FRESH),
        ):
            response = self.buy()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(create.call_count, 2)
        self.assertEqual(create.call_args_list[0].kwargs["customer"], STALE)
        self.assertEqual(create.call_args_list[1].kwargs["customer"], FRESH)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.stripe_customer_id, FRESH)

    def test_a_second_failure_answers_with_the_checkout_text(self):
        create = MagicMock(side_effect=[missing_customer(), missing_customer()])
        with patch(
            "billing.stripe_service.stripe.checkout.Session.create", create
        ), patch(
            "billing.stripe_service.stripe.Customer.create",
            return_value=MagicMock(id=FRESH),
        ):
            response = self.buy()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], CHECKOUT_FAILED_MESSAGE)
        self.assertEqual(create.call_count, 2)

    def test_another_stripe_error_is_not_retried_and_shows_no_stripe_text(self):
        error = stripe.error.APIConnectionError("secret network detail")
        create = MagicMock(side_effect=error)
        with patch("billing.stripe_service.stripe.checkout.Session.create", create):
            response = self.buy()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], CHECKOUT_FAILED_MESSAGE)
        self.assertEqual(create.call_count, 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.stripe_customer_id, STALE)


class PaidPlanRefusalTests(OverageCheckoutBase):
    def assert_refused(self):
        create = MagicMock()
        with patch("billing.stripe_service.stripe.checkout.Session.create", create):
            response = self.buy()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], PAID_PLAN_TEXT)
        create.assert_not_called()

    def test_no_active_subscription_is_told_to_choose_a_paid_plan(self):
        self.assert_refused()

    def test_an_expired_trial_switched_off_is_told_to_choose_a_paid_plan(self):
        self.subscribe(is_active=False, is_trial=False)
        self.assert_refused()

    def test_a_trial_past_its_end_but_still_active_is_refused(self):
        """The expiry task may not have run yet: the end date decides."""
        self.subscribe(is_trial=True, trial_end=timezone.now() - timedelta(days=1))
        self.assert_refused()

    def test_a_running_trial_is_not_refused_by_this_rule(self):
        self.subscribe(is_trial=True, trial_end=timezone.now() + timedelta(days=3))
        session = MagicMock(id="cs_trial", url="https://checkout.example/cs_trial")
        with patch(
            "billing.stripe_service.stripe.checkout.Session.create",
            return_value=session,
        ):
            response = self.buy()
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
