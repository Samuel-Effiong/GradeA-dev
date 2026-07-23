"""
billing/tests/test_access_control.py
=====================================
Covers Phases 1-8 of TEST_PLAN.md - the core access-control mechanism
itself, independent of the AIProcessor enforcement chokepoint (see
ai_processor/tests/test_execute_graded_task.py for that layer).

Run with:
    python manage.py test billing.tests.test_access_control

NOTE: classrooms.models.School's required fields aren't visible to me in
this conversation - _make_school() below creates one with only `name`. If
your actual School model requires more fields, Django will raise a clear
error naming the missing field(s); add them there.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from billing.access_control import (
    ADMIN_ALLOWED_AI_FEATURES,
    AI_FEATURE_GATING_MAP,
    can_ai_be_used_for_assignment,
    can_user_access_ai,
    get_remaining_trial_days,
    get_user_ai_access_status,
    is_user_on_active_trial,
    is_user_trial_expired,
)
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanFeature,
    PlanFeatureInclusion,
    PlanFeatureKey,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes


class AccessControlTestBase(TestCase):
    """Shared fixture helpers for every test class below."""

    def _make_user(self, user_type, email, is_active=True):
        return CustomUser.objects.create_user(
            email=email,
            password="testpass123",
            user_type=user_type,
            is_active=is_active,
        )

    def _make_plan(
        self,
        name,
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        monthly_credits=20000,
        carry_over_percent=25,
    ):
        return SubscriptionPlan.objects.create(
            name=name,
            category=category,
            tier=tier,
            interval=BillingInterval.MONTHLY,
            monthly_credits=monthly_credits,
            carry_over_percent=carry_over_percent,
            is_active=True,
        )

    def _give_credits(self, user, amount):
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        if amount > 0:
            CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=amount,
                used_credits=0,
                expires_at=timezone.now() + timedelta(days=30),
            )
        return wallet

    def _make_individual_subscription(
        self, user, plan, is_trial=False, trial_end=None, is_active=True
    ):
        now = timezone.now()
        return UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=is_active,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=30),
            is_trial=is_trial,
            trial_end=trial_end,
            auto_renew=not is_trial,
        )

    def _make_school(self):
        return School.objects.create(name="Test School")

    def _make_license(self, plan, admin_user, is_active=True, school=None):
        now = timezone.now()
        return LicenseSubscription.objects.create(
            school=school or self._make_school(),
            admin_user=admin_user,
            plan=plan,
            contract_months=12,
            max_seats=10,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=365),
            is_active=is_active,
            auto_renew=True,
        )

    def _make_allocation(self, license_sub, user, is_admin=False, is_active=True):
        return SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=user,
            monthly_allocation=20000,
            is_active=is_active,
            is_admin_allocation=is_admin,
            next_credit_grant_at=timezone.now() + timedelta(days=30),
        )

    def _make_gating_feature(self, key, is_gating=True):
        feature, _ = PlanFeature.objects.get_or_create(
            key=key, defaults={"label": key, "is_gating_feature": is_gating}
        )
        if feature.is_gating_feature != is_gating:
            feature.is_gating_feature = is_gating
            feature.save(update_fields=["is_gating_feature"])
        return feature

    def _include_feature(self, plan, feature, included=True):
        PlanFeatureInclusion.objects.update_or_create(
            plan=plan, feature=feature, defaults={"included": included}
        )


class ResolveAccessContextTests(AccessControlTestBase):
    """Phase 1 - the resolver every other check depends on."""

    def test_individual_paid_subscription(self):
        plan = self._make_plan(PlanType.PRO)
        user = self._make_user(UserTypes.TEACHER, "paid@example.com")
        self._make_individual_subscription(user, plan)
        self._give_credits(user, 1000)

        can_access, reason = can_user_access_ai(user)
        self.assertTrue(can_access, reason)

    def test_no_subscription_at_all(self):
        user = self._make_user(UserTypes.TEACHER, "nosub@example.com")
        can_access, reason = can_user_access_ai(user)
        self.assertFalse(can_access)
        self.assertEqual(reason, "No active subscription")

    def test_license_teacher_resolves_to_license_plan(self):
        license_plan = self._make_plan(
            PlanType.PRO_LICENSE, category=PlanCategory.LICENSE
        )
        admin = self._make_user(UserTypes.SCHOOL_ADMIN, "admin1@example.com")
        license_sub = self._make_license(license_plan, admin)
        teacher = self._make_user(UserTypes.TEACHER, "teacher1@example.com")
        self._make_allocation(license_sub, teacher, is_admin=False)
        self._give_credits(teacher, 1000)

        can_access, reason = can_user_access_ai(teacher)
        self.assertTrue(can_access, reason)

    def test_license_admin_resolves_separately_from_teacher(self):
        license_plan = self._make_plan(
            PlanType.PRO_LICENSE, category=PlanCategory.LICENSE
        )
        admin = self._make_user(UserTypes.SCHOOL_ADMIN, "admin2@example.com")
        license_sub = self._make_license(license_plan, admin)
        self._make_allocation(license_sub, admin, is_admin=True)
        self._give_credits(admin, 1000)

        can_access, reason = can_user_access_ai(admin)
        self.assertTrue(can_access, reason)

    def test_teacher_allocation_under_inactive_license_does_not_resolve(self):
        license_plan = self._make_plan(
            PlanType.PRO_LICENSE, category=PlanCategory.LICENSE
        )
        admin = self._make_user(UserTypes.SCHOOL_ADMIN, "admin3@example.com")
        license_sub = self._make_license(license_plan, admin, is_active=False)
        teacher = self._make_user(UserTypes.TEACHER, "teacher2@example.com")
        self._make_allocation(license_sub, teacher, is_admin=False)
        self._give_credits(teacher, 1000)

        can_access, reason = can_user_access_ai(teacher)
        self.assertFalse(can_access)
        self.assertEqual(reason, "No active subscription")

    def test_inactive_allocation_does_not_resolve(self):
        license_plan = self._make_plan(
            PlanType.PRO_LICENSE, category=PlanCategory.LICENSE
        )
        admin = self._make_user(UserTypes.SCHOOL_ADMIN, "admin4@example.com")
        license_sub = self._make_license(license_plan, admin)
        teacher = self._make_user(UserTypes.TEACHER, "teacher3@example.com")
        self._make_allocation(license_sub, teacher, is_admin=False, is_active=False)
        self._give_credits(teacher, 1000)

        can_access, reason = can_user_access_ai(teacher)
        self.assertFalse(can_access)
        self.assertEqual(reason, "No active subscription")


class BaseAccessChecksTests(AccessControlTestBase):
    """Phase 2 - authentication / trial-window / credit checks."""

    def test_inactive_account_blocked(self):
        plan = self._make_plan(PlanType.PRO)
        user = self._make_user(
            UserTypes.TEACHER, "inactive@example.com", is_active=False
        )
        self._make_individual_subscription(user, plan)
        self._give_credits(user, 1000)

        can_access, reason = can_user_access_ai(user)
        self.assertFalse(can_access)
        self.assertEqual(reason, "User account is inactive")

    def test_trial_within_window_and_has_credits(self):
        plan = self._make_plan(PlanType.TRIAL, tier=PlanTier.TRIAL)
        user = self._make_user(UserTypes.TEACHER, "trial1@example.com")
        self._make_individual_subscription(
            user, plan, is_trial=True, trial_end=timezone.now() + timedelta(days=5)
        )
        self._give_credits(user, 1000)

        can_access, reason = can_user_access_ai(user)
        self.assertTrue(can_access, reason)

    def test_trial_expired(self):
        plan = self._make_plan(PlanType.TRIAL, tier=PlanTier.TRIAL)
        user = self._make_user(UserTypes.TEACHER, "trial2@example.com")
        self._make_individual_subscription(
            user, plan, is_trial=True, trial_end=timezone.now() - timedelta(days=1)
        )
        self._give_credits(user, 1000)

        can_access, reason = can_user_access_ai(user)
        self.assertFalse(can_access)
        self.assertIn("Trial period has expired", reason)

    def test_paid_user_zero_credits(self):
        plan = self._make_plan(PlanType.PRO)
        user = self._make_user(UserTypes.TEACHER, "zerocredits@example.com")
        self._make_individual_subscription(user, plan)
        self._give_credits(user, 0)

        can_access, reason = can_user_access_ai(user)
        self.assertFalse(can_access)
        self.assertIn("No credits remaining", reason)

    def test_trial_user_zero_credits_gets_distinct_message(self):
        plan = self._make_plan(PlanType.TRIAL, tier=PlanTier.TRIAL)
        user = self._make_user(UserTypes.TEACHER, "trialzero@example.com")
        self._make_individual_subscription(
            user, plan, is_trial=True, trial_end=timezone.now() + timedelta(days=5)
        )
        self._give_credits(user, 0)

        can_access, reason = can_user_access_ai(user)
        self.assertFalse(can_access)
        self.assertIn("Out of trial credits", reason)
        self.assertNotIn("No credits remaining", reason)


class FeatureTierGatingTests(AccessControlTestBase):
    """Phase 3 - premium-feature gating via PlanFeature/PlanFeatureInclusion."""

    def test_gated_feature_denied_when_not_included(self):
        plan = self._make_plan(PlanType.STANDARD, tier=PlanTier.STANDARD)
        feature = self._make_gating_feature(
            PlanFeatureKey.AI_PROMPT_ASSIGNMENT_CREATION
        )
        self._include_feature(plan, feature, included=False)
        user = self._make_user(UserTypes.TEACHER, "standard@example.com")
        self._make_individual_subscription(user, plan)
        self._give_credits(user, 1000)

        can_access, reason = can_user_access_ai(user, feature="Assignment Generation")
        self.assertFalse(can_access)
        self.assertIn("does not include this feature", reason)

    def test_gated_feature_allowed_when_included(self):
        plan = self._make_plan(PlanType.PRO, tier=PlanTier.PRO)
        feature = self._make_gating_feature(
            PlanFeatureKey.AI_PROMPT_ASSIGNMENT_CREATION
        )
        self._include_feature(plan, feature, included=True)
        user = self._make_user(UserTypes.TEACHER, "pro@example.com")
        self._make_individual_subscription(user, plan)
        self._give_credits(user, 1000)

        can_access, reason = can_user_access_ai(user, feature="Assignment Generation")
        self.assertTrue(can_access, reason)

    def test_ungated_baseline_feature_always_allowed(self):
        plan = self._make_plan(PlanType.STANDARD, tier=PlanTier.STANDARD)
        user = self._make_user(UserTypes.TEACHER, "baseline@example.com")
        self._make_individual_subscription(user, plan)
        self._give_credits(user, 1000)

        # "Grading Assignment" is deliberately NOT in AI_FEATURE_GATING_MAP
        self.assertNotIn("Grading Assignment", AI_FEATURE_GATING_MAP)
        can_access, reason = can_user_access_ai(user, feature="Grading Assignment")
        self.assertTrue(can_access, reason)

    def test_display_only_feature_never_gates_even_if_included(self):
        plan = self._make_plan(PlanType.STANDARD, tier=PlanTier.STANDARD)
        # is_gating_feature=False - a catalogue label, not a real gate
        feature = self._make_gating_feature(
            PlanFeatureKey.AI_PROMPT_ASSIGNMENT_CREATION, is_gating=False
        )
        self._include_feature(plan, feature, included=True)
        user = self._make_user(UserTypes.TEACHER, "displayonly@example.com")
        self._make_individual_subscription(user, plan)
        self._give_credits(user, 1000)

        can_access, reason = can_user_access_ai(user, feature="Assignment Generation")
        self.assertTrue(can_access, reason)

    def test_missing_plan_feature_row_denies_by_default(self):
        plan = self._make_plan(PlanType.STANDARD, tier=PlanTier.STANDARD)
        user = self._make_user(UserTypes.TEACHER, "noconfig@example.com")
        self._make_individual_subscription(user, plan)
        self._give_credits(user, 1000)

        # Deliberately do NOT create the PlanFeature/PlanFeatureInclusion row.
        self.assertFalse(
            PlanFeature.objects.filter(
                pk=PlanFeatureKey.AI_PROMPT_ASSIGNMENT_CREATION
            ).exists()
        )
        can_access, reason = can_user_access_ai(user, feature="Assignment Generation")
        self.assertFalse(can_access)


class LicenseAdminAllowlistTests(AccessControlTestBase):
    """Phase 4 - admin's fixed feature allowlist, independent of plan tier."""

    def _make_admin_with_allocation(self, license_plan, credits=1000):
        admin = self._make_user(UserTypes.SCHOOL_ADMIN, "adminfeature@example.com")
        license_sub = self._make_license(license_plan, admin)
        self._make_allocation(license_sub, admin, is_admin=True)
        self._give_credits(admin, credits)
        return admin, license_sub

    def test_allowlisted_feature_allowed(self):
        plan = self._make_plan(PlanType.POWER_LICENSE, category=PlanCategory.LICENSE)
        admin, _ = self._make_admin_with_allocation(plan)
        allowed_feature = next(iter(ADMIN_ALLOWED_AI_FEATURES))

        can_access, reason = can_user_access_ai(admin, feature=allowed_feature)
        self.assertTrue(can_access, reason)

    def test_non_allowlisted_feature_denied_even_if_plan_includes_it(self):
        plan = self._make_plan(PlanType.POWER_LICENSE, category=PlanCategory.LICENSE)
        admin, _ = self._make_admin_with_allocation(plan)

        # Prove the plan DOES include the gated feature...
        feature = self._make_gating_feature(
            PlanFeatureKey.AI_PROMPT_ASSIGNMENT_CREATION
        )
        self._include_feature(plan, feature, included=True)

        # ...but the admin is still denied, because AI_FEATURE_GATING_MAP /
        # plan tier is irrelevant on the license_admin path.
        can_access, reason = can_user_access_ai(admin, feature="Assignment Generation")
        self.assertFalse(can_access)
        self.assertIn("not available to school admin accounts", reason)

    def test_grading_assignment_denied_for_admin(self):
        plan = self._make_plan(PlanType.PRO_LICENSE, category=PlanCategory.LICENSE)
        admin, _ = self._make_admin_with_allocation(plan)
        self.assertNotIn("Grading Assignment", ADMIN_ALLOWED_AI_FEATURES)

        can_access, reason = can_user_access_ai(admin, feature="Grading Assignment")
        self.assertFalse(can_access)


