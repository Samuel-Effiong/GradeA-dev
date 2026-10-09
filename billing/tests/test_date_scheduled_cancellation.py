"""
H-178 (part A): a cancellation Stripe has scheduled BY DATE (`cancel_at`) is
mirrored onto the local record like one scheduled for the period's end.

WHAT WAS WRONG (found by reading, 2026-10-07; not run until these tests)
------------------------------------------------------------------------
`StripeWebhookHandler._sync_cancellation_intent` (the `customer.subscription.
updated` mirror) was driven by `cancel_at_period_end` alone. A subscription
with `cancel_at` set and that flag false read as "not cancelling": the mirror
set `auto_renew = True` and cleared `cancelled_at`. The local record said
"renews" while Stripe would end the subscription on the date.

THE RULE (part A, no migration; the Senior Manager approved it 2026-10-08)
-------------------------------------------------------------------------
"Scheduled to end on Stripe" means `cancel_at_period_end` is true OR
`cancel_at` is set. The record goes back to renewing only when both are clear.
The existing guard stays: a payload that carries neither key says nothing and
changes nothing, so a thin payload cannot un-cancel a subscription.

KNOWN LIMITS, SAID PLAINLY (the package carries them too)
---------------------------------------------------------
  * The page's sentence will say "You cancelled this subscription" for a date
    set by Stripe's dashboard or by support, which is untrue, and it shows the
    period's end, which is wrong where the real date is earlier. Showing the
    real date and who scheduled it is part B (a stored date and the page), not
    built until it is decided.
  * "Keep subscription" will be offered (the record now reads as scheduled to
    cancel) and then refuses: the answer tells the customer to contact support
    and does not say the subscription is active. Pinned below. It does not
    clear the date at Stripe: that is a decision, not a defect.
  * Stripe is a stand-in everywhere here; how Stripe forms `cancel_at` when the
    date equals the period's end is not shown (the rule does not depend on it).

Run with:
    python manage.py test billing.tests.test_date_scheduled_cancellation
"""

