"""
H-174: a plan bought during the free trial is not "scheduled to cancel".

WHAT THE USER SAW (the trial service, 2026-10-07)
-------------------------------------------------
A new subscriber on the Standard plan is shown "Subscription scheduled to
cancel". The API answer: is_trial false, auto_renew false, cancelled_at
null, cancellation.has_pending_cancellation true. Pressing "Keep
subscription" leaves the banner; that request's own answer says "already
active and set to renew" and encloses a subscription that still reads as
a pending cancellation.

WHY (read in the code, then shown by these tests on the old code)
-----------------------------------------------------------------
1. Every trial's record is born with auto_renew=False ("a trial does not
   turn into a paid plan by itself"). Buying a plan during the trial
   turns the SAME record into the paid one, and neither conversion
   (finalize_trial_to_paid_conversion, finalize_trial_conversion_via_
   stripe) set auto_renew. The serializer's "pending cancellation" is
   "active, not a trial, auto_renew false, cycle not ended".
2. "Keep subscription" set auto_renew back only when Stripe itself said
   the subscription was cancelling. For these records Stripe never did,
   so nothing was written.
3. Stripe was never told to cancel: it renews and charges. So a customer
   who wants to leave is told it "won't renew" and is then charged.

THE RULING (Senior Manager, 2026-10-07)
---------------------------------------
1. Both conversions set auto_renew=True and clear cancelled_at in the
   same save.
2. Stripe is the source of truth. When Stripe says "not cancelling" and
   our record says cancelling, "keep" sets auto_renew True, clears
   cancelled_at and says so; when both agree, it says "already active"
   and the enclosed subscription reads the same. Cancel on such a record
   says what it did.

HOW THE RECORDS HERE ARE MADE
-----------------------------
By production code: a teacher is created, the sign-up signal starts the
automatic trial, and the purchase is the message Stripe sends when a
Checkout completes, given to the handler that receives it. A record
"converted before the fix" is such a record with its mark put back to
false, which is exactly what the old code left.

Stripe is replaced by a stand-in. No Stripe call is made.

Run with:
    python manage.py test billing.tests.test_converted_trial_is_not_cancelling
"""

from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
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
from billing.serializers import UserSubscriptionSerializer
from billing.services import SubscriptionService
from billing.stripe_service import (
    IndividualPlanChangeService,
    StripeWebhookHandler,
    SubscriptionReactivationService,
)
from users.models import UserTypes

CustomUser = get_user_model()

STRIPE_ID = "sub_h174_made"
NOTHING_PENDING = {
    "cancelled_at": None,
    "has_pending_cancellation": False,
    "cancellation_effective_date": None,
    "cancellation_message": None,
}
WILL_NOT_RENEW = "Subscription will not renew at the end of the current billing cycle"
UNDONE = "undone the scheduled cancellation"


