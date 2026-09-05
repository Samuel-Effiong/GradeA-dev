"""
billing/tests/test_analytics_and_audit_log.py
=============================================
Two small but real defects found during the section 2 line-by-line pass.

F5 — DIVISION BY ZERO
---------------------
`AnalyticsService.record_consumption` divided `total_credits_used` by
`profile.initial_beta_credits`. That denominator is seeded from the BETA
plan's `monthly_credits`, which is `default=0, null=True` — so a plan left
at its default made the very first AI action raise ZeroDivisionError,
*after* the user's credits had already been deducted (record_consumption
runs inside the consumption flow).

`AnalyticsService.calculate_conversion_probability` divided by
`(now - joined_beta_at).days`, which is 0 for a profile created today.

Both are guarded now. The ratio only gates two "ever reached" booleans, so
with no allocation those flags are simply left untouched rather than given
an invented value. The velocity window is floored at one day, which keeps
the metric honest (a teacher who burns credits on day one has a high
velocity, not a zero one) instead of reporting 0.0.

F6 — DISCARDED AUDIT LOG
------------------------
`LicenseSubscriptionService.update_license_plan` passed four arguments to
a three-placeholder format string. Python's logging raises internally on
that and DISCARDS the record, so a licence plan change — a billing
mutation — produced no audit line at all. It also ran a needless COUNT.
"""

import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BetaProfile,
    CreditWallet,
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.services import AnalyticsService
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()


class RecordConsumptionZeroAllocationTests(TestCase):
    """F5, path A."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="zero.alloc@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

    def test_a_zero_allocation_profile_does_not_crash_consumption(self):
        """The regression: this raised ZeroDivisionError mid-consumption."""
        BetaProfile.objects.update_or_create(
            user=self.user, defaults={"initial_beta_credits": 0}
        )

        AnalyticsService.record_consumption(self.user, 100, "Grading Assignment")

        profile = BetaProfile.objects.get(user=self.user)
        self.assertEqual(profile.total_credits_used, 100)

    def test_a_zero_allocation_profile_leaves_the_threshold_flags_alone(self):
        """
        With no allocation there is no fraction to be a proportion of, so
        neither "ever reached" flag may be invented.
        """
        BetaProfile.objects.update_or_create(
            user=self.user, defaults={"initial_beta_credits": 0}
        )

        AnalyticsService.record_consumption(self.user, 10_000, "Grading Assignment")

        profile = BetaProfile.objects.get(user=self.user)
        self.assertFalse(profile.has_hit_cap)
        self.assertFalse(profile.has_hit_80_percent)

    def test_a_zero_allocation_profile_is_logged_for_investigation(self):
        BetaProfile.objects.update_or_create(
            user=self.user, defaults={"initial_beta_credits": 0}
        )

        with self.assertLogs("billing.services", level="WARNING"):
            AnalyticsService.record_consumption(self.user, 100, "Grading Assignment")

    def test_a_normal_allocation_still_sets_the_thresholds(self):
        """The guard must not disable the thresholds it protects."""
        BetaProfile.objects.update_or_create(
            user=self.user, defaults={"initial_beta_credits": 1_000}
        )

        AnalyticsService.record_consumption(self.user, 850, "Grading Assignment")
        profile = BetaProfile.objects.get(user=self.user)
        self.assertTrue(profile.has_hit_80_percent)
        self.assertFalse(profile.has_hit_cap)

        AnalyticsService.record_consumption(self.user, 200, "Grading Assignment")
        profile.refresh_from_db()
        self.assertTrue(profile.has_hit_cap)

    def test_consumption_totals_are_recorded_either_way(self):
        BetaProfile.objects.update_or_create(
            user=self.user, defaults={"initial_beta_credits": 0}
        )

        AnalyticsService.record_consumption(self.user, 250, "Grading Assignment")

        profile = BetaProfile.objects.get(user=self.user)
        self.assertEqual(profile.credits_used_grading, 250)


class ConversionProbabilityZeroDaysTests(TestCase):
    """F5, path B."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="zero.days@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

    def test_a_profile_created_today_does_not_crash(self):
        """The regression: `.days` is 0 on the day of creation."""
        profile, _ = BetaProfile.objects.get_or_create(user=self.user)
        profile.total_credits_used = 5_000
        profile.save(update_fields=["total_credits_used"])

        AnalyticsService.calculate_conversion_probability(profile)

        profile.refresh_from_db()
        self.assertEqual(profile.usage_velocity, 5_000.0)

    def test_velocity_uses_the_real_window_once_days_have_passed(self):
        profile, _ = BetaProfile.objects.get_or_create(user=self.user)
        BetaProfile.objects.filter(pk=profile.pk).update(
            joined_beta_at=timezone.now() - timedelta(days=4),
            total_credits_used=4_000,
        )
        profile.refresh_from_db()

        AnalyticsService.calculate_conversion_probability(profile)

        profile.refresh_from_db()
        self.assertEqual(profile.usage_velocity, 1_000.0)

    def test_a_profile_with_no_usage_reports_zero_velocity(self):
        profile, _ = BetaProfile.objects.get_or_create(user=self.user)

        AnalyticsService.calculate_conversion_probability(profile)

        profile.refresh_from_db()
        self.assertEqual(profile.usage_velocity, 0.0)