class StudentSubmissionAccessTests(AccessControlTestBase):
    """Phase 5 - can_ai_be_used_for_assignment (student-triggered billing)."""

    def _make_fake_assignment(self, teacher):
        """
        Lightweight stand-in for a real Assignment/Course pair - we only
        need `.course.teacher` to resolve to a real CustomUser so the
        DB-backed access checks run for real. Substitute your actual
        Assignment factory here if you have one, for higher fidelity.
        """
        from unittest.mock import MagicMock

        assignment = MagicMock()
        assignment.course.teacher = teacher
        assignment.id = "fake-assignment-id"
        return assignment

    def test_none_assignment_denied(self):
        can_access, reason = can_ai_be_used_for_assignment(None)
        self.assertFalse(can_access)
        self.assertIn("Assignment is required", reason)

    def test_valid_assignment_teacher_has_access(self):
        plan = self._make_plan(PlanType.PRO)
        teacher = self._make_user(UserTypes.TEACHER, "assignteacher@example.com")
        self._make_individual_subscription(teacher, plan)
        self._give_credits(teacher, 1000)

        assignment = self._make_fake_assignment(teacher)
        can_access, reason = can_ai_be_used_for_assignment(assignment)
        self.assertTrue(can_access, reason)

    def test_valid_assignment_teacher_out_of_credits(self):
        plan = self._make_plan(PlanType.PRO)
        teacher = self._make_user(UserTypes.TEACHER, "brokeeacher@example.com")
        self._make_individual_subscription(teacher, plan)
        self._give_credits(teacher, 0)

        assignment = self._make_fake_assignment(teacher)
        can_access, reason = can_ai_be_used_for_assignment(assignment)
        self.assertFalse(can_access)
        self.assertIn("No credits remaining", reason)


