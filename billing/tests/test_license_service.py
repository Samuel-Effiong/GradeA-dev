"""
Tests for LicenseSubscriptionService

Validates all core functionality and edge cases.
"""

from datetime import datetime, timedelta

import pytest
from django.db import transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    CONVERSION_FACTOR,
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
)
from classrooms.models import School
from users.models import CustomUser, UserTypes


@pytest.mark.django_db
class TestLicenseSubscriptionServiceValidation(TestCase):
    """Tests for validation methods"""

    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Admin",
            last_name="User",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )

    def test_validate_license_plan_accepts_valid_plan(self):
        """Valid LICENSE plan should not raise exception"""
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Test License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        # Should not raise
        LicenseSubscriptionService.validate_license_plan(plan)

    def test_validate_license_plan_rejects_individual_category(self):
        """INDIVIDUAL category plan should raise ValueError"""
        plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Individual Plan",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
        )
        with pytest.raises(ValueError, match="category=INDIVIDUAL"):
            LicenseSubscriptionService.validate_license_plan(plan)

    def test_validate_license_plan_rejects_null_credits(self):
        """Plan with null monthly_credits should raise ValueError"""
        plan = SubscriptionPlan.objects.create(
            name=PlanType.CUSTOM,
            display_name="Custom License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.CUSTOM,
            monthly_credits=None,
        )
        with pytest.raises(ValueError, match="must define monthly_credits"):
            LicenseSubscriptionService.validate_license_plan(plan)

    def test_validate_admin_user_rejects_students(self):
        """Student users should not be allowed as admin"""
        student = CustomUser.objects.create_user(
            email="student@edu.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Student",
            last_name="User",
            user_type=UserTypes.STUDENT,
        )
        with pytest.raises(ValueError, match="Student users cannot manage"):
            LicenseSubscriptionService.validate_admin_user(student, self.school)

    def test_validate_admin_user_accepts_teachers(self):
        """Teachers should be allowed as admin"""
        teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        # Should not raise
        LicenseSubscriptionService.validate_admin_user(teacher, self.school)


