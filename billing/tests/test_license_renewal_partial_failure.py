"""
billing/tests/test_license_renewal_partial_failure.py
=====================================================
Per-teacher transaction boundaries in the two license renewal loops
(`process_license_renewal` for Stripe-billed licences,
`process_offline_renewal` for offline ones).

WHAT WAS WRONG
--------------
Both loops were written as::

    with transaction.atomic():
        try:
            ...grant credits...
        except Exception:
            log and continue

Catching the exception INSIDE the atomic block means it never reaches
`atomic.__exit__`, so Django sees a clean exit and COMMITS the savepoint.
The failed teacher's partial writes therefore survived.

That is not a cosmetic ordering issue.
`_rollover_and_grant_monthly_bucket` does its work in this order:

    1. create the CARRY_OVER bucket + its GRANT ledger row
    2. retire the old MONTHLY bucket (expires_at=now, is_processed=True)
    3. create the new MONTHLY bucket + its GRANT ledger row

A failure between (2) and (3) left the teacher with their old bucket
retired and no replacement — short an entire cycle's credits — while the
licence still advanced `billing_cycle_end`, so no later sweep ever
revisited them. They appeared only as a name in a log line.

The fix moves `try` outside `atomic()`, which is the idiom already used
correctly elsewhere in this app (billing/views.py, billing/tasks.py).
Django explicitly supports catching an exception raised by an inner
atomic block: the savepoint is rolled back and the outer transaction
stays usable.

WHY TransactionTestCase
-----------------------
Savepoint release/rollback is what is under test, so these need real
transaction semantics rather than the single outer transaction a
TestCase wraps every test in.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

#: Captured before any patching so the failure injector can delegate to the
#: real implementation for the teachers it is not sabotaging.
REAL_ROLLOVER = LicenseSubscriptionService._rollover_and_grant_monthly_bucket

MONTHLY_ALLOCATION = 10_000
SENTINEL_CARRY_TOTAL = 777_777


class LicenseRenewalTransactionBoundaryTests(TransactionTestCase):
    def setUp(self):
        self.school = School.objects.create(name="Renewal Boundary School")
        self.admin = CustomUser.objects.create_user(
            email="boundary.admin@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.STANDARD,
            monthly_credits=MONTHLY_ALLOCATION,
            is_active=True,
            carry_over_percent=100,
            carry_over_max=10**9,
            carry_over_expiry_months=1,
            max_bank=10**9,
        )
        now = timezone.now()
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            contract_months=1,
            max_seats=10,
            is_active=True,
            auto_renew=True,
            billing_method=LicenseBillingMethod.STRIPE,
            billing_cycle_start=now - timedelta(days=40),
            billing_cycle_end=now - timedelta(days=1),
        )
        self.teachers = [self._enrol(index) for index in range(2)]

    def _enrol(self, index):
        teacher = CustomUser.objects.create_user(
            email=f"boundary.t{index}@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
            is_active=True,
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
        SchoolCreditAllocation.objects.create(
            license_subscription=self.license,
            user=teacher,
            monthly_allocation=MONTHLY_ALLOCATION,
            is_active=True,
            next_credit_grant_at=timezone.now(),
        )
        # A live bucket with unused credits, so renewal has something real to
        # roll over rather than taking the trivial no-op path.
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=MONTHLY_ALLOCATION,
            used_credits=4_000,
            expires_at=timezone.now() + timedelta(days=1),
            is_processed=False,
        )
        return teacher

    def _buckets(self, teacher, bucket_type):
        return CreditBucket.objects.filter(
            wallet__user=teacher, bucket_type=bucket_type
        )

    def _failing_rollover(self, victim_id):
        """
        Fails a specific teacher AFTER real billing writes have landed,
        reproducing the audited scenario rather than failing before any
        work is done (which would prove nothing about rollback).
        """

        def _rollover(*, teacher, wallet, **kwargs):
            if teacher.id == victim_id:
                CreditBucket.objects.create(
                    wallet=wallet,
                    bucket_type=CreditBucketType.CARRY_OVER,
                    total_credits=SENTINEL_CARRY_TOTAL,
                    used_credits=0,
                    expires_at=timezone.now() + timedelta(days=30),
                )
                CreditLedger.record(
                    user=teacher,
                    bucket=None,
                    ledger_type=CreditLedgerType.GRANT,
                    amount=SENTINEL_CARRY_TOTAL,
                    reference="sentinel partial write",
                )
                raise ValueError("simulated failure part-way through the grant")
            return REAL_ROLLOVER(teacher=teacher, wallet=wallet, **kwargs)

        return _rollover

    # ---------------- Test A: the successful path still works -------------

    def test_successful_renewal_grants_credits_and_advances_the_cycle(self):
        LicenseSubscriptionService.process_license_renewal(self.license)

        for teacher in self.teachers:
            live_monthly = self._buckets(teacher, CreditBucketType.MONTHLY).filter(
                is_processed=False
            )
            self.assertEqual(live_monthly.count(), 1)
            self.assertEqual(live_monthly.first().total_credits, MONTHLY_ALLOCATION)

            # 6,000 unused at 100% carry_over_percent.
            carry = self._buckets(teacher, CreditBucketType.CARRY_OVER)
            self.assertEqual(carry.count(), 1)
            self.assertEqual(carry.first().total_credits, 6_000)

            # The old bucket is retired so it cannot be double-counted later.
            retired = self._buckets(teacher, CreditBucketType.MONTHLY).filter(
                is_processed=True
            )
            self.assertEqual(retired.count(), 1)

            self.assertTrue(
                CreditLedger.objects.filter(
                    user_id=teacher.id, amount=MONTHLY_ALLOCATION
                ).exists()
            )

        self.license.refresh_from_db()
        self.assertGreater(self.license.billing_cycle_end, timezone.now())

    # ---------------- Test B: a failed teacher rolls back fully -----------

    def test_a_failed_teacher_leaves_no_partial_writes_behind(self):
        victim = self.teachers[1]
        ledger_before = CreditLedger.objects.filter(user_id=victim.id).count()

        with patch.object(
            LicenseSubscriptionService,
            "_rollover_and_grant_monthly_bucket",
            self._failing_rollover(victim.id),
        ):
            LicenseSubscriptionService.process_license_renewal(self.license)

        # Nothing the failed teacher's savepoint wrote may survive.
        self.assertFalse(
            CreditBucket.objects.filter(
                wallet__user=victim, total_credits=SENTINEL_CARRY_TOTAL
            ).exists(),
            "the failed teacher's carry-over bucket was committed",
        )
        self.assertEqual(
            CreditLedger.objects.filter(user_id=victim.id).count(),
            ledger_before,
            "the failed teacher's ledger rows were committed",
        )
        self.assertEqual(self._buckets(victim, CreditBucketType.CARRY_OVER).count(), 0)

        # Their original bucket must still be live — NOT retired — so they
        # keep the credits they already had.
        victim_monthly = self._buckets(victim, CreditBucketType.MONTHLY)
        self.assertEqual(victim_monthly.count(), 1)
        self.assertFalse(victim_monthly.first().is_processed)

    def test_a_failed_teacher_does_not_stop_the_others_renewing(self):
        victim, survivor = self.teachers[1], self.teachers[0]

        with patch.object(
            LicenseSubscriptionService,
            "_rollover_and_grant_monthly_bucket",
            self._failing_rollover(victim.id),
        ):
            LicenseSubscriptionService.process_license_renewal(self.license)

        live = self._buckets(survivor, CreditBucketType.MONTHLY).filter(
            is_processed=False
        )
        self.assertEqual(live.count(), 1)
        self.assertEqual(live.first().total_credits, MONTHLY_ALLOCATION)
        self.assertEqual(
            self._buckets(survivor, CreditBucketType.CARRY_OVER).count(), 1
        )

    def test_a_failed_teacher_is_reported_and_not_counted_as_renewed(self):
        victim = self.teachers[1]

        with patch.object(
            LicenseSubscriptionService,
            "_rollover_and_grant_monthly_bucket",
            self._failing_rollover(victim.id),
        ):
            with self.assertLogs("billing.license_service", level="ERROR") as logs:
                LicenseSubscriptionService.process_license_renewal(self.license)

        self.assertTrue(
            any(victim.email in line for line in logs.output),
            "the failed teacher was not named in the error log",
        )


class OfflineRenewalTransactionBoundaryTests(TransactionTestCase):
    """The same boundary, on the offline renewal path."""

    def setUp(self):
        self.school = School.objects.create(name="Offline Boundary School")
        self.admin = CustomUser.objects.create_user(
            email="offline.admin@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        self.superadmin = CustomUser.objects.create_user(
            email="offline.super@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_active=True,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Offline Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.STANDARD,
            monthly_credits=MONTHLY_ALLOCATION,
            is_active=True,
            carry_over_percent=100,
            carry_over_max=10**9,
            carry_over_expiry_months=1,
            max_bank=10**9,
        )
        now = timezone.now()
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            contract_months=1,
            max_seats=10,
            is_active=True,
            auto_renew=True,
            billing_method=LicenseBillingMethod.OFFLINE,
            billing_cycle_start=now - timedelta(days=40),
            billing_cycle_end=now - timedelta(days=1),
        )
        self.teacher = CustomUser.objects.create_user(
            email="offline.teacher@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
            is_active=True,
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        SchoolCreditAllocation.objects.create(
            license_subscription=self.license,
            user=self.teacher,
            monthly_allocation=MONTHLY_ALLOCATION,
            is_active=True,
            next_credit_grant_at=now,
        )
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=MONTHLY_ALLOCATION,
            used_credits=4_000,
            expires_at=now + timedelta(days=1),
            is_processed=False,
        )

    def test_a_failed_teacher_leaves_no_partial_writes_behind(self):
        victim_id = self.teacher.id
        ledger_before = CreditLedger.objects.filter(user_id=victim_id).count()

        def _rollover(*, teacher, wallet, **kwargs):
            CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=CreditBucketType.CARRY_OVER,
                total_credits=SENTINEL_CARRY_TOTAL,
                used_credits=0,
                expires_at=timezone.now() + timedelta(days=30),
            )
            raise ValueError("simulated offline failure part-way through")

        with patch.object(
            LicenseSubscriptionService,
            "_rollover_and_grant_monthly_bucket",
            _rollover,
        ):
            LicenseSubscriptionService.process_offline_renewal(
                self.license,
                performed_by=self.superadmin,
                new_billing_cycle_end=timezone.now() + timedelta(days=30),
            )

        self.assertFalse(
            CreditBucket.objects.filter(
                wallet__user_id=victim_id, total_credits=SENTINEL_CARRY_TOTAL
            ).exists(),
            "the failed teacher's offline carry-over bucket was committed",
        )
        self.assertEqual(
            CreditLedger.objects.filter(user_id=victim_id).count(), ledger_before
        )
