"""
billing/tests/test_mailerlite_sync.py
=======================================
Locks in when a MailerLite sync gets queued as a side effect of billing
state changes.

Two rules are under test:

1. queue_sync(user) (users/mailerlite_service.py) only enqueues a sync
   for users whose account is already active - it must never sync a
   user mid-signup/invite before they've completed activation.
2. Every code path that flips a subscription's is_active status, or
   changes its plan/tier, must call queue_sync() (single user) or
   sync_teachers_under_license_to_mailerlite() (license - all
   currently-allocated teachers) so MailerLite never goes stale.

Every test mocks users.tasks.sync_user_to_mailerlite.delay so nothing
here ever makes a real HTTP call or touches Celery - we're only
asserting that the right call was queued with the right argument.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from billing.services import SubscriptionService
from classrooms.models import School
from users.mailerlite_service import queue_sync
from users.models import CustomUser, UserTypes


def make_user(email, user_type=UserTypes.TEACHER, is_active=True, **extra):
    return CustomUser.objects.create_user(
        email=email,
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        is_active=is_active,
        **extra,
    )


def make_individual_plan(
    name=PlanType.STANDARD, tier=PlanTier.STANDARD, monthly_credits=10_000_000
):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        monthly_credits=monthly_credits,
        carry_over_percent=15,
        carry_over_max=1_500_000,
        carry_over_expiry_months=1,
        overage_block_size=5_000_000,
        overage_block_price=500,
        max_overage_blocks=5,
        is_active=True,
    )


def make_license_plan(
    name=PlanType.PRO_LICENSE, tier=PlanTier.PRO, monthly_credits=20_000_000
):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=PlanCategory.LICENSE,
        tier=tier,
        monthly_credits=monthly_credits,
        carry_over_percent=25,
        carry_over_max=5_000_000,
        carry_over_expiry_months=1,
        overage_block_size=5_000_000,
        overage_block_price=500,
        max_overage_blocks=5,
        is_active=True,
    )


class MailerLiteMockedTestCase(TestCase):
    """Patches the Celery task's .delay before setUp runs, so signal-driven
    side effects during fixture creation (e.g. auto trial activation on
    CustomUser post_save) are captured too - callers should reset_mock()
    after fixture setup and before the action under test."""

    def setUp(self):
        super().setUp()
        patcher = patch("users.tasks.sync_user_to_mailerlite.delay")
        self.mock_delay = patcher.start()
        self.addCleanup(patcher.stop)


class QueueSyncGuardTests(MailerLiteMockedTestCase):
    def test_active_user_is_queued(self):
        user = make_user("active@example.com", is_active=True)
        self.mock_delay.reset_mock()

        queue_sync(user)

        self.mock_delay.assert_called_once_with(str(user.id))

    def test_inactive_user_is_not_queued(self):
        user = make_user("inactive@example.com", is_active=False)
        self.mock_delay.reset_mock()

        queue_sync(user)

        self.mock_delay.assert_not_called()


class IndividualSubscriptionSyncTests(MailerLiteMockedTestCase):
    def setUp(self):
        super().setUp()
        self.plan = make_individual_plan()
        self.upgraded_plan = make_individual_plan(
            name=PlanType.PRO, tier=PlanTier.PRO, monthly_credits=20_000_000
        )

    def test_activate_subscription_syncs_active_user(self):
        user = make_user("teacher1@example.com", is_active=True)
        self.mock_delay.reset_mock()

        SubscriptionService.activate_subscription(user, self.plan)

        self.mock_delay.assert_called_with(str(user.id))

    def test_activate_subscription_skips_inactive_user(self):
        # Mirrors users/signals.py granting a beta/individual plan to a
        # user mid-signup, before they've verified their email.
        user = make_user("teacher2@example.com", is_active=False)
        self.mock_delay.reset_mock()

        SubscriptionService.activate_subscription(user, self.plan)

        self.mock_delay.assert_not_called()

    def test_apply_immediate_plan_change_syncs_user(self):
        user = make_user("teacher3@example.com", is_active=True)
        sub = SubscriptionService.activate_subscription(user, self.plan)
        self.mock_delay.reset_mock()

        SubscriptionService.apply_immediate_plan_change(sub, self.upgraded_plan)

        self.mock_delay.assert_called_with(str(user.id))

    def test_activate_free_trial_syncs_active_user(self):
        user = make_user("teacher4@example.com", is_active=True)
        self.mock_delay.reset_mock()

        SubscriptionService.activate_free_trial(user, self.plan)

        self.mock_delay.assert_called_with(str(user.id))

    def test_activate_free_trial_skips_inactive_user(self):
        user = make_user("teacher4b@example.com", is_active=False)
        self.mock_delay.reset_mock()

        SubscriptionService.activate_free_trial(user, self.plan)

        self.mock_delay.assert_not_called()

    def test_expire_trial_syncs_user(self):
        user = make_user("teacher5@example.com", is_active=True)
        trial_sub = SubscriptionService.activate_free_trial(user, self.plan)
        trial_sub.trial_end = timezone.now() - timedelta(days=1)
        trial_sub.save(update_fields=["trial_end"])
        self.mock_delay.reset_mock()

        SubscriptionService.expire_trial(trial_sub)

        self.mock_delay.assert_called_with(str(user.id))

    def test_finalize_trial_conversion_via_stripe_syncs_user(self):
        user = make_user("teacher6@example.com", is_active=True)
        trial_sub = SubscriptionService.activate_free_trial(user, self.plan)
        self.mock_delay.reset_mock()

        SubscriptionService.finalize_trial_conversion_via_stripe(trial_sub)

        self.mock_delay.assert_called_with(str(user.id))

    def test_finalize_trial_to_paid_conversion_syncs_user(self):
        user = make_user("teacher7@example.com", is_active=True)
        trial_sub = SubscriptionService.activate_free_trial(user, self.plan)
        self.mock_delay.reset_mock()

        SubscriptionService.finalize_trial_to_paid_conversion(
            trial_sub, self.upgraded_plan, "sub_test_123"
        )

        self.mock_delay.assert_called_with(str(user.id))


class AutomaticTrialSyncTests(MailerLiteMockedTestCase):
    @staticmethod
    def _make_trial_plan():
        # Created AFTER the teacher user in each test below, so the
        # post_save signal's own activate_automatic_free_trial() call
        # (fired at user-creation time, before this plan exists) fails
        # harmlessly with "plan not found" instead of consuming the
        # trial before the explicit call under test runs.
        return SubscriptionPlan.objects.create(
            name=PlanType.TRIAL,
            display_name="Trial",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.TRIAL,
            monthly_credits=5_000_000,
            carry_over_percent=0,
            carry_over_max=0,
            carry_over_expiry_months=1,
            overage_block_size=5_000_000,
            overage_block_price=500,
            max_overage_blocks=5,
            is_active=True,
        )

    def test_activate_automatic_free_trial_syncs_active_user(self):
        user = make_user("teacher8@example.com", is_active=True)
        self._make_trial_plan()
        self.mock_delay.reset_mock()

        SubscriptionService.activate_automatic_free_trial(user)

        self.mock_delay.assert_called_with(str(user.id))

    def test_activate_automatic_free_trial_skips_inactive_user(self):
        # This is the real-world shape: the post_save signal calls this
        # during signup, while the row is still is_active=False.
        user = make_user("teacher9@example.com", is_active=False)
        self._make_trial_plan()
        self.mock_delay.reset_mock()

        SubscriptionService.activate_automatic_free_trial(user)

        self.mock_delay.assert_not_called()


class LicenseSyncTests(MailerLiteMockedTestCase):
    def setUp(self):
        super().setUp()
        self.school = School.objects.create(name="Test School")
        self.admin = make_user(
            "admin@school.edu", user_type=UserTypes.SCHOOL_ADMIN, school=self.school
        )
        self.plan = make_license_plan()
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            billing_method=LicenseBillingMethod.OFFLINE,
        )
        self.teacher = make_user(
            "teacher@school.edu", user_type=UserTypes.TEACHER, school=self.school
        )

    def test_add_teacher_to_license_syncs_active_teacher(self):
        self.mock_delay.reset_mock()

        LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, self.teacher.email
        )

        self.mock_delay.assert_called_with(str(self.teacher.id))

    def test_add_teacher_to_license_skips_inactive_teacher(self):
        inactive_teacher = make_user(
            "inactive_teacher@school.edu",
            user_type=UserTypes.TEACHER,
            school=self.school,
            is_active=False,
        )
        self.mock_delay.reset_mock()

        LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, inactive_teacher.email
        )

        self.mock_delay.assert_not_called()

    def test_update_license_plan_syncs_all_active_teachers(self):
        LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, self.teacher.email
        )
        new_plan = make_license_plan(
            name=PlanType.POWER_LICENSE, tier=PlanTier.POWER, monthly_credits=40_000_000
        )
        self.mock_delay.reset_mock()

        LicenseSubscriptionService.update_license_plan(self.license_sub, new_plan)

        self.mock_delay.assert_called_with(str(self.teacher.id))

    def test_change_license_plan_syncs_all_active_teachers(self):
        LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, self.teacher.email
        )
        new_plan = make_license_plan(
            name=PlanType.POWER_LICENSE, tier=PlanTier.POWER, monthly_credits=40_000_000
        )
        self.mock_delay.reset_mock()

        LicenseSubscriptionService.change_license_plan(self.license_sub, new_plan)

        self.mock_delay.assert_called_with(str(self.teacher.id))

    def test_cancel_license_subscription_syncs_all_active_teachers(self):
        LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, self.teacher.email
        )
        self.mock_delay.reset_mock()

        LicenseSubscriptionService.cancel_license_subscription(self.license_sub)

        self.mock_delay.assert_called_with(str(self.teacher.id))

    def test_sync_teachers_under_license_excludes_inactive_allocation(self):
        LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, self.teacher.email
        )
        LicenseSubscriptionService.remove_teacher_from_license(
            self.license_sub, self.teacher
        )
        self.mock_delay.reset_mock()

        LicenseSubscriptionService.update_license_plan(
            self.license_sub,
            make_license_plan(
                name=PlanType.CUSTOM_LICENSE_MID,
                tier=PlanTier.CUSTOM,
                monthly_credits=50_000_000,
            ),
        )

        self.mock_delay.assert_not_called()