@pytest.mark.django_db(transaction=True)
class TestLicenseCreation(TransactionTestCase):
    """Tests for creating license subscriptions"""

    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Admin",
            last_name="User",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Test License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            carry_over_percent=25,
            carry_over_max=5000,
            carry_over_expiry_months=1,
        )

    def test_create_license_subscription_basic(self):
        """Should create basic license subscription"""
        license_sub = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            admin_user=self.admin,
            max_seats=10,
        )

        assert license_sub.school == self.school
        assert license_sub.admin_user == self.admin
        assert license_sub.plan == self.plan
        assert license_sub.is_active is True
        assert license_sub.auto_renew is True
        assert license_sub.teacher_count == 0

    def test_create_license_subscription_with_teachers(self):
        """Should create license and enroll teachers"""
        teacher1 = CustomUser.objects.create_user(
            email="teacher1@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="One",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        teacher2 = CustomUser.objects.create_user(
            email="teacher2@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Two",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )

        teacher_emails = [teacher1.email, teacher2.email]
        license_sub = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            admin_user=self.admin,
            teacher_emails=teacher_emails,
            max_seats=10,
        )

        assert license_sub.teacher_count == 2

        # Verify allocations created
        alloc1 = SchoolCreditAllocation.objects.get(
            license_subscription=license_sub, user=teacher1
        )
        assert alloc1.monthly_allocation == self.plan.monthly_credits
        assert alloc1.is_active is True

        # Verify wallets created
        assert CreditWallet.objects.filter(user=teacher1).exists()
        assert CreditWallet.objects.filter(user=teacher2).exists()

        # Verify MONTHLY buckets created
        wallet1 = teacher1.credit_wallet
        monthly_bucket = CreditBucket.objects.get(
            wallet=wallet1, bucket_type=CreditBucketType.MONTHLY
        )
        assert monthly_bucket.total_credits == self.plan.monthly_credits
        assert monthly_bucket.used_credits == 0

    def test_create_license_rejects_teacher_with_active_individual_subscription(self):
        """A teacher with an active individual subscription should be
        SKIPPED (not auto-converted) when enrolling into a license —
        create_license_subscription itself must not raise, since
        _invite_and_enroll_one_teacher never raises; the failure is
        captured in _teacher_enrollment_results and the individual
        subscription is left untouched, requiring the admin to cancel it
        first."""
        from billing.models import UserSubscription

        teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )

        # Create individual subscription
        individual_plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Individual Plan",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
        )
        individual_sub = UserSubscription.objects.create(
            user=teacher,
            plan=individual_plan,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
        )

        # Now create license with teacher — should NOT raise.
        license_sub = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            admin_user=self.admin,
            teacher_emails=[teacher.email],
            max_seats=10,
        )

        # Individual subscription is untouched.
        individual_sub.refresh_from_db()
        assert individual_sub.is_active is True

        # Teacher was NOT enrolled under the license.
        assert not SchoolCreditAllocation.objects.filter(
            license_subscription=license_sub, user=teacher
        ).exists()

        results = license_sub._teacher_enrollment_results
        assert results["successful"] == 0
        assert results["failed"] == 1
        assert "active individual subscription" in results["errors"][0]["error"]

    def test_create_license_deactivates_previous_license(self):
        """Previous active license for school should be deactivated"""
        old_plan = SubscriptionPlan.objects.create(
            name=PlanType.POWER,
            display_name="Old License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.POWER,
            monthly_credits=30000,
        )
        old_license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=old_plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )

        # Create new license
        new_license = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            admin_user=self.admin,
            max_seats=10,
        )

        # Old license should be deactivated
        old_license.refresh_from_db()
        assert old_license.is_active is False
        assert new_license.is_active is True

    def test_create_license_carries_forward_active_teachers(self):
        """Active teachers under a replaced license should be carried
        forward to the new one with no invitation email, and should
        resolve billing access under the new license afterward."""
        from unittest.mock import patch

        from billing.subscription_resolver import (
            SOURCE_LICENSE_TEACHER,
            resolve_user_billing_context,
        )

        old_license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=10,
        )
        teacher1 = CustomUser.objects.create_user(
            email="teacher1@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="One",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        teacher2 = CustomUser.objects.create_user(
            email="teacher2@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Two",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        with transaction.atomic():
            LicenseSubscriptionService._enroll_teacher_internal(old_license, teacher1)
            LicenseSubscriptionService._enroll_teacher_internal(old_license, teacher2)

        with patch("billing.license_service.send_email_task") as mock_send_email:
            new_license = LicenseSubscriptionService.create_license_subscription(
                school=self.school,
                plan=self.plan,
                admin_user=self.admin,
                max_seats=10,
            )
            mock_send_email.delay.assert_not_called()

        for teacher in (teacher1, teacher2):
            assert SchoolCreditAllocation.objects.filter(
                license_subscription=new_license, user=teacher, is_active=True
            ).exists()
            context = resolve_user_billing_context(teacher)
            assert context.source == SOURCE_LICENSE_TEACHER
            assert context.license_subscription == new_license

        results = new_license._teacher_enrollment_results
        assert results["successful"] == 2
        assert results["failed"] == 0

    def test_create_license_carry_forward_dedupes_against_teacher_emails(self):
        """A teacher carried forward should not also be re-invited even
        if their email is re-listed in teacher_emails."""
        from unittest.mock import patch

        old_license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=10,
        )
        teacher_a = CustomUser.objects.create_user(
            email="teacher_a@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="A",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        with transaction.atomic():
            LicenseSubscriptionService._enroll_teacher_internal(old_license, teacher_a)

        with patch("billing.license_service.send_email_task") as mock_send_email:
            new_license = LicenseSubscriptionService.create_license_subscription(
                school=self.school,
                plan=self.plan,
                admin_user=self.admin,
                teacher_emails=["teacher_a@school.edu", "teacher_b@school.edu"],
                max_seats=10,
            )

            sent_recipients = [
                call.kwargs.get("recipient_list")
                for call in mock_send_email.delay.call_args_list
            ]
            flattened = [r for recipients in sent_recipients for r in recipients]
            assert "teacher_a@school.edu" not in flattened
            assert "teacher_b@school.edu" in flattened

        assert (
            SchoolCreditAllocation.objects.filter(
                license_subscription=new_license, user=teacher_a
            ).count()
            == 1
        )
        assert SchoolCreditAllocation.objects.filter(
            license_subscription=new_license,
            user__email="teacher_b@school.edu",
            is_active=True,
        ).exists()

    def test_create_license_carry_forward_opt_out(self):
        """carry_forward_teachers=False should leave old teachers off the
        new license entirely."""
        old_license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=10,
        )
        teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        with transaction.atomic():
            LicenseSubscriptionService._enroll_teacher_internal(old_license, teacher)

        new_license = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            admin_user=self.admin,
            max_seats=10,
            carry_forward_teachers=False,
        )

        assert not SchoolCreditAllocation.objects.filter(
            license_subscription=new_license, user=teacher
        ).exists()

    def test_create_license_carry_forward_seat_overflow_rejected(self):
        """Combined carried-forward + new teachers exceeding max_seats
        should reject the WHOLE creation, leaving the old license
        untouched (atomic rollback), not truncate the roster."""
        old_license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=10,
        )
        teacher1 = CustomUser.objects.create_user(
            email="teacher1@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="One",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        teacher2 = CustomUser.objects.create_user(
            email="teacher2@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Two",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        with transaction.atomic():
            LicenseSubscriptionService._enroll_teacher_internal(old_license, teacher1)
            LicenseSubscriptionService._enroll_teacher_internal(old_license, teacher2)

        with pytest.raises(ValueError, match="max_seats is 2"):
            LicenseSubscriptionService.create_license_subscription(
                school=self.school,
                plan=self.plan,
                admin_user=self.admin,
                teacher_emails=["new1@school.edu"],
                max_seats=2,
            )

        old_license.refresh_from_db()
        assert old_license.is_active is True
        assert (
            LicenseSubscription.objects.filter(
                school=self.school, is_active=True
            ).count()
            == 1
        )

    def test_create_license_carry_forward_cancels_pending_overage_requests(self):
        """A PENDING offline overage request against the old license
        should be auto-rejected, with the school admin notified, rather
        than left to fail confusingly later."""
        from unittest.mock import patch

        from billing.models import (
            LicenseOverageOfflineRequest,
            LicenseOverageOfflineRequestStatus,
        )

        old_license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=10,
        )
        pending_request = LicenseOverageOfflineRequest.objects.create(
            license_subscription=old_license,
            requested_by=self.admin,
            total_blocks=1,
            allocations={},
            block_size_snapshot=1000,
            unit_price_cents_snapshot=500,
            amount_cents_quoted=500,
        )

        with patch(
            "billing.license_service.LicenseSubscriptionService."
            "_notify_school_admin_offline_overage_rejected"
        ) as mock_notify:
            LicenseSubscriptionService.create_license_subscription(
                school=self.school,
                plan=self.plan,
                admin_user=self.admin,
                max_seats=10,
            )
            mock_notify.assert_called_once()

        pending_request.refresh_from_db()
        assert pending_request.status == LicenseOverageOfflineRequestStatus.REJECTED
        assert pending_request.rejection_reason
        assert pending_request.reviewed_by == self.admin

    def test_create_license_carry_forward_excludes_admin_allocation(self):
        """The old admin's own analytics-only allocation
        (is_admin_allocation=True) must never be treated as a teacher
        during carry-forward."""
        old_license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=10,
        )
        LicenseSubscriptionService._grant_admin_allocation(old_license)

        new_license = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            admin_user=self.admin,
            max_seats=10,
        )

        results = new_license._teacher_enrollment_results
        assert results["successful"] == 0
        assert not SchoolCreditAllocation.objects.filter(
            license_subscription=new_license,
            user=self.admin,
            is_admin_allocation=False,
        ).exists()

    def test_create_license_carry_forward_live_vs_expired_monthly_bucket(self):
        """A carried-forward teacher with a still-live MONTHLY bucket
        should get its unused balance rolled into CARRY_OVER; a teacher
        whose MONTHLY bucket already expired should get a fresh bucket
        with no rollover and no error."""
        old_license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=10,
        )
        teacher_live = CustomUser.objects.create_user(
            email="teacher_live@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Live",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        teacher_expired = CustomUser.objects.create_user(
            email="teacher_expired@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Expired",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        with transaction.atomic():
            LicenseSubscriptionService._enroll_teacher_internal(
                old_license, teacher_live
            )
            LicenseSubscriptionService._enroll_teacher_internal(
                old_license, teacher_expired
            )

        # Consume none, but force the second teacher's MONTHLY bucket
        # into the past so it's no longer "live" at carry-forward time.
        expired_bucket = CreditBucket.objects.get(
            wallet=teacher_expired.credit_wallet, bucket_type=CreditBucketType.MONTHLY
        )
        expired_bucket.expires_at = timezone.now() - timedelta(days=1)
        expired_bucket.save(update_fields=["expires_at"])

        new_license = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            admin_user=self.admin,
            max_seats=10,
        )

        wallet_live = teacher_live.credit_wallet
        assert CreditBucket.objects.filter(
            wallet=wallet_live, bucket_type=CreditBucketType.CARRY_OVER
        ).exists()

        wallet_expired = teacher_expired.credit_wallet
        assert not CreditBucket.objects.filter(
            wallet=wallet_expired, bucket_type=CreditBucketType.CARRY_OVER
        ).exists()
        assert CreditBucket.objects.filter(
            wallet=wallet_expired,
            bucket_type=CreditBucketType.MONTHLY,
            expires_at__gt=timezone.now(),
        ).exists()

        assert SchoolCreditAllocation.objects.filter(
            license_subscription=new_license, user=teacher_expired, is_active=True
        ).exists()


@pytest.mark.django_db(transaction=True)
class TestTeacherAllocation(TransactionTestCase):
    """Tests for teacher allocation management"""

    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Admin",
            last_name="User",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Test License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            carry_over_percent=25,
            carry_over_max=5000,
        )
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=10,
        )

    def test_add_single_teacher_creates_allocation(self):
        """Adding single teacher should create allocation"""
        teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )

        allocation = LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, teacher.email
        )

        assert allocation.license_subscription == self.license_sub
        assert allocation.user == teacher
        assert allocation.monthly_allocation == self.plan.monthly_credits
        assert allocation.is_active is True

    def test_add_teachers_batch_success(self):
        """Batch add should return success count"""
        teacher1 = CustomUser.objects.create_user(
            email="teacher1@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="One",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        teacher2 = CustomUser.objects.create_user(
            email="teacher2@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Two",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )

        results = LicenseSubscriptionService.add_teachers_batch(
            self.license_sub,
            [teacher1.email, teacher2.email],
        )

        assert results["successful"] == 2
        assert results["failed"] == 0
        assert len(results["errors"]) == 0

    def test_add_teachers_batch_invalid_email(self):
        """Batch add with a non-business email should handle gracefully"""
        valid_teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )

        results = LicenseSubscriptionService.add_teachers_batch(
            self.license_sub,
            [valid_teacher.email, "invalid@gmail.com"],
        )

        assert results["successful"] == 1
        assert results["failed"] == 1
        assert len(results["errors"]) == 1

    def test_remove_teacher_deactivates_allocation(self):
        """Removing teacher should deactivate allocation"""
        teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )

        allocation = LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, teacher.email
        )
        assert allocation.is_active is True

        LicenseSubscriptionService.remove_teacher_from_license(
            self.license_sub, teacher
        )

        allocation.refresh_from_db()
        assert allocation.is_active is False


