"""
H-222: the two functions that EXPIRE a bucket take the wallet row lock FIRST.

WHAT WAS WRONG (found by reading, 2026-10-09)
---------------------------------------------
`SubscriptionService.expire_bucket` (the Beat cleanup calls it per bucket, and
so does the licence clawback) took the bucket row FOR UPDATE and no wallet lock
at all; `LicenseSubscriptionService.remove_teacher_from_license` locked every
live bucket of the teacher the same way before it called `expire_bucket`. A
monthly grant or a charge for the same user holds the WALLET row and then waits
for the bucket (`billing/locks.py`: licence, wallet, bucket), so the two met
and Postgres aborted one transaction ("deadlock detected":
`audit.tests_background_attribution.ClawbackRaceTests`, alone, 3 of 3).

HOW THESE TESTS WORK
--------------------
The harness is H-181's (`billing.tests.test_wallet_lock_first.RaceTestCase`):
worker 0 runs the function under test; worker 1 runs a charge, which takes the
wallet and then the bucket. In the ORDER mode worker 0 stops at the entry of the
wallet-lock helper. A helper call placed after the bucket lock leaves worker 0
holding the bucket there, the charge takes the wallet and waits for that bucket,
and the helper's own lock deadlocks. A function that never calls the helper
never reaches the hook at all ("the hook was not reached once").

Run with:
    python manage.py test billing.tests.test_wallet_lock_first_expiry
"""

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.services import SubscriptionService
from billing.tests.test_wallet_lock_first import CustomUser, RaceTestCase
from classrooms.models import School
from users.models import UserTypes

# The functions queue mail-list syncs; with TransactionTestCase the
# after-commit callbacks really run, so the queueing is stubbed once for the
# module, from the main thread (as H-181's module does).
_queueing = [
    patch("billing.services.queue_sync", return_value=None),
    patch("billing.services.safe_delay", return_value=None),
    patch("billing.license_service.queue_sync", return_value=None),
    patch("billing.license_service.safe_delay", return_value=None),
]


def setUpModule():
    for stub in _queueing:
        stub.start()


def tearDownModule():
    for stub in _queueing:
        stub.stop()


class ExpiryLocksTheWalletFirst(RaceTestCase):
    def give_monthly_bucket(self):
        return CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=12_000_000,
            used_credits=3_000_000,
            expires_at=timezone.now() + timedelta(days=1),
        )

    def expire_rows(self, bucket):
        return CreditLedger.objects.filter(
            bucket_id=bucket.id, ledger_type=CreditLedgerType.EXPIRE
        ).count()

    def test_x1_expire_bucket_locks_the_wallet_before_the_bucket(self):
        bucket = self.give_monthly_bucket()

        results, errors, hook_calls = self.race(
            lambda: SubscriptionService.expire_bucket(bucket),
            before_wallet_lock_in="billing.services.lock_wallet_first",
        )

        self.assertNoDeadlock(errors, hook_calls)
        bucket.refresh_from_db()
        self.assertTrue(bucket.is_processed)
        self.assertEqual(self.expire_rows(bucket), 1)

    def test_x2_the_clawback_locks_the_wallet_before_its_buckets(self):
        school = School.objects.create(name="Expiry Lock First High")
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Expiry Lock First",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        admin = CustomUser.objects.create_user(
            email="expiry-lockfirst-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
        )
        licence = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.user.school = school
        self.user.save(update_fields=["school"])
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=self.user,
            monthly_allocation=1000,
            is_active=True,
        )
        bucket = self.give_monthly_bucket()

        results, errors, hook_calls = self.race(
            lambda: LicenseSubscriptionService.remove_teacher_from_license(
                licence, self.user
            ),
            before_wallet_lock_in="billing.license_service.lock_wallet_first",
        )

        self.assertNoDeadlock(errors, hook_calls)
        bucket.refresh_from_db()
        self.assertTrue(bucket.is_processed)
        self.assertEqual(self.expire_rows(bucket), 1)
