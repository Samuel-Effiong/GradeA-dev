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

billing.refresh_timing (the fix) is imported inside the tests that need it,
so this module also loads on the pre-fix tree, where the lost-rollover
test's first assertion shows the F6 query catching the real damage.
"""

import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import TYPE_CHECKING
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from billing.immutable import allow_unsafe_mutation
from billing.license_service import LicenseSubscriptionService
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
from billing.services import SubscriptionService
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
from billing.tests.tests_free_trial import make_individual_plan
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

LOST_MONTHS_SQL = (
    settings.BASE_DIR
    / "docs"
    / "evidence"
    / "monthly-rollover-cleanup-race"
    / "detect_monthly_rollovers_lost_to_cleanup.sql"
)


def detect_lost_rollovers():
    with connection.cursor() as cursor:
        cursor.execute(LOST_MONTHS_SQL.read_text())
        columns = [c.name for c in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


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
        self.step = self.STEP

    def __call__(self):
        self.moment += self.step
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

    def next_due(self):
        """The refreshed row's next due time; each path class defines it."""
        raise NotImplementedError

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

    def test_a_run_starting_a_little_earlier_than_last_months_still_refreshes(self):
        """Beat dispatch and worker pickup jitter from day to day: this
        month's run starts 2 s earlier than last month's did. The due
        tolerance keeps the row due on it."""
        self.use_month_1()
        before = self.monthly_buckets_granted()
        self.clock.at(
            DAY_0 + relativedelta(months=1) + self.refresh_at - timedelta(seconds=2)
        )
        self.refresh_task()
        self.assertEqual(self.monthly_buckets_granted() - before, 1)

    def test_a_row_processed_late_in_a_slow_run_is_due_on_next_months_run(self):
        """A long batch: the row is processed minutes after the run started
        (the clock steps 4 minutes per call here). Its next due time comes
        from the run's start, not from when it was processed, so next
        month's run still finds it due."""
        self.use_month_1()
        self.clock.step = timedelta(minutes=4)
        self.run_refresh_on(DAY_0 + relativedelta(months=1))
        self.clock.step = AdvancingClock.STEP
        before = self.monthly_buckets_granted()
        self.run_refresh_on(DAY_0 + relativedelta(months=2))
        self.assertEqual(self.monthly_buckets_granted() - before, 1)

    def test_the_monthly_bucket_outlives_its_due_time_by_the_grace(self):
        from billing.refresh_timing import MONTHLY_BUCKET_GRACE

        [bucket] = self.live_monthly()
        self.assertEqual(bucket.expires_at, self.next_due() + MONTHLY_BUCKET_GRACE)

    def test_month_1s_unused_credits_roll_over(self):
        month_1, _, _ = self.drive_month_2()
        # First, so that on the pre-fix code the failure shows what the F6
        # query finds on the real lost rollover.
        self.assertEqual(detect_lost_rollovers(), [])
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

    def next_due(self):
        return UserSubscription.objects.get(user=self.user).next_credit_grant_at

    def test_the_lost_months_query_finds_a_write_off_before_the_refresh(self):
        """The ledger exactly as the pre-fix code left it: the cleanup wrote
        month 1 off while its refresh was owed (here by making the owner
        briefly not entitled, the only way the fixed cleanup still expires
        a monthly bucket), and the refresh came the next day."""
        self.write_off_month_1_before_its_refresh()

        [row] = detect_lost_rollovers()
        self.assertEqual(row["wallet_id"], self.wallet.pk)
        self.assertEqual(row["user_id"], self.user.pk)
        self.assertEqual(row["months_lost"], 1)
        self.assertEqual(
            row["unused_credits_written_off_raw"], MONTHLY_CREDITS - USED_IN_MONTH_1
        )
        # The plan's carry-over applied to it, as the rollover would have.
        self.assertEqual(row["plan_id"], self.plan.pk)
        self.assertEqual(row["carry_over_percent"], CARRY_PERCENT)
        self.assertEqual(row["estimated_carry_over_lost_raw"], EXPECTED_ROLLOVER)
        self.assertNotIn("@", " ".join(str(v) for v in row.values()))

    def test_the_lost_months_query_leaves_out_a_plan_with_no_carry_over(self):
        """1a's Q4: nothing was lost on a plan with no carry-over."""
        SubscriptionPlan.objects.filter(pk=self.plan.pk).update(carry_over_percent=0)
        self.write_off_month_1_before_its_refresh()
        self.assertEqual(detect_lost_rollovers(), [])

    def write_off_month_1_before_its_refresh(self):
        month_1 = self.use_month_1()
        day = DAY_0 + relativedelta(months=1)
        UserSubscription.objects.filter(user=self.user).update(is_active=False)
        self.clock.at(month_1.expires_at + timedelta(minutes=1))
        cleanup_expired_credit_buckets()
        UserSubscription.objects.filter(user=self.user).update(is_active=True)
        self.run_refresh_on(day + timedelta(days=3))

    def test_no_grant_for_the_last_minutes_of_the_contract(self):
        """1a's F2. The last grant set a due time 2 minutes BEFORE the cycle
        ends (a contract starting a few minutes after 02:00 UTC), so it is
        not capped; the anniversary run starts 4 minutes before the end. A
        contract ending within the tolerance counts as ended."""
        sub = UserSubscription.objects.get(user=self.user)
        end = sub.billing_cycle_end
        UserSubscription.objects.filter(pk=sub.pk).update(
            next_credit_grant_at=end - timedelta(minutes=2)
        )
        self.clock.at(end - timedelta(minutes=4))
        before = self.monthly_buckets_granted()
        summary = self.refresh_task()
        self.assertEqual(
            self.monthly_buckets_granted() - before,
            0,
            "a full month's credits granted for the contract's last minutes",
        )
        # Not even selected (the service's own re-check is tested below).
        self.assertIn("0 granted, 0 already granted", summary)

    def test_the_service_refuses_the_last_minutes_of_the_contract(self):
        sub = UserSubscription.objects.get(user=self.user)
        end = sub.billing_cycle_end
        UserSubscription.objects.filter(pk=sub.pk).update(
            next_credit_grant_at=end - timedelta(minutes=2)
        )
        almost = end - timedelta(minutes=4)
        self.clock.at(almost)
        self.assertIsNone(
            SubscriptionService.process_mid_cycle_credit_grant(sub, now=almost)
        )

    def test_a_due_time_capped_at_the_cycle_end_is_not_granted_early(self):
        """The tolerance must not grant the renewal's month mid-cycle."""
        sub = UserSubscription.objects.get(user=self.user)
        UserSubscription.objects.filter(pk=sub.pk).update(
            next_credit_grant_at=sub.billing_cycle_end
        )
        self.clock.at(sub.billing_cycle_end - timedelta(minutes=3))
        self.assertIn(
            "0 granted, 0 already granted", process_annual_plan_credit_grants()
        )

    def test_the_service_refuses_a_due_time_capped_at_the_cycle_end(self):
        sub = UserSubscription.objects.get(user=self.user)
        UserSubscription.objects.filter(pk=sub.pk).update(
            next_credit_grant_at=sub.billing_cycle_end
        )
        almost = sub.billing_cycle_end - timedelta(minutes=3)
        self.clock.at(almost)
        self.assertIsNone(
            SubscriptionService.process_mid_cycle_credit_grant(sub, now=almost)
        )


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

    def next_due(self):
        return SchoolCreditAllocation.objects.get(user=self.user).next_credit_grant_at

    def run_with_last_months_usage(self, offset):
        """1a's F1. The licence used a whole seat's month in month 1; this
        month's run starts `offset` from last month's. The refresh must
        reopen the licence's consumption window with it, or last month's
        usage caps this month's new-teacher enrolments
        (_enroll_teacher_internal)."""
        licence = LicenseSubscription.objects.get()
        opened = licence.consumption_window_start
        self.assertIsNotNone(opened, "month 1 opened no window")
        LicenseSubscription.objects.filter(pk=licence.pk).update(
            total_credits_consumed=MONTHLY_CREDITS
        )
        before = self.monthly_buckets_granted()
        self.clock.at(DAY_0 + relativedelta(months=1) + self.refresh_at + offset)
        self.refresh_task()
        licence.refresh_from_db()
        self.assertEqual(self.monthly_buckets_granted() - before, 1)
        self.assertEqual(
            licence.total_credits_consumed,
            0,
            "refreshed, but last month's usage still counts against this month",
        )
        reopened = licence.consumption_window_start
        assert reopened is not None and opened is not None
        self.assertGreater(reopened, opened)

    def test_a_run_2s_early_reopens_the_consumption_window(self):
        self.run_with_last_months_usage(-timedelta(seconds=2))

    def test_a_run_2s_late_reopens_the_consumption_window(self):
        self.run_with_last_months_usage(timedelta(seconds=2))

    def test_no_refresh_for_the_last_minutes_of_the_contract(self):
        """1a's F2, the licence path."""
        end = LicenseSubscription.objects.get().billing_cycle_end
        SchoolCreditAllocation.objects.filter(user=self.user).update(
            next_credit_grant_at=end - timedelta(minutes=2)
        )
        self.clock.at(end - timedelta(minutes=4))
        before = self.monthly_buckets_granted()
        self.refresh_task()
        self.assertEqual(self.monthly_buckets_granted() - before, 0)


