"""
billing/tests/test_trial_to_annual_conversion.py
================================================
Locks the fix for a bug where a customer converts a trial to a PAID
ANNUAL plan, pays for a full year, and then receives credits for
month 1 only.

THE BUG (two independent paths, same outcome)
---------------------------------------------
(a) finalize_trial_conversion_via_stripe computed the monthly grant date
    and threw it away — `cycle_start, billing_end, _grant_at = ...` — so
    next_credit_grant_at stayed NULL. It ALSO created the MONTHLY bucket
    with expires_at=billing_end, which on an annual plan is a YEAR, so a
    single month's credits were stretched across twelve.

(b) finalize_trial_to_paid_conversion assigned next_credit_grant_at but
    omitted it from save(update_fields=[...]), so the assignment was
    silently discarded. The in-memory object looked correct; only a
    reload from the database revealed it. Every assertion here for that
    path therefore RE-READS from the DB — asserting on the returned
    object would pass against the broken code.

THE SINK
--------
process_annual_plan_credit_grants filters next_credit_grant_at__lte=now.
In SQL, NULL <= now is UNKNOWN, so the row is excluded — forever, with
no fallback sweep. The subscription silently never receives another
credit for the remaining eleven months of a year it has been paid for.

activate_free_trial deliberately leaves next_credit_grant_at unset and is
NOT changed here: during a trial there is no monthly grant cadence, and
setting it would make the annual grant task consider trial rows.
"""

from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.tasks import process_annual_plan_credit_grants
from users.models import UserTypes

CustomUser = get_user_model()

MONTHLY_CREDITS = 12_000
TOLERANCE_SECONDS = 120


def make_plan(name, interval, price_id):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=interval,
        price_cents=4_999,
        monthly_credits=MONTHLY_CREDITS,
        stripe_price_id=price_id,
        carry_over_percent=0,
        carry_over_expiry_months=1,
        is_active=True,
    )


