"""
billing/tests/test_plan_change_retires_old_bucket.py
====================================================
H-76: an immediate plan change must retire the old MONTHLY bucket the way
its three sibling rollovers do (activate_subscription's upgrade rollover,
process_rollover_and_renewal, the licence rollover): expires_at = now AND
is_processed = True.

apply_immediate_plan_change set only expires_at. The bucket then looked,
to cleanup_expired_credit_buckets (05:00), like one that had simply
expired, and the cleanup wrote an EXPIRE row for its unused credits,
which the plan change had already rolled over into a CARRY_OVER bucket.
The ledger counted the same credits twice (once carried over, once
expired). Balances were not affected: the bucket had already expired.

Found by d5 while building the monthly rollover fix (2026-09-30).

The same defect at the sibling site (1a's verification, N1):
LicenseSubscriptionService._enroll_teacher_internal rolls a former
individual subscriber's still-live MONTHLY bucket into CARRY_OVER when
they join a licence, then retired it with expires_at = now only, and the
cleanup expired those credits again. 1a's probe E1 is the test below.
"""

from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.immutable import allow_unsafe_mutation
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseSubscription,
    PlanTier,
    PlanType,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.tasks import cleanup_expired_credit_buckets
from billing.tests.test_monthly_rollover_cleanup_race import clear_signal_state
from billing.tests.tests_free_trial import make_individual_plan, make_license_plan
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

USED = 4_000_000


class PlanChangeRetiresOldBucketTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="plan-change@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        UserSubscription.objects.filter(user=self.user).delete()
        wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        wallet.buckets.all().delete()
        with allow_unsafe_mutation():
            CreditLedger.objects.filter(user_id=self.user.id).delete()

        self.sub = SubscriptionService.activate_subscription(
            self.user, make_individual_plan()
        )
        self.old = CreditBucket.objects.get(
            wallet__user=self.user, bucket_type=CreditBucketType.MONTHLY
        )
        self.old.used_credits = USED
        self.old.save(update_fields=["used_credits"])
        self.bigger = make_individual_plan(
            name=PlanType.PRO,
            display_name="Pro Grader",
            monthly_credits=20_000_000,
        )
        self.bigger.tier = PlanTier.PRO
        self.bigger.save(update_fields=["tier"])

        SubscriptionService.apply_immediate_plan_change(self.sub, self.bigger)
        self.old.refresh_from_db()

    def expire_rows(self):
        return CreditLedger.objects.filter(
            bucket=self.old, ledger_type=CreditLedgerType.EXPIRE
        )

    def test_the_old_bucket_is_retired_as_processed(self):
        self.assertTrue(self.old.is_processed)
        retired_at = self.old.expires_at
        assert retired_at is not None
        self.assertLessEqual(retired_at, timezone.now())

    def test_the_cleanup_does_not_expire_what_was_rolled_over(self):
        carried = CreditBucket.objects.get(
            wallet__user=self.user, bucket_type=CreditBucketType.CARRY_OVER
        )
        self.assertGreater(carried.total_credits, 0, "nothing was rolled over")

        later = timezone.now() + timedelta(hours=6)
        with patch("django.utils.timezone.now", return_value=later):
            cleanup_expired_credit_buckets()

        self.assertEqual(self.expire_rows().count(), 0)


class LicenceEnrolmentRetiresOldBucketTests(TestCase):
    """1a's probe E1: a former individual subscriber (the subscription no
    longer active, so enrolment is allowed) whose MONTHLY bucket is still
    live joins a school's licence."""

    def setUp(self):
        school = School.objects.create(name="Sibling High")
        admin = CustomUser.objects.create_user(
            email="h76-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        now = timezone.now()
        self.licence = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=make_license_plan(),
            contract_months=12,
            max_seats=0,  # unlimited: no budget cap in the way
            billing_cycle_start=now - relativedelta(months=1),
            billing_cycle_end=now + relativedelta(months=11),
            is_active=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="h76-teacher@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=school,
        )
        wallet = clear_signal_state(self.teacher)
        plan = make_individual_plan()
        # The individual plan ended, but its month's bucket is still live
        # for another 10 days.
        UserSubscription.objects.create(
            user=self.teacher,
            plan=plan,
            is_active=False,
            billing_cycle_start=now - relativedelta(days=20),
            billing_cycle_end=now + relativedelta(days=10),
        )
        self.old = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=plan.monthly_credits,
            used_credits=USED,
            expires_at=now + timedelta(days=10),
        )
        with patch("billing.license_service.transaction.on_commit"):
            LicenseSubscriptionService._enroll_teacher_internal(
                self.licence, self.teacher
            )
        self.old.refresh_from_db()

    def test_the_old_bucket_is_retired_as_processed(self):
        self.assertTrue(self.old.is_processed)
        retired_at = self.old.expires_at
        assert retired_at is not None
        self.assertLessEqual(retired_at, timezone.now())

    def test_the_cleanup_does_not_expire_what_was_rolled_over(self):
        carried = CreditLedger.objects.filter(
            user_id=self.teacher.id,
            ledger_type=CreditLedgerType.GRANT,
            reference__startswith="Rollover from previous subscription",
        )
        self.assertTrue(carried.exists(), "nothing was rolled over")

        later = timezone.now() + timedelta(hours=1)
        with patch("django.utils.timezone.now", return_value=later):
            cleanup_expired_credit_buckets()

        self.assertEqual(
            list(
                CreditLedger.objects.filter(
                    bucket=self.old, ledger_type=CreditLedgerType.EXPIRE
                ).values_list("amount", flat=True)
            ),
            [],
        )