class GetUserAIAccessStatusTests(AccessControlTestBase):
    """Phase 6 - frontend status payload."""

    def test_unauthenticated_returns_safe_defaults(self):
        from django.contrib.auth.models import AnonymousUser

        data = get_user_ai_access_status(AnonymousUser())
        self.assertFalse(data["can_access"])
        self.assertIsNone(data["subscription_type"])
        self.assertEqual(data["credits_remaining"], 0)

    def test_trial_status_fields(self):
        plan = self._make_plan(PlanType.TRIAL, tier=PlanTier.TRIAL)
        user = self._make_user(UserTypes.TEACHER, "statustrial@example.com")
        self._make_individual_subscription(
            user, plan, is_trial=True, trial_end=timezone.now() + timedelta(days=3)
        )
        self._give_credits(user, 1000)

        data = get_user_ai_access_status(user)
        self.assertEqual(data["subscription_type"], "TRIAL")
        self.assertTrue(data["is_trial"])
        self.assertIn(data["days_remaining_in_trial"], (2, 3))

    def test_license_teacher_status(self):
        plan = self._make_plan(PlanType.PRO_LICENSE, category=PlanCategory.LICENSE)
        admin = self._make_user(UserTypes.SCHOOL_ADMIN, "statusadmin@example.com")
        license_sub = self._make_license(plan, admin)
        teacher = self._make_user(UserTypes.TEACHER, "statusteacher@example.com")
        self._make_allocation(license_sub, teacher, is_admin=False)
        self._give_credits(teacher, 1000)

        data = get_user_ai_access_status(teacher)
        self.assertEqual(data["subscription_type"], "LICENSE_TEACHER")

    def test_license_admin_status(self):
        plan = self._make_plan(PlanType.PRO_LICENSE, category=PlanCategory.LICENSE)
        admin = self._make_user(UserTypes.SCHOOL_ADMIN, "statusadmin2@example.com")
        license_sub = self._make_license(plan, admin)
        self._make_allocation(license_sub, admin, is_admin=True)
        self._give_credits(admin, 1000)

        data = get_user_ai_access_status(admin)
        self.assertEqual(data["subscription_type"], "LICENSE_ADMIN")