from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import (
    BillingInterval,
    CreditWallet,
    PlanCategory,
    PlanTier,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import StripeWebhookHandler
from users.models import UserTypes

CustomUser = get_user_model()

STRIPE_SUB_ID = "sub_h178_made"


def stamp(delta):
    """A Stripe timestamp (whole seconds) `delta` from now."""
    return int((timezone.now() + delta).timestamp())


class StripeAnswer(dict):
    """Stands where Stripe's subscription object stands."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


class PaidSubscriptionFixture:
    def build(self, *, auto_renew=True, cancelled_at=None, is_trial=False):
        self.user = CustomUser.objects.create_user(
            email="h178.teacher@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        UserSubscription.objects.filter(user=self.user).delete()
        self.plan = SubscriptionPlan.objects.create(
            name="STANDARD",
            display_name="Standard",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            price_cents=999,
            monthly_credits=10_000,
            stripe_price_id="price_h178_standard",
            carry_over_percent=0,
            carry_over_expiry_months=1,
            is_active=True,
        )
        now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=is_trial,
            auto_renew=auto_renew,
            cancelled_at=cancelled_at,
            billing_cycle_start=now - relativedelta(days=5),
            billing_cycle_end=now + relativedelta(days=25),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        CreditWallet.objects.filter(user=self.user).update(
            stripe_customer_id="cus_h178_made"
        )

    def reloaded(self):
        return UserSubscription.objects.get(pk=self.sub.pk)

    def deliver(self, **fields):
        """The `customer.subscription.updated` message, given to the handler."""
        payload = {"id": STRIPE_SUB_ID, "status": "active", **fields}
        StripeWebhookHandler.handle_subscription_updated(payload)


class DateScheduledCancellationMirrorTests(PaidSubscriptionFixture, TestCase):
    def test_a_date_with_the_period_end_flag_off_stops_the_record_renewing(self):
        self.build()

        self.deliver(
            cancel_at_period_end=False,
            cancel_at=stamp(timedelta(days=10)),
            canceled_at=stamp(timedelta(minutes=-5)),
        )

        sub = self.reloaded()
        self.assertIs(sub.auto_renew, False)
        self.assertIsNotNone(sub.cancelled_at)

    def test_the_recorded_date_is_stripes_own_when_it_gives_one(self):
        self.build()
        asked = stamp(timedelta(days=-2))

        self.deliver(
            cancel_at_period_end=False,
            cancel_at=stamp(timedelta(days=10)),
            canceled_at=asked,
        )

        recorded = self.reloaded().cancelled_at
        assert recorded is not None
        self.assertEqual(int(recorded.timestamp()), asked)

    def test_a_repeat_of_the_same_message_writes_nothing(self):
        self.build()
        message = {
            "cancel_at_period_end": False,
            "cancel_at": stamp(timedelta(days=10)),
            "canceled_at": stamp(timedelta(minutes=-5)),
        }
        self.deliver(**message)
        first = self.reloaded()
        self.assertIs(first.auto_renew, False)

        self.deliver(**message)

        again = self.reloaded()
        self.assertEqual(again.updated_at, first.updated_at)
        self.assertEqual(again.cancelled_at, first.cancelled_at)

    def test_a_message_with_a_date_but_without_the_flag_is_scheduled(self):
        """The flag is absent (a thin payload), the date is there: that says
        the subscription will end."""
        self.build()

        self.deliver(cancel_at=stamp(timedelta(days=10)))

        self.assertIs(self.reloaded().auto_renew, False)

    def test_clearing_the_flag_does_not_undo_a_date_that_is_still_set(self):
        """The customer's own cancellation (period end) was changed in
        Stripe's dashboard to a date: the flag clears but the subscription
        still ends. The record must not go back to renewing."""
        self.build(auto_renew=False, cancelled_at=timezone.now())

        self.deliver(cancel_at_period_end=False, cancel_at=stamp(timedelta(days=10)))

        sub = self.reloaded()
        self.assertIs(sub.auto_renew, False)
        self.assertIsNotNone(sub.cancelled_at)

    def test_clearing_both_puts_the_record_back_to_renewing(self):
        """Green on the old code too: held across the change."""
        self.build(auto_renew=False, cancelled_at=timezone.now())

        self.deliver(cancel_at_period_end=False, cancel_at=None, canceled_at=None)

        sub = self.reloaded()
        self.assertIs(sub.auto_renew, True)
        self.assertIsNone(sub.cancelled_at)

    def test_a_message_that_says_nothing_about_it_changes_nothing(self):
        """Neither key: nothing is said, so nothing is un-cancelled. Green on
        the old code too."""
        self.build(auto_renew=False, cancelled_at=timezone.now())
        before = self.reloaded()

        self.deliver()

        after = self.reloaded()
        self.assertIs(after.auto_renew, False)
        self.assertEqual(after.cancelled_at, before.cancelled_at)

    def test_the_period_end_flag_alone_still_mirrors(self):
        """Green on the old code too: the existing behaviour is kept."""
        self.build()

        self.deliver(cancel_at_period_end=True)

        self.assertIs(self.reloaded().auto_renew, False)

    def test_a_trial_is_left_alone(self):
        """A trial's auto_renew is False by design and must not gain a
        fabricated cancellation date. Green on the old code too."""
        self.build(auto_renew=False, is_trial=True)

        self.deliver(cancel_at_period_end=False, cancel_at=stamp(timedelta(days=3)))

        sub = self.reloaded()
        self.assertIs(sub.auto_renew, False)
        self.assertIsNone(sub.cancelled_at)


class KeepAnswersForADateScheduledRecordTests(PaidSubscriptionFixture, APITestCase):
    """What "Keep subscription" tells a customer whose subscription Stripe will
    end on a date: that it cannot be undone here and to contact support, and
    NOT that the subscription is active. Green on the old code (pins H-174's
    answer); it keeps part A's visible side effect honest."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build(auto_renew=False, cancelled_at=timezone.now())
        self.client.force_authenticate(user=self.user)

    @patch("stripe.Subscription")
    def test_the_answer_says_to_contact_support_and_not_that_it_is_active(
        self, stripe_subscription
    ):
        stripe_subscription.retrieve.return_value = StripeAnswer(
            status="active",
            cancel_at_period_end=False,
            cancel_at=stamp(timedelta(days=10)),
            canceled_at=stamp(timedelta(days=-1)),
        )

        response = self.client.post(reverse("subscription-resume"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "cancellation_scheduled")
        message = response.data["message"].lower()
        self.assertIn("contact support", message)
        self.assertNotIn("active", message)
        self.assertNotIn("nothing to resume", message)
        stripe_subscription.modify.assert_not_called()
        sub = self.reloaded()
        self.assertIs(sub.auto_renew, False)
