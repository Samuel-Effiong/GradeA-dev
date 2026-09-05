"""
billing/tests/test_credit_ceiling_resolution.py
===============================================
`get_monthly_credit_ceiling_for_user` — the denominator behind every
"you have used X% of your credits" figure a user sees.

WHY THIS FILE EXISTS
--------------------
`billing/subscription_resolver.py` sat at 72.7%, and the uncovered block
was this entire function: all three of its source branches plus both of its
guard clauses. Nothing tested it.

It is not a display detail. The LICENSE_TEACHER branch encodes a real
business rule with a stated consequence:

    the ceiling is the teacher's `allocation.monthly_allocation`, NOT the
    plan's nominal `monthly_credits`

because a licence caps a teacher's real per-cycle grant by the school's
seat/global budget, so the two genuinely differ. Using the plan default
would show a progress bar that can never reach 100% even after the teacher
has spent every credit they actually have — the user is told they have
headroom that does not exist.

`test_a_capped_teacher_is_measured_against_their_real_grant` is the one
that catches that specific regression: it sets the allocation BELOW the
plan default and asserts the smaller number comes back.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from billing.subscription_resolver import get_monthly_credit_ceiling_for_user
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()


class CreditCeilingResolutionTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Ceiling School")

        self.individual_plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Standard",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            monthly_credits=12_345,
            is_active=True,
        )
        self.license_plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Pro Licence",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            monthly_credits=50_000,
            is_active=True,
        )

    def _user(self, email, user_type=UserTypes.TEACHER, school=None):
        return CustomUser.objects.create_user(
            email=email,
            password="testpass123",  # pragma: allowlist secret
            user_type=user_type,
            is_active=True,
            school=school,
        )

    def _license(self, admin):
        now = timezone.now()
        return LicenseSubscription.objects.create(
            school=self.school,
            admin_user=admin,
            plan=self.license_plan,
            contract_months=12,
            max_seats=20,
            is_active=True,
            billing_method=LicenseBillingMethod.OFFLINE,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=30),
        )

    # --- INDIVIDUAL -----------------------------------------------------

    def test_an_individual_subscriber_is_measured_against_their_plan(self):
        user = self._user("ceiling.individual@test.dev")
        now = timezone.now()
        UserSubscription.objects.create(
            user=user,
            plan=self.individual_plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )

        self.assertEqual(get_monthly_credit_ceiling_for_user(user), 12_345)

    def test_a_plan_with_null_credits_reports_zero_not_none(self):
        """
        `monthly_credits` is nullable. Returning None would propagate into
        a division and 500 the dashboard; the contract is an int.
        """
        plan = SubscriptionPlan.objects.create(
            name=PlanType.CUSTOM,
            display_name="Custom",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.CUSTOM,
            interval=BillingInterval.MONTHLY,
            monthly_credits=None,
            is_active=True,
        )
        user = self._user("ceiling.nullcredits@test.dev")
        now = timezone.now()
        UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )

        result = get_monthly_credit_ceiling_for_user(user)
        self.assertEqual(result, 0)
        self.assertIsInstance(result, int)

    # --- LICENSE_TEACHER ------------------------------------------------

    def test_a_capped_teacher_is_measured_against_their_real_grant(self):
        """
        THE REGRESSION THIS FILE EXISTS FOR.

        The teacher's allocation (8,000) is deliberately LOWER than the
        licence plan's nominal monthly_credits (50,000), which is what
        happens when the school's seat/global budget caps a seat. The
        ceiling must be the allocation. Returning the plan default would
        tell the teacher they had used 16% when they had used 100%.
        """
        admin = self._user(
            "ceiling.admin1@test.dev", UserTypes.SCHOOL_ADMIN, self.school
        )
        licence = self._license(admin)
        teacher = self._user("ceiling.capped@test.dev", school=self.school)
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=teacher,
            monthly_allocation=8_000,
            is_active=True,
            next_credit_grant_at=timezone.now() + timedelta(days=30),
        )

        ceiling = get_monthly_credit_ceiling_for_user(teacher)

        self.assertEqual(ceiling, 8_000)
        self.assertNotEqual(
            ceiling,
            self.license_plan.monthly_credits,
            "the teacher was measured against the plan default instead of "
            "their real capped grant",
        )

    def test_an_inactive_allocation_does_not_set_a_ceiling(self):
        """A teacher dropped from the licence is no longer a licence teacher."""
        admin = self._user(
            "ceiling.admin2@test.dev", UserTypes.SCHOOL_ADMIN, self.school
        )
        licence = self._license(admin)
        teacher = self._user("ceiling.dropped@test.dev", school=self.school)
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=teacher,
            monthly_allocation=8_000,
            is_active=False,
            next_credit_grant_at=timezone.now() + timedelta(days=30),
        )

        self.assertEqual(get_monthly_credit_ceiling_for_user(teacher), 0)

    # --- LICENSE_ADMIN --------------------------------------------------

    def test_a_licence_admin_uses_their_own_admin_allocation(self):
        """
        The admin's analytics allocation is separate from, and independent
        of, the plan's teacher-facing monthly_credits.
        """
        admin = self._user(
            "ceiling.admin3@test.dev", UserTypes.SCHOOL_ADMIN, self.school
        )
        licence = self._license(admin)
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=admin,
            monthly_allocation=2_500,
            is_admin_allocation=True,
            is_active=True,
            next_credit_grant_at=timezone.now() + timedelta(days=30),
        )

        self.assertEqual(get_monthly_credit_ceiling_for_user(admin), 2_500)

    def test_a_licence_admin_without_an_allocation_reports_zero(self):
        admin = self._user(
            "ceiling.admin4@test.dev", UserTypes.SCHOOL_ADMIN, self.school
        )
        self._license(admin)

        self.assertEqual(get_monthly_credit_ceiling_for_user(admin), 0)

    def test_one_user_cannot_hold_two_allocations_under_one_licence(self):
        """
        The teacher branch excludes `is_admin_allocation=True` rows so an
        admin's own analytics allocation is never read as a teacher
        enrollment. It turns out the DATABASE makes that ambiguity
        impossible in the first place: `(license_subscription, user)` is
        UNIQUE, so a user holds at most one allocation per licence.

        Pinned because that constraint is doing real work. If it were ever
        relaxed — say to let an admin also teach on their own licence —
        the two allocations would become simultaneously live and the
        exclusion in the resolver would suddenly be load-bearing rather
        than belt-and-braces. This test is where that would surface.
        """
        from django.db import IntegrityError, transaction

        admin = self._user(
            "ceiling.admin5@test.dev", UserTypes.SCHOOL_ADMIN, self.school
        )
        licence = self._license(admin)
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=admin,
            monthly_allocation=2_500,
            is_admin_allocation=True,
            is_active=True,
            next_credit_grant_at=timezone.now() + timedelta(days=30),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SchoolCreditAllocation.objects.create(
                    license_subscription=licence,
                    user=admin,
                    monthly_allocation=9_999,
                    is_admin_allocation=False,
                    is_active=True,
                    next_credit_grant_at=timezone.now() + timedelta(days=30),
                )

        # The admin still reads their own, single, admin figure.
        self.assertEqual(get_monthly_credit_ceiling_for_user(admin), 2_500)

    # --- no billing context ----------------------------------------------

    def test_a_user_with_no_billing_context_reports_zero(self):
        stranger = self._user("ceiling.stranger@test.dev")

        self.assertEqual(get_monthly_credit_ceiling_for_user(stranger), 0)

    # --- resolution order -------------------------------------------------

    def test_an_individual_subscription_outranks_a_licence_role(self):
        """
        Documented, deliberate precedence: a directly-paid-for subscription
        always wins over any licence role, so a teacher who buys their own
        plan is measured against what they paid for.
        """
        admin = self._user(
            "ceiling.admin6@test.dev", UserTypes.SCHOOL_ADMIN, self.school
        )
        licence = self._license(admin)
        both = self._user("ceiling.both@test.dev", school=self.school)
        SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=both,
            monthly_allocation=8_000,
            is_active=True,
            next_credit_grant_at=timezone.now() + timedelta(days=30),
        )
        now = timezone.now()
        UserSubscription.objects.create(
            user=both,
            plan=self.individual_plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )

        self.assertEqual(get_monthly_credit_ceiling_for_user(both), 12_345)
