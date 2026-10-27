"""
billing/tests/test_h254_stripe_error_text.py
============================================
H-254: Stripe's own text never reaches a client unless it is a card error.

Seen on the beta test site (27 October 2026): the subscription page's toast
read "Could not verify your subscription with our payment provider: Expired
API Key provided: sk_test_****…clSJ". `describe_stripe_error` returned
`error.user_message` for ANY StripeError, and Stripe fills `user_message`
from the error's own message for every error type (stripe/_error.py), so an
AuthenticationError's internal text (with the masked key's tail) went into
the 400 body.

Pinned here:
- the reactivation route, the cancel route, the overage purchase route and
  the payment-methods route answer an AuthenticationError without its text;
- every StripeError other than a CardError gets the fallback sentence, and
  the server log gets one WARNING with the class name and Stripe's request
  id only (no message, no traceback);
- the other places that carried Stripe's text or ids into a client message
  (the QA Test Clock, `create_custom_price`, the stale-schedule retry, the
  overage price read) no longer do; the drift report keeps what an operator
  needs (price id, class, request id) without the text;
- control: a CardError's `user_message` (written for the cardholder) still
  reaches the body.

All Stripe API calls are mocked by patching attributes on the real `stripe`
module, so `stripe.error.*` stay the real exception classes.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from AutoGrader.error_messages import describe_stripe_error
from billing.models import (
    BillingInterval,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.overage_pricing import overage_price_drift
from billing.qa_time_travel import new_customer_test_clock_kwargs
from billing.stripe_service import StripePriceService, StripeSubscriptionScheduleService
from users.models import UserTypes

CustomUser = get_user_model()

PROBE = "Expired API Key provided: sk_test_h254xxxxxxxxxxxxclSJ"
REQUEST_ID = "req_h254"
STALE_SCHEDULE_ID = "sub_sched_H254stale"
OVERAGE_PRICE_ID = "price_overage_h254"


def expired_key():
    return real_stripe.error.AuthenticationError(
        PROBE, http_status=401, headers={"request-id": REQUEST_ID}
    )


def assert_no_stripe_text(test, text):
    test.assertNotIn("sk_test", text)
    test.assertNotIn("API Key", text)


def make_plan(name, tier, price_cents, stripe_price_id, **extra):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=BillingInterval.MONTHLY,
        price_cents=price_cents,
        monthly_credits=30_000_000,
        stripe_price_id=stripe_price_id,
        is_active=True,
        **extra,
    )


class SubscriptionRouteTests(APITestCase):
    """The resume route (the one in the screenshot) and the cancel route."""

    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(
            email="h254-routes@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(PlanType.PRO, PlanTier.PRO, 2999, "price_h254_pro")
        CreditWallet.objects.get_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_h254"}
        )
        self.client.force_authenticate(user=self.user)

    def _make_sub(self, **overrides):
        now = timezone.now()
        defaults = {
            "user": self.user,
            "plan": self.plan,
            "is_active": True,
            "auto_renew": True,
            "is_trial": False,
            "billing_cycle_start": now,
            "billing_cycle_end": now + relativedelta(months=1),
            "stripe_subscription_id": "sub_h254",
            "stripe_status": StripeSubscriptionStatus.ACTIVE,
        }
        defaults.update(overrides)
        return UserSubscription.objects.create(**defaults)

    @patch("stripe.Subscription")
    def test_an_expired_key_on_resume_answers_without_the_key_text(
        self, mock_subscription
    ):
        self._make_sub(auto_renew=False)
        mock_subscription.retrieve.side_effect = expired_key()

        response = self.client.post(reverse("subscription-resume"))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_no_stripe_text(self, response.data["detail"])
        self.assertEqual(
            response.data["detail"],
            "Could not verify your subscription with our payment provider: "
            "Please try again.",
        )

    @patch("stripe.Subscription")
    def test_the_resume_failure_is_logged_with_class_and_request_id_only(
        self, mock_subscription
    ):
        self._make_sub(auto_renew=False)
        mock_subscription.retrieve.side_effect = expired_key()

        with self.assertLogs("AutoGrader.error_messages", level="WARNING") as logs:
            self.client.post(reverse("subscription-resume"))

        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        assert_no_stripe_text(self, record.getMessage())
        self.assertIsNone(record.exc_info)
        self.assertEqual(record.levelname, "WARNING")
        self.assertEqual(
            record.getMessage(),
            f"Stripe call failed: AuthenticationError (request {REQUEST_ID})",
        )

    @patch("stripe.Subscription")
    def test_an_expired_key_on_cancel_answers_without_the_key_text(
        self, mock_subscription
    ):
        self._make_sub()
        mock_subscription.modify.side_effect = expired_key()

        response = self.client.post(reverse("subscription-cancel"))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_no_stripe_text(self, response.data["detail"])
        self.assertEqual(
            response.data["detail"],
            "Could not cancel your subscription with our payment provider: "
            "Please try again.",
        )

    @patch("stripe.Subscription")
    def test_a_card_errors_text_still_reaches_the_body(self, mock_subscription):
        """Control: a card error's text is written for the cardholder."""
        self._make_sub()
        mock_subscription.modify.side_effect = real_stripe.error.CardError(
            "Your card was declined.", param=None, code="card_declined"
        )

        response = self.client.post(reverse("subscription-cancel"))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "Could not cancel your subscription with our payment provider: "
            "Your card was declined.",
        )