class TrialTypoRegressionTests(AccessControlTestBase):
    """
    Phase 8 - regression tests for the `user.subscription` (singular,
    nonexistent attribute) typo bug. Before the fix, all three of these
    ALWAYS returned their "no trial" default for every user, silently,
    because the AttributeError was swallowed by a broad except clause.
    """

    def test_get_remaining_trial_days_not_always_zero(self):
        plan = self._make_plan(PlanType.TRIAL, tier=PlanTier.TRIAL)
        user = self._make_user(UserTypes.TEACHER, "typo1@example.com")
        self._make_individual_subscription(
            user, plan, is_trial=True, trial_end=timezone.now() + timedelta(days=7)
        )

        days = get_remaining_trial_days(user)
        self.assertGreater(days, 0)

    def test_is_user_on_active_trial_not_always_false(self):
        plan = self._make_plan(PlanType.TRIAL, tier=PlanTier.TRIAL)
        user = self._make_user(UserTypes.TEACHER, "typo2@example.com")
        self._make_individual_subscription(
            user, plan, is_trial=True, trial_end=timezone.now() + timedelta(days=7)
        )

        self.assertTrue(is_user_on_active_trial(user))

    def test_is_user_trial_expired_not_always_false(self):
        plan = self._make_plan(PlanType.TRIAL, tier=PlanTier.TRIAL)
        user = self._make_user(UserTypes.TEACHER, "typo3@example.com")
        self._make_individual_subscription(
            user, plan, is_trial=True, trial_end=timezone.now() - timedelta(days=1)
        )

        self.assertTrue(is_user_trial_expired(user))

    def test_is_user_trial_expired_false_for_active_trial(self):
        plan = self._make_plan(PlanType.TRIAL, tier=PlanTier.TRIAL)
        user = self._make_user(UserTypes.TEACHER, "typo4@example.com")
        self._make_individual_subscription(
            user, plan, is_trial=True, trial_end=timezone.now() + timedelta(days=7)
        )

        self.assertFalse(is_user_trial_expired(user))
