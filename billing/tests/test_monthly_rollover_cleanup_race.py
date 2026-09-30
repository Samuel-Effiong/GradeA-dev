"""
billing/tests/test_monthly_rollover_cleanup_race.py
===================================================
A monthly credit refresh must happen on its scheduled run, and a month's
unused credits must roll over, not be expired by the 05:00 cleanup first.

THE SUSPECTED BUG (d5, 2026-09-30; reproduce-first)
---------------------------------------------------
Both monthly refresh paths set the next due time from the moment the
subscription was PROCESSED, `now + 1 month`:

  * annual plans: SubscriptionService.process_mid_cycle_credit_grant
    (Beat: process_annual_plan_credit_grants, 02:00);
  * licences: LicenseSubscriptionService._refresh_teacher_credits
    (Beat: process_license_monthly_credit_refreshes, 03:00).

The monthly bucket expires at that same moment. A month later the task
filters on its own START time, which is a little earlier than the moment
the subscription was processed last month, so the subscription isn't due
yet and waits for the next day's run. Meanwhile:

  * cleanup_expired_credit_buckets (05:00) finds the monthly bucket
    expired and unprocessed, writes it off (EXPIRE) and marks it processed;
  * so the next day's refresh finds no unprocessed monthly bucket and
    grants the new month WITHOUT the rollover of the old month's unused
    credits;
  * and for about a day the customer has no monthly bucket at all.

The clock here advances on every call, as a real one does: that's the
whole mechanism (with a frozen clock the processing moment and the task's
start coincide, and the bug can't show).

Beat times are taken from settings: grants 02:00, licence refreshes 03:00,
cleanup 05:00.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import TYPE_CHECKING
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase

from billing.immutable import allow_unsafe_mutation
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from billing.tasks import (
    cleanup_expired_credit_buckets,
    process_annual_plan_credit_grants,
    process_license_monthly_credit_refreshes,
)
from billing.tests.test_annual_mid_cycle_grants import (
    CARRY_PERCENT,
    MONTHLY_CREDITS,
    make_annual_plan,
)
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

#: A day the Beat schedule fires; the hour is set per task.
DAY_0 = datetime(2026, 3, 10, tzinfo=dt_timezone.utc)
GRANTS_AT = timedelta(hours=2)
LICENCE_REFRESH_AT = timedelta(hours=3)
CLEANUP_AT = timedelta(hours=5)

#: What each month leaves unused, so its rollover is a known number.
USED_IN_MONTH_1 = 6_000
EXPECTED_ROLLOVER = (MONTHLY_CREDITS - USED_IN_MONTH_1) * CARRY_PERCENT // 100


class AdvancingClock:
    """timezone.now() that moves forward on every call, like a real one."""

    STEP = timedelta(milliseconds=3)

    def __init__(self, start):
        self.moment = start

    def __call__(self):
        self.moment += self.STEP
        return self.moment

    def at(self, moment):
        self.moment = moment


def clear_signal_state(user):
    UserSubscription.objects.filter(user=user).delete()
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    wallet.buckets.all().delete()
    with allow_unsafe_mutation():
        CreditLedger.objects.filter(user_id=user.id).delete()
    return wallet


if TYPE_CHECKING:
    _MixinBase = TestCase
else:
    _MixinBase = object


class RefreshRaceFixture(_MixinBase):
    """Month 1's refresh runs through the real task at its scheduled time;
    then month 2 is driven day by day: the scheduled run, the 05:00
    cleanup, and the next day's run. A mixin: the two path classes below
    add TestCase."""

    wallet: CreditWallet
    refresh_task = staticmethod(lambda: None)
    refresh_at = timedelta()

    def setUp(self):
        super().setUp()
        self.clock = AdvancingClock(DAY_0)
        patcher = patch("django.utils.timezone.now", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_refresh_on(self, day):
        self.clock.at(day + self.refresh_at)
        return self.refresh_task()

    def run_cleanup_on(self, day):
        self.clock.at(day + CLEANUP_AT)
        cleanup_expired_credit_buckets()

    def live_monthly(self):
        return CreditBucket.objects.filter(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            is_processed=False,
            expires_at__gt=self.clock.moment,
        )

    def use_month_1(self):
        [bucket] = self.live_monthly()
        bucket.used_credits = USED_IN_MONTH_1
        bucket.save(update_fields=["used_credits"])
        return bucket

    def rollovers(self):
        return list(
            CreditBucket.objects.filter(
                wallet=self.wallet, bucket_type=CreditBucketType.CARRY_OVER
            ).values_list("total_credits", flat=True)
        )

    def expire_rows(self, bucket):
        return CreditLedger.objects.filter(
            bucket=bucket, ledger_type=CreditLedgerType.EXPIRE
        ).count()

    # -- the checks, shared by both paths --------------------------------

    def monthly_buckets_granted(self):
        return CreditBucket.objects.filter(
            wallet=self.wallet, bucket_type=CreditBucketType.MONTHLY
        ).count()

    def drive_month_2(self):
        month_1 = self.use_month_1()
        granted_before = self.monthly_buckets_granted()
        day = DAY_0 + relativedelta(months=1)
        self.run_refresh_on(day)
        granted_on_schedule = self.monthly_buckets_granted() - granted_before
        # An hour after the scheduled run, on the day the new month is due.
        self.clock.at(day + self.refresh_at + timedelta(hours=1))
        live_after_run = self.live_monthly().count()
        self.run_cleanup_on(day)
        self.run_refresh_on(day + timedelta(days=1))
        return month_1, granted_on_schedule, live_after_run

    def test_the_new_month_is_granted_on_its_scheduled_run(self):
        _, granted_on_schedule, _ = self.drive_month_2()
        self.assertEqual(
            granted_on_schedule,
            1,
            "month 2 was due on this run's day but the run found it not due "
            "yet (due a few milliseconds after the run started)",
        )

    def test_month_2_is_granted_exactly_once_in_total(self):
        self.drive_month_2()
        # Month 0's bucket, month 1's, month 2's: never a double grant.
        self.assertEqual(self.monthly_buckets_granted(), 3)

    def test_the_customer_has_a_monthly_bucket_after_the_scheduled_run(self):
        _, _, live_after_run = self.drive_month_2()
        self.assertEqual(
            live_after_run,
            1,
            "a day with no monthly credits: month 1 had expired and month 2 "
            "was not granted",
        )

    def test_month_1s_unused_credits_roll_over(self):
        month_1, _, _ = self.drive_month_2()
        self.assertEqual(self.rollovers(), [EXPECTED_ROLLOVER])
        self.assertEqual(
            self.expire_rows(month_1),
            0,
            "the 05:00 cleanup wrote month 1 off before the refresh rolled " "it over",
        )


class AnnualMidCycleGrantRaceTests(RefreshRaceFixture, TestCase):
    refresh_task = staticmethod(process_annual_plan_credit_grants)
    refresh_at = GRANTS_AT

    def setUp(self):
        super().setUp()
        self.clock.at(DAY_0 - relativedelta(months=1))
        self.plan = make_annual_plan()
        self.user = CustomUser.objects.create_user(
            email="annual-race@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.wallet = clear_signal_state(self.user)
        start = DAY_0 - relativedelta(months=1)
        UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=start,
            billing_cycle_end=start + relativedelta(years=1),
            # Month 1 is due exactly at the scheduled run.
            next_credit_grant_at=DAY_0 + GRANTS_AT,
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=MONTHLY_CREDITS,
            used_credits=MONTHLY_CREDITS,  # month 0 fully used: no rollover
            expires_at=DAY_0 + GRANTS_AT,
        )
        # Month 1, through the real task at its scheduled time.
        self.assertIn("1 granted", self.run_refresh_on(DAY_0))


class LicenceMonthlyRefreshRaceTests(RefreshRaceFixture, TestCase):
    refresh_task = staticmethod(process_license_monthly_credit_refreshes)
    refresh_at = LICENCE_REFRESH_AT

    def setUp(self):
        super().setUp()
        self.clock.at(DAY_0 - relativedelta(months=1))
        school = School.objects.create(name="Race High")
        plan = SubscriptionPlan.objects.create(
            name=PlanType.POWER_LICENSE,
            display_name="Power License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.POWER,
            interval=BillingInterval.MONTHLY,
            price_cents=19_900,
            monthly_credits=MONTHLY_CREDITS,
            carry_over_percent=CARRY_PERCENT,
            carry_over_expiry_months=6,
            is_active=True,
        )
        admin = CustomUser.objects.create_user(
            email="race-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        start = DAY_0 - relativedelta(months=1)
        licence = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=plan,
            contract_months=12,
            max_seats=2,
            billing_cycle_start=start,
            billing_cycle_end=start + relativedelta(months=12),
            is_active=True,
            auto_renew=True,
        )
        self.user = CustomUser.objects.create_user(
            email="race-teacher@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=school,
        )
        self.wallet = clear_signal_state(self.user)
        SchoolCreditAllocation.objects.filter(user=self.user).delete()
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=self.user,
            monthly_allocation=MONTHLY_CREDITS,
            is_active=True,
            next_credit_grant_at=DAY_0 + LICENCE_REFRESH_AT,
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=MONTHLY_CREDITS,
            used_credits=MONTHLY_CREDITS,
            expires_at=DAY_0 + LICENCE_REFRESH_AT,
        )
        self.run_refresh_on(DAY_0)
        self.assertEqual(self.live_monthly().count(), 1, "month 1 not granted")