class EveryNonCardStripeErrorTests(SimpleTestCase):
    def test_no_non_card_stripe_error_passes_its_text(self):
        errors = [
            real_stripe.error.AuthenticationError(PROBE),
            real_stripe.error.PermissionError(PROBE),
            real_stripe.error.APIConnectionError(PROBE),
            real_stripe.error.RateLimitError(PROBE),
            real_stripe.error.InvalidRequestError(PROBE, param="customer"),
            real_stripe.error.APIError(PROBE),
            real_stripe.error.IdempotencyError(PROBE),
            real_stripe.error.StripeError(PROBE),
        ]

        leaked = [
            type(error).__name__
            for error in errors
            if describe_stripe_error(error, fallback_message="FALLBACK") != "FALLBACK"
        ]

        self.assertEqual([], leaked)


class OverageRouteTests(APITestCase):
    """views.py purchase_overage answers a ValueError with its text; the
    overage price read raised one carrying Stripe's repr."""

    def setUp(self):
        cache.clear()
        self.plan = make_plan(
            PlanType.PRO,
            PlanTier.PRO,
            2999,
            "price_h254_overage_plan",
            overage_block_size=500,
            overage_block_price=400,
            max_overage_blocks=10,
            stripe_overage_price_id=OVERAGE_PRICE_ID,
        )
        self.user = CustomUser.objects.create_user(
            email="h254-overage@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        CreditWallet.objects.get_or_create(user=self.user)
        now = timezone.now()
        UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=30),
            next_credit_grant_at=now + timedelta(days=30),
        )
        self.client.force_authenticate(user=self.user)

    def test_an_expired_key_on_overage_purchase_answers_without_the_key_text(self):
        with patch(
            "billing.overage_pricing.stripe.Price.retrieve", side_effect=expired_key()
        ):
            response = self.client.post(
                reverse("subscription-purchase-overage"),
                {
                    "success_url": "https://example.com/ok",
                    "cancel_url": "https://example.com/cancel",
                    "quantity": 1,
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_no_stripe_text(self, response.data["detail"])
        self.assertNotIn(OVERAGE_PRICE_ID, response.data["detail"])

    def test_the_drift_report_names_the_failure_without_its_text(self):
        with patch(
            "billing.overage_pricing.stripe.Price.retrieve", side_effect=expired_key()
        ):
            report = overage_price_drift()

        (row,) = report
        assert_no_stripe_text(self, row["error"])
        self.assertIn(OVERAGE_PRICE_ID, row["error"])
        self.assertIn("AuthenticationError", row["error"])
        self.assertIn(REQUEST_ID, row["error"])
        self.assertFalse(row["in_sync"])


class PaymentMethodRouteTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="h254-cards@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        plan = make_plan(PlanType.STANDARD, PlanTier.STANDARD, 999, "price_h254_std")
        UserSubscription.objects.create(
            user=self.user,
            plan=plan,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + relativedelta(months=1),
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        wallet.stripe_customer_id = "cus_h254_cards"
        wallet.save(update_fields=["stripe_customer_id"])
        self.client.force_authenticate(user=self.user)

    @patch("stripe.Customer")
    @patch("stripe.PaymentMethod")
    def test_an_expired_key_on_payment_methods_answers_without_the_key_text(
        self, mock_pm, mock_customer
    ):
        mock_pm.list.side_effect = expired_key()

        response = self.client.get(reverse("payment-method-list"))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_no_stripe_text(self, response.data["detail"])


@override_settings(
    ENABLE_BILLING_TIME_TRAVEL=True, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["yopmail.com"]
)
class TestClockTextTests(SimpleTestCase):
    """new_customer_test_clock_kwargs runs inside get_or_create_customer, so
    its ValueError reaches every checkout route on a QA-configured site."""

    def test_a_test_clock_failure_carries_no_stripe_text(self):
        with patch.object(real_stripe, "api_key", "sk_test_abc123"), patch.object(
            real_stripe, "test_helpers"
        ) as helpers:
            helpers.TestClock.create.side_effect = expired_key()

            with self.assertRaises(ValueError) as ctx:
                new_customer_test_clock_kwargs("qa1@yopmail.com")

        assert_no_stripe_text(self, str(ctx.exception))
        self.assertIn("Test Clock", str(ctx.exception))


class CustomPriceTextTests(SimpleTestCase):
    @patch("stripe.Price")
    def test_create_custom_price_carries_no_stripe_text(self, mock_price):
        mock_price.create.side_effect = expired_key()

        with self.assertRaises(ValueError) as ctx:
            StripePriceService.create_custom_price("prod_h254", 1000, 9)

        assert_no_stripe_text(self, str(ctx.exception))


class StaleScheduleTextTests(TestCase):
    """schedule_plan_change_on_stripe's stale-schedule recovery put the
    schedule's id (and Stripe's text) into the client's message."""

    def setUp(self):
        user = CustomUser.objects.create_user(
            email="h254-schedule@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.pro = make_plan(PlanType.PRO, PlanTier.PRO, 2999, "price_h254_sched_pro")
        self.standard = make_plan(
            PlanType.STANDARD, PlanTier.STANDARD, 999, "price_h254_sched_std"
        )
        now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=user,
            plan=self.pro,
            is_active=True,
            auto_renew=True,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=1),
            stripe_subscription_id="sub_h254_sched",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    @staticmethod
    def conflict():
        return real_stripe.error.InvalidRequestError(
            "You cannot migrate a subscription that is already attached to a "
            f"schedule: `{STALE_SCHEDULE_ID}`.",
            param=None,
        )

    @patch("stripe.SubscriptionSchedule")
    def test_a_stale_schedule_release_failure_names_no_schedule_id(self, mock_schedule):
        mock_schedule.release.side_effect = expired_key()

        with patch.object(
            StripeSubscriptionScheduleService,
            "_create_fresh_schedule",
            side_effect=self.conflict(),
        ):
            with self.assertRaises(ValueError) as ctx:
                StripeSubscriptionScheduleService.schedule_plan_change_on_stripe(
                    self.sub, self.standard
                )

        self.assertNotIn(STALE_SCHEDULE_ID, str(ctx.exception))
        assert_no_stripe_text(self, str(ctx.exception))

    @patch("stripe.SubscriptionSchedule")
    def test_a_stale_schedule_retry_failure_names_no_schedule_id(self, mock_schedule):
        mock_schedule.release.return_value = SimpleNamespace(id=STALE_SCHEDULE_ID)

        with patch.object(
            StripeSubscriptionScheduleService,
            "_create_fresh_schedule",
            side_effect=[self.conflict(), expired_key()],
        ):
            with self.assertRaises(ValueError) as ctx:
                StripeSubscriptionScheduleService.schedule_plan_change_on_stripe(
                    self.sub, self.standard
                )

        self.assertNotIn(STALE_SCHEDULE_ID, str(ctx.exception))
        assert_no_stripe_text(self, str(ctx.exception))