class StripeAnswer(dict):
    """Stands where Stripe's subscription object stands."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


def make_plan(name, tier, price_id, interval=BillingInterval.MONTHLY):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=interval,
        price_cents=999,
        monthly_credits=10_000_000,
        stripe_price_id=price_id,
        is_active=True,
    )


def page(sub):
    """What the subscription page is sent about cancellation."""
    return UserSubscriptionSerializer(sub).data["cancellation"]


@override_settings(USE_BETA_PLAN_ON_SIGNUP=False)
class BoughtDuringTheTrial(APITestCase):
    """A teacher whose sign-up started the automatic trial."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.trial_plan = make_plan("TRIAL", PlanTier.TRIAL, "price_h174_trial")
        self.standard = make_plan("STANDARD", PlanTier.STANDARD, "price_h174_std")
        self.standard_annual = make_plan(
            "STANDARD_ANNUAL",
            PlanTier.STANDARD,
            "price_h174_std_annual",
            interval=BillingInterval.ANNUAL,
        )
        self.pro = make_plan("PRO", PlanTier.PRO, "price_h174_pro")
        self.teacher = CustomUser.objects.create_user(
            email="bought.in.trial@h174.test",
            password="Str0ng-h174-made!",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        CreditWallet.objects.filter(user=self.teacher).update(
            stripe_customer_id="cus_h174_made"
        )
        # The sign-up signal made this, not the test.
        self.trial = UserSubscription.objects.get(user=self.teacher, is_active=True)
        self.assertTrue(self.trial.is_trial)
        self.assertIs(self.trial.auto_renew, False)
        self.assertEqual(page(self.trial), NOTHING_PENDING)

    def buy(self, plan):
        """The message Stripe sends when the Checkout completes, given to
        the handler that receives it. Returns the record, read again."""
        StripeWebhookHandler.handle_checkout_completed(
            {
                "id": "cs_h174_made",
                "object": "checkout.session",
                "subscription": STRIPE_ID,
                "invoice": "in_h174_made",
                "payment_intent": "pi_h174_made",
                "amount_total": plan.price_cents,
                "currency": "usd",
                "payment_status": "paid",
                "metadata": {
                    "flow": "individual_checkout",
                    "user_id": str(self.teacher.id),
                    "plan_id": str(plan.id),
                    "trial_subscription_id": str(self.trial.id),
                },
            }
        )
        sub = UserSubscription.objects.get(user=self.teacher, is_active=True)
        # The SAME record, now a paid one with Stripe's id: the conversion.
        self.assertEqual(sub.pk, self.trial.pk)
        self.assertFalse(sub.is_trial)
        self.assertEqual(sub.plan_id, plan.id)
        self.assertEqual(sub.stripe_subscription_id, STRIPE_ID)
        return sub

    def converted_before_the_fix(self, plan=None):
        """A record as the old code left it: bought during the trial, the
        trial's mark still on it, no date of cancellation."""
        sub = self.buy(plan or self.standard)
        UserSubscription.objects.filter(pk=sub.pk).update(
            auto_renew=False, cancelled_at=None
        )
        sub.refresh_from_db()
        self.assertTrue(page(sub)["has_pending_cancellation"])
        return sub


class ThePurchaseTests(BoughtDuringTheTrial):
    def test_the_plan_is_set_to_renew(self):
        sub = self.buy(self.standard)

        self.assertIs(sub.auto_renew, True)
        self.assertIsNone(sub.cancelled_at)

    def test_the_page_is_not_told_a_cancellation_is_pending(self):
        sub = self.buy(self.standard)

        data = UserSubscriptionSerializer(sub).data
        self.assertEqual(data["cancellation"], NOTHING_PENDING)
        self.assertIs(data["auto_renew"], True)
        self.assertIs(data["is_trial"], False)

    def test_an_annual_plan_the_same(self):
        """The one that would otherwise read so for a year."""
        sub = self.buy(self.standard_annual)

        self.assertIs(sub.auto_renew, True)
        self.assertEqual(page(sub), NOTHING_PENDING)

    def test_a_stray_date_of_cancellation_on_the_trial_is_cleared(self):
        UserSubscription.objects.filter(pk=self.trial.pk).update(
            cancelled_at=timezone.now() - timedelta(days=1)
        )

        sub = self.buy(self.standard)

        self.assertIsNone(sub.cancelled_at)
        self.assertEqual(page(sub), NOTHING_PENDING)


class ATrialThatStripeConvertsTests(BoughtDuringTheTrial):
    """The other conversion: a trial whose first charge Stripe makes. The
    paid-invoice handler calls it for a record that is still a trial."""

    def convert(self):
        start = timezone.now()
        SubscriptionService.finalize_trial_conversion_via_stripe(
            self.trial, period_start=start, period_end=start + relativedelta(months=1)
        )
        sub = UserSubscription.objects.get(pk=self.trial.pk)
        self.assertFalse(sub.is_trial)
        self.assertTrue(sub.is_active)
        return sub

    def test_it_is_set_to_renew_and_the_page_is_told_nothing_is_pending(self):
        sub = self.convert()

        self.assertIs(sub.auto_renew, True)
        self.assertIsNone(sub.cancelled_at)
        self.assertEqual(page(sub), NOTHING_PENDING)

    def test_a_stray_date_of_cancellation_is_cleared(self):
        UserSubscription.objects.filter(pk=self.trial.pk).update(
            cancelled_at=timezone.now() - timedelta(days=1)
        )
        self.trial.refresh_from_db()

        sub = self.convert()

        self.assertIsNone(sub.cancelled_at)


class KeepSubscriptionTests(BoughtDuringTheTrial):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.teacher)
        self.url = reverse("subscription-resume")

    @patch("stripe.Subscription")
    def test_right_after_the_purchase_the_answer_does_not_contradict_itself(
        self, stripe_subscription
    ):
        """What the user did: bought, saw the banner, pressed the button."""
        sub = self.buy(self.standard)
        stripe_subscription.retrieve.return_value = StripeAnswer(
            status="active", cancel_at_period_end=False
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "already_active")
        self.assertEqual(response.data["subscription"]["cancellation"], NOTHING_PENDING)
        self.assertIs(response.data["subscription"]["auto_renew"], True)
        stripe_subscription.modify.assert_not_called()
        # Nothing needed doing, so nothing was written.
        self.assertEqual(
            UserSubscription.objects.get(pk=sub.pk).updated_at, sub.updated_at
        )

    @patch("stripe.Subscription")
    def test_a_record_converted_before_the_fix_is_corrected(self, stripe_subscription):
        sub = self.converted_before_the_fix()
        stripe_subscription.retrieve.return_value = StripeAnswer(
            status="active", cancel_at_period_end=False
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertIs(sub.auto_renew, True)
        self.assertIsNone(sub.cancelled_at)
        self.assertEqual(response.data["status"], "resumed")
        self.assertIn("corrected", response.data["message"])
        self.assertNotIn("nothing to resume", response.data["message"])
        self.assertEqual(response.data["subscription"]["cancellation"], NOTHING_PENDING)
        # Stripe was asked, and told nothing: there was nothing to undo there.
        stripe_subscription.retrieve.assert_called_once_with(STRIPE_ID)
        stripe_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_a_stale_date_goes_with_it(self, stripe_subscription):
        """Our record says cancelled on a date; Stripe says not cancelling."""
        sub = self.converted_before_the_fix()
        UserSubscription.objects.filter(pk=sub.pk).update(
            cancelled_at=timezone.now() - timedelta(days=2)
        )
        stripe_subscription.retrieve.return_value = StripeAnswer(
            status="active", cancel_at_period_end=False
        )

        response = self.client.post(self.url)

        sub.refresh_from_db()
        self.assertIs(sub.auto_renew, True)
        self.assertIsNone(sub.cancelled_at)
        self.assertEqual(response.data["status"], "resumed")
        self.assertEqual(response.data["subscription"]["cancellation"], NOTHING_PENDING)

    @patch("stripe.Subscription")
    def test_it_does_not_guess_when_stripe_does_not_say(self, stripe_subscription):
        """An answer WITHOUT the flag is not "not cancelling" (the same
        caution the webhook's sync takes): nothing is changed."""
        sub = self.converted_before_the_fix()
        stripe_subscription.retrieve.return_value = StripeAnswer(status="active")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertIs(sub.auto_renew, False)
        stripe_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_a_real_cancellation_is_still_undone_at_stripe(self, stripe_subscription):
        """The road that already worked, on a record made the same way."""
        sub = self.buy(self.standard)
        UserSubscription.objects.filter(pk=sub.pk).update(
            auto_renew=False, cancelled_at=timezone.now()
        )
        stripe_subscription.retrieve.return_value = StripeAnswer(
            status="active", cancel_at_period_end=True
        )
        stripe_subscription.modify.return_value = None

        response = self.client.post(self.url)

        self.assertEqual(response.data["status"], "resumed")
        self.assertIn("resumed and will renew normally", response.data["message"])
        stripe_subscription.modify.assert_called_once_with(
            STRIPE_ID, cancel_at_period_end=False
        )
        sub.refresh_from_db()
        self.assertIs(sub.auto_renew, True)
        self.assertIsNone(sub.cancelled_at)

    @patch("stripe.Subscription")
    def test_a_trial_is_never_corrected_into_a_renewing_plan(self, stripe_subscription):
        """A trial's mark is false by design. Even with a Stripe id on it
        and Stripe saying "not cancelling", it stays false."""
        UserSubscription.objects.filter(pk=self.trial.pk).update(
            stripe_subscription_id=STRIPE_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        stripe_subscription.retrieve.return_value = StripeAnswer(
            status="trialing", cancel_at_period_end=False
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.trial.refresh_from_db()
        self.assertTrue(self.trial.is_trial)
        self.assertIs(self.trial.auto_renew, False)
        self.assertEqual(response.data["status"], "already_active")


class TheSharedCoreTests(BoughtDuringTheTrial):
    @patch("stripe.Subscription")
    def test_it_reports_a_local_correction_and_no_change_at_stripe(
        self, stripe_subscription
    ):
        sub = self.converted_before_the_fix()
        stripe_subscription.retrieve.return_value = StripeAnswer(
            status="active", cancel_at_period_end=False
        )

        result = SubscriptionReactivationService.reactivate_if_cancelling(sub)

        self.assertTrue(result.changed)
        self.assertTrue(result.local_changed)
        self.assertFalse(result.stripe_changed)
        stripe_subscription.modify.assert_not_called()

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_choosing_another_plan_corrects_it_and_claims_no_undone_cancellation(
        self, stripe_subscription, stripe_schedule
    ):
        """select_plan calls the same core first. It must not tell the
        customer a cancellation was undone when none was ever scheduled."""
        sub = self.converted_before_the_fix(self.pro)
        stripe_subscription.retrieve.return_value = StripeAnswer(
            status="active", cancel_at_period_end=False
        )
        stripe_schedule.create.return_value = StripeAnswer(
            id="sched_h174_made",
            phases=[{"start_date": int(sub.billing_cycle_start.timestamp())}],
        )

        result = IndividualPlanChangeService.select_plan(self.teacher, self.standard)

        self.assertEqual(result["action"], "downgrade_scheduled")
        self.assertNotIn(UNDONE, result["message"])
        sub.refresh_from_db()
        self.assertIs(sub.auto_renew, True)


class CancelOnSuchARecordTests(BoughtDuringTheTrial):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.teacher)
        self.url = reverse("subscription-cancel")

    @patch("stripe.Subscription")
    def test_it_says_what_it_did_and_records_when(self, stripe_subscription):
        """The record already read as "not renewing", but Stripe had never
        been told. This request is the one that tells it."""
        sub = self.converted_before_the_fix()
        before = timezone.now()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        stripe_subscription.modify.assert_called_once_with(
            STRIPE_ID, cancel_at_period_end=True
        )
        self.assertEqual(response.data["message"], WILL_NOT_RENEW)
        sub.refresh_from_db()
        self.assertIs(sub.auto_renew, False)
        self.assertIsNotNone(sub.cancelled_at)
        self.assertGreaterEqual(sub.cancelled_at, before)
        self.assertIn(
            "You cancelled this subscription on", page(sub)["cancellation_message"]
        )

    @patch("stripe.Subscription")
    def test_a_second_cancel_still_says_already_and_keeps_the_date(
        self, stripe_subscription
    ):
        sub = self.buy(self.standard)
        first = timezone.now() - timedelta(days=3)
        UserSubscription.objects.filter(pk=sub.pk).update(
            auto_renew=False, cancelled_at=first
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("already set to not renew", response.data["message"])
        sub.refresh_from_db()
        self.assertEqual(sub.cancelled_at, first)