class CleanupKeepsOwedMonthlyBucketsTests(TestCase):
    """Defence 3: the cleanup never writes off a monthly bucket its owner's
    refresh or renewal still has to roll over."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="owed-refresh@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.wallet = clear_signal_state(self.user)
        self.plan = make_individual_plan()
        now = timezone.now()
        # A monthly plan whose cycle ended an hour ago: its renewal (the
        # Stripe webhook, or the 04:00 reconcile) hasn't arrived yet.
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            billing_cycle_start=now - relativedelta(months=1, hours=1),
            billing_cycle_end=now - timedelta(hours=1),
            next_credit_grant_at=now - timedelta(hours=1),
        )
        self.bucket = self.monthly(expired_ago=timedelta(hours=1))

    def monthly(self, expired_ago, used=4_000_000):
        return CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=self.plan.monthly_credits,
            used_credits=used,
            expires_at=timezone.now() - expired_ago,
        )

    def written_off(self, bucket):
        bucket.refresh_from_db()
        return (
            bucket.is_processed
            or CreditLedger.objects.filter(
                bucket=bucket, ledger_type=CreditLedgerType.EXPIRE
            ).exists()
        )

    def test_an_entitled_owners_newest_monthly_bucket_is_kept(self):
        summary = cleanup_expired_credit_buckets()
        self.assertFalse(self.written_off(self.bucket))
        self.assertIn("1 monthly buckets kept for an owed refresh", summary)

    def test_the_renewal_that_arrives_after_the_cleanup_still_rolls_it_over(self):
        """The monthly individual renewal path: the webhook after 05:00."""
        cleanup_expired_credit_buckets()
        SubscriptionService.process_rollover_and_renewal(self.sub)

        self.bucket.refresh_from_db()
        self.assertTrue(self.bucket.is_processed)
        self.assertFalse(
            CreditLedger.objects.filter(
                bucket=self.bucket, ledger_type=CreditLedgerType.EXPIRE
            ).exists()
        )
        self.assertEqual(
            CreditBucket.objects.filter(
                wallet=self.wallet, bucket_type=CreditBucketType.CARRY_OVER
            ).count(),
            1,
        )

    def test_it_is_written_off_once_the_owner_is_no_longer_entitled(self):
        UserSubscription.objects.filter(pk=self.sub.pk).update(is_active=False)
        cleanup_expired_credit_buckets()
        self.assertTrue(self.written_off(self.bucket))

    def test_an_older_unprocessed_monthly_bucket_is_still_written_off(self):
        older = self.monthly(expired_ago=timedelta(days=40))
        CreditBucket.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - timedelta(days=70)
        )
        cleanup_expired_credit_buckets()
        self.assertTrue(self.written_off(older))
        self.assertFalse(self.written_off(self.bucket))

    def test_a_licence_teacher_is_entitled_too(self):
        UserSubscription.objects.filter(pk=self.sub.pk).delete()
        school = School.objects.create(name="Owed High")
        admin = CustomUser.objects.create_user(
            email="owed-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        licence = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=self.plan,
            contract_months=12,
            max_seats=1,
            billing_cycle_start=timezone.now() - relativedelta(months=2),
            billing_cycle_end=timezone.now() + relativedelta(months=10),
            is_active=True,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=self.user,
            monthly_allocation=self.plan.monthly_credits,
            is_active=True,
            next_credit_grant_at=timezone.now() - timedelta(hours=1),
        )
        cleanup_expired_credit_buckets()
        self.assertFalse(self.written_off(self.bucket))

    def test_other_bucket_types_are_still_written_off(self):
        carry = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.CARRY_OVER,
            total_credits=1_000,
            used_credits=0,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        cleanup_expired_credit_buckets()
        self.assertTrue(self.written_off(carry))

    # -- 1a's licence edges (Q2): no longer entitled means written off ----

    def licence_for(self, *, licence_active=True, allocation_active=True):
        UserSubscription.objects.filter(pk=self.sub.pk).delete()
        school = School.objects.create(name="Edge High")
        admin = CustomUser.objects.create_user(
            email="edge-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        licence = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=self.plan,
            contract_months=12,
            max_seats=1,
            billing_cycle_start=timezone.now() - relativedelta(months=2),
            billing_cycle_end=timezone.now() + relativedelta(months=10),
            is_active=licence_active,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=self.user,
            monthly_allocation=self.plan.monthly_credits,
            is_active=allocation_active,
            next_credit_grant_at=timezone.now() - timedelta(hours=1),
        )
        return licence

    def test_a_deactivated_licences_teacher_is_written_off(self):
        self.licence_for(licence_active=False)
        cleanup_expired_credit_buckets()
        self.assertTrue(self.written_off(self.bucket))

    def test_an_inactive_allocation_is_written_off(self):
        self.licence_for(allocation_active=False)
        cleanup_expired_credit_buckets()
        self.assertTrue(self.written_off(self.bucket))

    def test_a_teacher_removed_through_the_real_path_is_written_off(self):
        licence = self.licence_for()
        live = self.monthly(expired_ago=-timedelta(days=20))
        with patch("users.tasks.sync_user_to_mailerlite.delay"):
            LicenseSubscriptionService.remove_teacher_from_license(licence, self.user)
        cleanup_expired_credit_buckets()
        self.assertTrue(self.written_off(live), "the removed teacher's bucket kept")
        self.assertTrue(self.written_off(self.bucket))

    def test_a_kept_bucket_is_not_spendable(self):
        cleanup_expired_credit_buckets()
        self.assertFalse(self.written_off(self.bucket))
        self.assertEqual(self.wallet.total_remaining_credits(), 0)

    def test_a_lapsed_subscriber_is_written_off_on_the_next_cleanup(self):
        cleanup_expired_credit_buckets()
        self.assertFalse(self.written_off(self.bucket))
        UserSubscription.objects.filter(pk=self.sub.pk).update(is_active=False)
        cleanup_expired_credit_buckets()
        self.assertTrue(self.written_off(self.bucket))

    def test_a_refresh_overdue_by_a_week_is_logged_and_still_kept(self):
        from billing.refresh_timing import OWED_REFRESH_OVERDUE

        CreditBucket.objects.filter(pk=self.bucket.pk).update(
            expires_at=timezone.now() - OWED_REFRESH_OVERDUE - timedelta(hours=1)
        )
        with self.assertLogs("billing.tasks", "ERROR") as logs:
            cleanup_expired_credit_buckets()
        self.assertFalse(self.written_off(self.bucket))
        self.assertIn(str(self.bucket.id), logs.output[0])
        self.assertNotIn("@", logs.output[0])


class FirstMonthGraceTests(TestCase):
    """Defence 2 for the first month: an annual activation's monthly bucket
    outlives the first grant's due time; a monthly plan's is unchanged."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="first-month@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        clear_signal_state(self.user)

    def monthly_bucket(self):
        return CreditBucket.objects.get(
            wallet__user=self.user, bucket_type=CreditBucketType.MONTHLY
        )

    def test_an_annual_activation_gets_the_grace(self):
        from billing.refresh_timing import MONTHLY_BUCKET_GRACE

        sub = SubscriptionService.activate_subscription(self.user, make_annual_plan())
        self.assertEqual(
            self.monthly_bucket().expires_at,
            sub.next_credit_grant_at + MONTHLY_BUCKET_GRACE,
        )

    def test_an_annual_immediate_plan_change_gets_the_grace(self):
        from billing.refresh_timing import MONTHLY_BUCKET_GRACE

        sub = SubscriptionService.activate_subscription(self.user, make_annual_plan())
        bigger = SubscriptionPlan.objects.create(
            name=PlanType.PRO_ANNUAL,
            display_name="Pro Annual",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            interval=BillingInterval.ANNUAL,
            price_cents=149_900,
            monthly_credits=MONTHLY_CREDITS * 2,
            carry_over_percent=CARRY_PERCENT,
            carry_over_expiry_months=6,
            max_bank=None,
            is_active=True,
        )
        SubscriptionService.apply_immediate_plan_change(sub, bigger)

        sub.refresh_from_db()
        # The new plan's bucket. (The plan change retires the old one by
        # expiring it without marking it processed: noted in EVIDENCE.)
        bucket = CreditBucket.objects.filter(
            wallet__user=self.user, bucket_type=CreditBucketType.MONTHLY
        ).latest("created_at")
        self.assertEqual(bucket.total_credits, MONTHLY_CREDITS * 2)
        self.assertEqual(
            bucket.expires_at, sub.next_credit_grant_at + MONTHLY_BUCKET_GRACE
        )

    def test_a_monthly_activation_is_unchanged(self):
        sub = SubscriptionService.activate_subscription(
            self.user, make_individual_plan()
        )
        self.assertEqual(self.monthly_bucket().expires_at, sub.billing_cycle_end)


class LostMonthsQueryIsReadOnlyTests(SimpleTestCase):
    def test_it_writes_nothing(self):
        sql = re.sub(r"--[^\n]*", "", LOST_MONTHS_SQL.read_text()).upper()
        for verb in ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "TRUNCATE"):
            self.assertNotRegex(sql, rf"\b{verb}\b", verb)