@pytest.mark.django_db(transaction=True)
class TestLicenseRenewal(TransactionTestCase):
    """Tests for license renewal logic"""

    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Admin",
            last_name="User",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Test License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            carry_over_percent=25,
            carry_over_max=5000,
            carry_over_expiry_months=1,
        )
        now = timezone.now()
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=now - timedelta(days=30),
            billing_cycle_end=now,  # Just ended
            is_active=True,
            auto_renew=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        # Add teacher to license. _enroll_teacher_internal uses
        # select_for_update() and relies on being called from within an
        # existing transaction (as every production caller does) — wrap it
        # here since this TransactionTestCase doesn't wrap tests in one.
        with transaction.atomic():
            LicenseSubscriptionService._enroll_teacher_internal(
                self.license_sub, self.teacher
            )

    def test_license_renewal_creates_new_monthly_bucket(self):
        """Renewal should create new MONTHLY bucket for each teacher"""
        wallet = self.teacher.credit_wallet
        old_monthly = wallet.buckets.get(bucket_type=CreditBucketType.MONTHLY)
        old_monthly_id = old_monthly.id

        # Renewal
        LicenseSubscriptionService.process_license_renewal(self.license_sub)

        # Old bucket should be expired
        old_monthly.refresh_from_db()
        assert old_monthly.expires_at <= timezone.now()

        # New bucket should exist
        new_monthly = (
            wallet.buckets.filter(bucket_type=CreditBucketType.MONTHLY)
            .exclude(id=old_monthly_id)
            .first()
        )
        assert new_monthly is not None
        assert new_monthly.total_credits == self.plan.monthly_credits

    def test_license_renewal_applies_rollover(self):
        """Renewal should apply rollover if credits remain"""
        wallet = self.teacher.credit_wallet
        monthly = wallet.buckets.get(bucket_type=CreditBucketType.MONTHLY)

        # Simulate partial usage: use 10K of 20K credits
        monthly.used_credits = 10000
        monthly.save()

        LicenseSubscriptionService.process_license_renewal(self.license_sub)

        # Should have carry over bucket with 25% of 10K = 2.5K
        carry_bucket = wallet.buckets.filter(
            bucket_type=CreditBucketType.CARRY_OVER
        ).first()
        expected_rollover = int(10000 * (self.plan.carry_over_percent / 100))
        assert carry_bucket is not None
        assert carry_bucket.total_credits == expected_rollover

    def test_license_renewal_inactive_license_skips(self):
        """Renewal of inactive license should skip"""
        self.license_sub.is_active = False
        self.license_sub.save()

        # Should not raise, but should skip
        LicenseSubscriptionService.process_license_renewal(self.license_sub)

        # License should still be inactive
        self.license_sub.refresh_from_db()
        assert self.license_sub.is_active is False

    def test_license_renewal_no_auto_renew_deactivates(self):
        """Renewal with auto_renew=False should deactivate license"""
        self.license_sub.auto_renew = False
        self.license_sub.save()

        LicenseSubscriptionService.process_license_renewal(self.license_sub)

        self.license_sub.refresh_from_db()
        assert self.license_sub.is_active is False


@pytest.mark.django_db
class TestAllocationInfo(TestCase):
    """Tests for getting allocation information"""

    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Admin",
            last_name="User",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Pro License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )

    def test_get_allocation_info_returns_full_details(self):
        """Should return complete allocation info"""
        LicenseSubscriptionService._enroll_teacher_internal(
            self.license_sub, self.teacher
        )

        info = LicenseSubscriptionService.get_teacher_allocation_info(self.teacher)

        assert info is not None
        assert info["school_name"] == self.school.name
        assert info["plan_name"] == "Pro License"
        assert info["monthly_allocation"] == self.plan.monthly_credits
        assert info["admin_email"] == self.admin.email

    def test_get_allocation_info_returns_none_for_individual_teacher(self):
        """Should return None for teacher without license"""
        teacher = CustomUser.objects.create_user(
            email="other@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Other",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
        )

        info = LicenseSubscriptionService.get_teacher_allocation_info(teacher)
        assert info is None