class UpdateLicensePlanAuditLogTests(TestCase):
    """F6."""

    def setUp(self):
        self.school = School.objects.create(name="Audit Log School")
        self.admin = CustomUser.objects.create_user(
            email="audit.admin@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        self.old_plan = self._plan(PlanType.PRO, 10_000, tier=PlanTier.PRO)
        self.new_plan = self._plan(PlanType.POWER, 20_000, tier=PlanTier.POWER)
        now = timezone.now()
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.old_plan,
            contract_months=1,
            max_seats=10,
            is_active=True,
            billing_method=LicenseBillingMethod.STRIPE,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=30),
        )
        self.teacher = CustomUser.objects.create_user(
            email="audit.teacher@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
            is_active=True,
        )
        CreditWallet.objects.get_or_create(user=self.teacher)
        SchoolCreditAllocation.objects.create(
            license_subscription=self.license,
            user=self.teacher,
            monthly_allocation=10_000,
            is_active=True,
            next_credit_grant_at=now,
        )

    def _plan(self, name, credits, tier=PlanTier.PRO):
        # NOT PlanTier.STANDARD: validate_license_plan rejects the Standard
        # Grader tier under a licence, which is a real business rule.
        return SubscriptionPlan.objects.create(
            name=name,
            display_name=str(name),
            category=PlanCategory.LICENSE,
            tier=tier,
            monthly_credits=credits,
            is_active=True,
        )

    def test_the_plan_change_emits_an_audit_line(self):
        """
        The regression: logging raised on the arg/placeholder mismatch and
        dropped the record, so this produced no audit line at all.
        """
        with self.assertLogs("billing.license_service", level="INFO") as logs:
            LicenseSubscriptionService.update_license_plan(self.license, self.new_plan)

        matching = [line for line in logs.output if "Updated license" in line]
        self.assertEqual(
            len(matching), 1, f"expected one audit line, got: {logs.output}"
        )
        self.assertIn(str(self.license.id), matching[0])
        self.assertIn(str(self.old_plan.name), matching[0])
        self.assertIn(str(self.new_plan.name), matching[0])
        self.assertIn("1 teacher(s)", matching[0])

    def test_logging_does_not_raise_internally(self):
        """
        assertLogs would still pass if the record were dropped by a
        formatting error, so assert the format itself is well-formed.
        """
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                # Raises if the args don't match the format string.
                records.append(record.getMessage())

        handler = _Capture()
        logger = logging.getLogger("billing.license_service")
        previous_level = logger.level
        # The project's LOGGING config leaves this logger unconfigured, so it
        # inherits WARNING and would filter the INFO record before any handler
        # saw it. assertLogs does this for you; a raw handler must do it here.
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            LicenseSubscriptionService.update_license_plan(self.license, self.new_plan)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        self.assertTrue(any("Updated license" in message for message in records))

    def test_the_plan_change_still_applies(self):
        LicenseSubscriptionService.update_license_plan(self.license, self.new_plan)

        self.license.refresh_from_db()
        self.assertEqual(self.license.plan_id, self.new_plan.id)
        allocation = SchoolCreditAllocation.objects.get(
            license_subscription=self.license, user=self.teacher
        )
        self.assertEqual(allocation.monthly_allocation, 20_000)