class TrialConversionBase(TestCase):
    def setUp(self):
        self.annual_plan = make_plan(
            "PRO_ANNUAL", BillingInterval.ANNUAL, "price_pro_annual"
        )
        self.monthly_plan = make_plan(
            "PRO", BillingInterval.MONTHLY, "price_pro_monthly"
        )
        self.user = CustomUser.objects.create_user(
            email="convert@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        UserSubscription.objects.filter(user=self.user).delete()
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.wallet.buckets.all().delete()

    def _make_trial(self, plan):
        return SubscriptionService.activate_free_trial(self.user, plan)

    def _monthly_buckets(self):
        return CreditBucket.objects.filter(
            wallet=self.wallet, bucket_type=CreditBucketType.MONTHLY
        ).order_by("created_at")

    def assertCloseTo(self, actual, expected, msg=""):
        self.assertIsNotNone(actual, f"{msg} (was None)")
        delta = abs((actual - expected).total_seconds())
        self.assertLessEqual(
            delta,
            TOLERANCE_SECONDS,
            f"{msg}: expected ~{expected.isoformat()}, got {actual.isoformat()} "
            f"({delta:.0f}s apart)",
        )


class StripeFinalizedAnnualConversionTests(TrialConversionBase):
    """Path (a) — Stripe charges at trial end and the webhook finalizes."""

    def test_grant_date_is_persisted_and_bucket_expires_monthly(self):
        trial = self._make_trial(self.annual_plan)
        start = timezone.now()
        end = start + relativedelta(years=1)

        SubscriptionService.finalize_trial_conversion_via_stripe(
            trial, period_start=start, period_end=end
        )

        converted = UserSubscription.objects.get(pk=trial.pk)
        self.assertCloseTo(
            converted.next_credit_grant_at,
            start + relativedelta(months=1),
            "next_credit_grant_at must be one month out, not NULL — a NULL "
            "is excluded by process_annual_plan_credit_grants' SQL filter "
            "and the subscriber never receives another credit",
        )
        self.assertCloseTo(converted.billing_cycle_end, end, "billing_cycle_end")

        bucket = self._monthly_buckets().last()
        self.assertCloseTo(
            bucket.expires_at,
            start + relativedelta(months=1),
            "the MONTHLY bucket must expire in a MONTH; expiring at the "
            "annual billing_cycle_end stretches one month's credits over a "
            "whole year",
        )

    def test_subscriber_receives_credits_in_every_month_of_the_year(self):
        """The end-to-end consequence: drive the real Celery task through
        eleven further months and require twelve monthly grants."""
        trial = self._make_trial(self.annual_plan)
        start = timezone.now()
        SubscriptionService.finalize_trial_conversion_via_stripe(
            trial,
            period_start=start,
            period_end=start + relativedelta(years=1),
        )

        self.assertEqual(self._monthly_buckets().count(), 1)

        for month in range(1, 12):
            subscription = UserSubscription.objects.get(pk=trial.pk)
            grant_moment = subscription.next_credit_grant_at
            self.assertIsNotNone(
                grant_moment,
                f"month {month}: next_credit_grant_at became NULL, so the "
                "annual grant task can never pick this subscription up again",
            )
            with patch("django.utils.timezone.now", return_value=grant_moment):
                process_annual_plan_credit_grants()

            self.assertEqual(
                self._monthly_buckets().count(),
                month + 1,
                f"month {month}: expected a fresh monthly credit grant",
            )

        self.assertEqual(
            self._monthly_buckets().count(),
            12,
            "an annual subscriber must receive twelve monthly credit grants",
        )
        for bucket in self._monthly_buckets():
            self.assertEqual(bucket.total_credits, MONTHLY_CREDITS)

    def test_monthly_plan_conversion_is_unchanged(self):
        """Non-regression: _resolve_billing_period returns
        grant_at == period_end for MONTHLY, so the swap is inert there."""
        trial = self._make_trial(self.monthly_plan)
        start = timezone.now()
        end = start + relativedelta(months=1)

        SubscriptionService.finalize_trial_conversion_via_stripe(
            trial, period_start=start, period_end=end
        )

        converted = UserSubscription.objects.get(pk=trial.pk)
        self.assertCloseTo(converted.billing_cycle_end, end, "billing_cycle_end")
        self.assertCloseTo(
            converted.next_credit_grant_at,
            end,
            "for a monthly plan the grant date is the cycle end",
        )
        self.assertCloseTo(
            self._monthly_buckets().last().expires_at, end, "bucket expiry"
        )


class CheckoutConversionTests(TrialConversionBase):
    """Path (b) — the user converts mid-trial via checkout."""

    def test_grant_date_survives_the_save(self):
        """MUST re-read from the DB. The in-memory object carries the
        value; only a reload exposes the dropped update_fields entry."""
        trial = self._make_trial(self.annual_plan)

        SubscriptionService.finalize_trial_to_paid_conversion(
            trial, self.annual_plan, "sub_checkout_annual"
        )

        reloaded = UserSubscription.objects.get(pk=trial.pk)
        self.assertIsNotNone(
            reloaded.next_credit_grant_at,
            "next_credit_grant_at was assigned but omitted from "
            "save(update_fields=[...]), so it never reached the database",
        )
        self.assertCloseTo(
            reloaded.next_credit_grant_at,
            timezone.now() + relativedelta(months=1),
            "grant date should be one month out for an annual plan",
        )

    def test_annual_grant_task_can_find_the_converted_subscription(self):
        trial = self._make_trial(self.annual_plan)
        SubscriptionService.finalize_trial_to_paid_conversion(
            trial, self.annual_plan, "sub_checkout_annual_2"
        )

        before = self._monthly_buckets().count()
        subscription = UserSubscription.objects.get(pk=trial.pk)

        with patch(
            "django.utils.timezone.now",
            return_value=subscription.next_credit_grant_at,
        ):
            process_annual_plan_credit_grants()

        self.assertEqual(
            self._monthly_buckets().count(),
            before + 1,
            "the annual grant task did not find the converted subscription",
        )

    def test_monthly_plan_conversion_is_unchanged(self):
        trial = self._make_trial(self.monthly_plan)

        SubscriptionService.finalize_trial_to_paid_conversion(
            trial, self.monthly_plan, "sub_checkout_monthly"
        )

        reloaded = UserSubscription.objects.get(pk=trial.pk)
        self.assertCloseTo(
            reloaded.next_credit_grant_at,
            reloaded.billing_cycle_end,
            "for a monthly plan the grant date is the cycle end",
        )
