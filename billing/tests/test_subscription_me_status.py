"""
billing/tests/test_subscription_me_status.py
==============================================
Coverage for GET /subscription/me ALWAYS returning 200 with a `status`
field (ACTIVE / EXPIRED / NONE) instead of 404ing when the caller has no
active subscription.

- ACTIVE: unchanged existing behavior for all three
  subscription_source tracks (INDIVIDUAL / LICENSE_TEACHER /
  LICENSE_ADMIN), just carrying the new "status": "ACTIVE" field.
- EXPIRED: now covers all three tracks, checked in the SAME priority
  order as the ACTIVE case (individual, then license-admin, then
  license-teacher — see PriorityOrderExpiredTests). Each reuses its
  ACTIVE-case serializer shape, with renewal-countdown fields
  (next_renewal_date/days_until_renewal/days_until_next_credit_grant)
  nulled out instead of a misleading clamped 0 (a lapsed cycle has no
  meaningful "days until renewal"). A license-teacher's EXPIRED
  lookup also covers the "only the license itself lapsed, the
  teacher's own allocation row was never touched" shape — see
  LicenseTeacherExpiredTests.
- NONE: no subscription/license history exists on ANY of the three
  tracks — a flat placeholder payload, everything null/false/0 except
  "status".

Also covers precedence: an ACTIVE context on any track always wins
over stale/inactive history on a lower-priority track (the resolver's
documented first-match-wins order applies identically to the EXPIRED
fallback chain), so EXPIRED is only ever reached once
resolve_user_billing_context returns source=None.
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.context import (
    clear_license_invitation_context,
    set_license_invitation_context,
)
from billing.models import (
    BillingInterval,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    SchoolCreditAllocation,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()
PASSWORD = "testpass123"  # pragma: allowlist secret


def make_plan(name, tier, monthly_credits, category=PlanCategory.INDIVIDUAL):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=category,
        tier=tier,
        interval=BillingInterval.MONTHLY,
        price_cents=999,
        monthly_credits=monthly_credits,
        stripe_price_id=f"price_{name.lower()}",
        is_active=True,
    )


class SubscriptionMeStatusTestBase(APITestCase):
    def setUp(self):
        self.url = reverse("subscription-get-my-subscription")
        self.plan = make_plan("PRO", PlanTier.PRO, 30_000_000)
        self.user = CustomUser.objects.create_user(
            email="me-status@example.com",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
        )
        CreditWallet.objects.get_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_me_status_1"}
        )
        self.client.force_authenticate(user=self.user)

    def make_sub(self, **overrides):
        defaults = {
            "user": self.user,
            "plan": self.plan,
            "is_active": True,
            "auto_renew": True,
            "billing_cycle_start": timezone.now(),
            "billing_cycle_end": timezone.now() + timedelta(days=30),
            "stripe_status": StripeSubscriptionStatus.ACTIVE,
        }
        defaults.update(overrides)
        return UserSubscription.objects.create(**defaults)


class ActiveStatusTests(SubscriptionMeStatusTestBase):
    def test_active_individual_subscription_reports_status_active(self):
        self.make_sub()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["subscription_type"], "INDIVIDUAL")
        self.assertEqual(response.data["status"], "ACTIVE")
        # Unaffected by this change: still populated for a genuinely
        # live cycle.
        self.assertIsNotNone(response.data["next_renewal_date"])
        self.assertIsInstance(response.data["days_until_renewal"], int)

    def test_active_license_teacher_reports_status_active(self):
        school = School.objects.create(name="Me-Status School T")
        admin = CustomUser.objects.create_user(
            email="admin-t@example.com",
            password=PASSWORD,
            user_type=UserTypes.SCHOOL_ADMIN,
            is_active=True,
            school=school,
        )
        set_license_invitation_context(True)
        try:
            teacher = CustomUser.objects.create_user(
                email="teacher-t@example.com",
                password=PASSWORD,
                user_type=UserTypes.TEACHER,
                is_active=True,
                school=school,
            )
        finally:
            clear_license_invitation_context()
        license_plan = make_plan(
            "LICENSE_T", PlanTier.CUSTOM, 40_000_000, category=PlanCategory.LICENSE
        )
        license_sub = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=license_plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=5,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=teacher,
            is_active=True,
            is_admin_allocation=False,
            monthly_allocation=1000,
        )
        self.client.force_authenticate(user=teacher)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["subscription_source"], "LICENSE_TEACHER")
        self.assertEqual(response.data["status"], "ACTIVE")

    def test_active_license_admin_reports_status_active(self):
        school = School.objects.create(name="Me-Status School A")
        admin = CustomUser.objects.create_user(
            email="admin-a@example.com",
            password=PASSWORD,
            user_type=UserTypes.SCHOOL_ADMIN,
            is_active=True,
            school=school,
        )
        license_plan = make_plan(
            "LICENSE_A", PlanTier.CUSTOM, 40_000_000, category=PlanCategory.LICENSE
        )
        license_sub = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=license_plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=5,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=admin,
            is_active=True,
            is_admin_allocation=True,
            monthly_allocation=1000,
        )
        self.client.force_authenticate(user=admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["subscription_source"], "LICENSE_ADMIN")
        self.assertEqual(response.data["status"], "ACTIVE")


class ExpiredStatusTests(SubscriptionMeStatusTestBase):
    def test_lapsed_individual_subscription_reports_status_expired(self):
        self.make_sub(
            is_active=False,
            auto_renew=False,
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
            stripe_status=StripeSubscriptionStatus.CANCELED,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], "EXPIRED")
        self.assertEqual(response.data["subscription_type"], "INDIVIDUAL")
        self.assertFalse(response.data["is_active"])
        self.assertEqual(
            response.data["stripe_status"], StripeSubscriptionStatus.CANCELED
        )

    def test_expired_nulls_out_renewal_fields_instead_of_negative_days(self):
        """
        The pre-existing get_days_until_renewal did
        `obj.billing_cycle_end - now` with no floor — for a lapsed cycle
        that's negative, and max(0, ...) would have silently hidden it
        as 0 (looks like "renews today"). EXPIRED must report null, not
        a misleading 0.
        """
        self.make_sub(
            is_active=False,
            auto_renew=False,
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
        )

        response = self.client.get(self.url)

        self.assertIsNone(response.data["next_renewal_date"])
        self.assertIsNone(response.data["days_until_renewal"])

    def test_expired_still_carries_cancellation_reason(self):
        cancelled_at = timezone.now() - timedelta(days=45)
        self.make_sub(
            is_active=False,
            auto_renew=False,
            cancelled_at=cancelled_at,
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
            stripe_status=StripeSubscriptionStatus.CANCELED,
        )

        response = self.client.get(self.url)

        self.assertEqual(
            response.data["stripe_status"], StripeSubscriptionStatus.CANCELED
        )
        self.assertEqual(response.data["cancellation"]["cancelled_at"], cancelled_at)

    def test_most_recent_inactive_subscription_is_the_one_returned(self):
        self.make_sub(
            is_active=False,
            billing_cycle_start=timezone.now() - timedelta(days=120),
            billing_cycle_end=timezone.now() - timedelta(days=90),
            stripe_status=StripeSubscriptionStatus.CANCELED,
        )
        newer_plan = make_plan("STANDARD_NEWER", PlanTier.STANDARD, 10_000_000)
        newer = self.make_sub(
            is_active=False,
            plan=newer_plan,
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
            stripe_status=StripeSubscriptionStatus.CANCELED,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.data["status"], "EXPIRED")
        self.assertEqual(str(response.data["plan"]["id"]), str(newer_plan.pk))
        self.assertEqual(str(response.data["id"]), str(newer.pk))

    def test_active_license_context_takes_precedence_over_inactive_individual_sub(self):
        """
        resolve_user_billing_context's documented resolution order means
        an ACTIVE license allocation always wins over a stale, inactive
        individual UserSubscription on the same user — EXPIRED must
        never surface for someone who is actually a live license
        teacher today.
        """
        self.make_sub(
            is_active=False,
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
        )
        school = School.objects.create(name="Precedence School")
        admin = CustomUser.objects.create_user(
            email="precedence-admin@example.com",
            password=PASSWORD,
            user_type=UserTypes.SCHOOL_ADMIN,
            is_active=True,
            school=school,
        )
        license_plan = make_plan(
            "LICENSE_P", PlanTier.CUSTOM, 40_000_000, category=PlanCategory.LICENSE
        )
        license_sub = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=license_plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=5,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=self.user,
            is_active=True,
            is_admin_allocation=False,
            monthly_allocation=1000,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["subscription_source"], "LICENSE_TEACHER")
        self.assertEqual(response.data["status"], "ACTIVE")


class NoneStatusTests(SubscriptionMeStatusTestBase):
    def test_user_who_never_had_a_subscription_reports_status_none(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], "NONE")

    def test_none_response_matches_the_active_shape_with_safe_defaults(self):
        self.make_sub()  # baseline ACTIVE response for the same user
        active_response = self.client.get(self.url)
        UserSubscription.objects.filter(user=self.user).delete()

        none_response = self.client.get(self.url)

        self.assertEqual(none_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(none_response.data.keys()), set(active_response.data.keys())
        )
        self.assertIsNone(none_response.data["id"])
        self.assertIsNone(none_response.data["plan"])
        self.assertFalse(none_response.data["is_active"])
        self.assertFalse(none_response.data["auto_renew"])
        self.assertIsNone(none_response.data["next_renewal_date"])
        self.assertIsNone(none_response.data["days_until_renewal"])
        self.assertEqual(
            none_response.data["cancellation"],
            {
                "cancelled_at": None,
                "has_pending_cancellation": False,
                "cancellation_effective_date": None,
                "cancellation_message": None,
            },
        )
        self.assertEqual(none_response.data["current_balance_display"], 0)
        self.assertEqual(none_response.data["credit_percentage_remaining"], 0.0)


def make_license_school(admin_email, teacher_email=None):
    school = School.objects.create(name=f"License School {admin_email}")
    admin = CustomUser.objects.create_user(
        email=admin_email,
        password=PASSWORD,
        user_type=UserTypes.SCHOOL_ADMIN,
        is_active=True,
        school=school,
    )
    teacher = None
    if teacher_email:
        set_license_invitation_context(True)
        try:
            teacher = CustomUser.objects.create_user(
                email=teacher_email,
                password=PASSWORD,
                user_type=UserTypes.TEACHER,
                is_active=True,
                school=school,
            )
        finally:
            clear_license_invitation_context()
    return school, admin, teacher


class LicenseAdminExpiredTests(SubscriptionMeStatusTestBase):
    def _make_lapsed_license(self, admin, **overrides):
        license_plan = make_plan(
            f"LICENSE_LAPSED_A_{uuid.uuid4().hex[:8]}",
            PlanTier.CUSTOM,
            40_000_000,
            category=PlanCategory.LICENSE,
        )
        defaults = {
            "school": School.objects.create(
                name=f"Lapsed School {uuid.uuid4().hex[:8]}"
            ),
            "admin_user": admin,
            "plan": license_plan,
            "billing_cycle_start": timezone.now() - timedelta(days=60),
            "billing_cycle_end": timezone.now() - timedelta(days=30),
            "is_active": False,
            "auto_renew": False,
            "stripe_status": StripeSubscriptionStatus.CANCELED,
            "max_seats": 10,
        }
        defaults.update(overrides)
        return LicenseSubscription.objects.create(**defaults)

    def test_lapsed_license_admin_reports_status_expired(self):
        _, admin, _ = make_license_school("lapsed-admin-1@example.com")
        self._make_lapsed_license(admin)
        self.client.force_authenticate(user=admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["subscription_source"], "LICENSE_ADMIN")
        self.assertEqual(response.data["status"], "EXPIRED")
        self.assertFalse(response.data["is_active"])
        self.assertEqual(
            response.data["stripe_status"], StripeSubscriptionStatus.CANCELED
        )

    def test_expired_license_admin_nulls_days_until_renewal(self):
        _, admin, _ = make_license_school("lapsed-admin-2@example.com")
        self._make_lapsed_license(admin)
        self.client.force_authenticate(user=admin)

        response = self.client.get(self.url)

        self.assertIsNone(response.data["days_until_renewal"])

    def test_expired_license_admin_reports_zero_managed_licenses(self):
        """
        managed_license_count/has_other_managed_licenses must reflect
        that there are 0 ACTIVE licenses right now — the ACTIVE-path
        default of 1 would wrongly tell the frontend this admin still
        manages one live license.
        """
        _, admin, _ = make_license_school("lapsed-admin-3@example.com")
        self._make_lapsed_license(admin)
        self.client.force_authenticate(user=admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data["managed_license_count"], 0)
        self.assertFalse(response.data["has_other_managed_licenses"])

    def test_most_recent_lapsed_license_is_the_one_returned(self):
        _, admin, _ = make_license_school("lapsed-admin-4@example.com")
        self._make_lapsed_license(
            admin,
            billing_cycle_start=timezone.now() - timedelta(days=200),
            billing_cycle_end=timezone.now() - timedelta(days=170),
        )
        newer = self._make_lapsed_license(
            admin,
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
        )
        self.client.force_authenticate(user=admin)

        response = self.client.get(self.url)

        self.assertEqual(str(response.data["license_id"]), str(newer.pk))


class LicenseTeacherExpiredTests(SubscriptionMeStatusTestBase):
    def _make_license(self, admin, **overrides):
        license_plan = make_plan(
            f"LICENSE_LAPSED_T_{uuid.uuid4().hex[:8]}",
            PlanTier.CUSTOM,
            40_000_000,
            category=PlanCategory.LICENSE,
        )
        defaults = {
            "school": School.objects.create(name=f"Teacher-Lapsed {admin.email}"),
            "admin_user": admin,
            "plan": license_plan,
            "billing_cycle_start": timezone.now() - timedelta(days=60),
            "billing_cycle_end": timezone.now() - timedelta(days=30),
            "is_active": True,
            "max_seats": 10,
        }
        defaults.update(overrides)
        return LicenseSubscription.objects.create(**defaults)

    def test_teachers_own_allocation_deactivated_reports_status_expired(self):
        """
        Shape (a): the license itself is still fine, but this teacher's
        own allocation was deactivated (e.g. removed from the course).
        is_license_active must stay True — it's the teacher's own
        history that lapsed, not the school's.
        """
        _, admin, _ = make_license_school("teacher-exp-admin-1@example.com")
        license_sub = self._make_license(admin, is_active=True)
        teacher = CustomUser.objects.create_user(
            email="teacher-exp-1@example.com",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=teacher,
            is_active=False,
            is_admin_allocation=False,
            monthly_allocation=1000,
        )
        self.client.force_authenticate(user=teacher)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["subscription_source"], "LICENSE_TEACHER")
        self.assertEqual(response.data["status"], "EXPIRED")
        self.assertTrue(response.data["is_license_active"])

    def test_license_itself_lapsed_reports_status_expired_and_is_license_active_false(
        self,
    ):
        """
        Shape (b): the teacher's OWN allocation row was never touched
        (still is_active=True) but the school's license lapsed — the
        broadened query (NOT the SM's literal is_active=False spec)
        exists specifically to catch this. is_license_active must read
        False, reflecting the real parent-license state.
        """
        _, admin, _ = make_license_school("teacher-exp-admin-2@example.com")
        license_sub = self._make_license(
            admin,
            is_active=False,
            stripe_status=StripeSubscriptionStatus.CANCELED,
        )
        teacher = CustomUser.objects.create_user(
            email="teacher-exp-2@example.com",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=teacher,
            is_active=True,  # never touched
            is_admin_allocation=False,
            monthly_allocation=1000,
        )
        self.client.force_authenticate(user=teacher)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["subscription_source"], "LICENSE_TEACHER")
        self.assertEqual(response.data["status"], "EXPIRED")
        self.assertFalse(response.data["is_license_active"])

    def test_expired_teacher_nulls_days_until_next_credit_grant(self):
        _, admin, _ = make_license_school("teacher-exp-admin-3@example.com")
        license_sub = self._make_license(admin, is_active=False)
        teacher = CustomUser.objects.create_user(
            email="teacher-exp-3@example.com",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=teacher,
            is_active=True,
            is_admin_allocation=False,
            monthly_allocation=1000,
            next_credit_grant_at=timezone.now() - timedelta(days=5),
        )
        self.client.force_authenticate(user=teacher)

        response = self.client.get(self.url)

        self.assertIsNone(response.data["days_until_next_credit_grant"])

    def test_admin_allocation_never_surfaces_as_a_teacher_expired_row(self):
        """
        is_admin_allocation=True rows are the admin's own analytics
        grant, not a teacher enrollment — must stay excluded from the
        EXPIRED lookup exactly as the ACTIVE lookup excludes them.
        """
        _, admin, _ = make_license_school("teacher-exp-admin-4@example.com")
        license_sub = self._make_license(admin, is_active=False)
        SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=admin,
            is_active=True,
            is_admin_allocation=True,
            monthly_allocation=1000,
        )
        self.client.force_authenticate(user=admin)

        response = self.client.get(self.url)

        # admin's own /me call resolves via the LICENSE_ADMIN branch
        # (the lapsed license itself), never the admin-allocation row.
        self.assertEqual(response.data["subscription_source"], "LICENSE_ADMIN")
        self.assertEqual(response.data["status"], "EXPIRED")

    def test_most_recently_updated_lapsed_allocation_is_the_one_returned(self):
        """
        SchoolCreditAllocation has no billing_cycle_end, so ordering
        uses -updated_at as the closest "most recently changed" proxy
        (documented in the evidence doc).
        """
        _, admin, _ = make_license_school("teacher-exp-admin-5@example.com")
        older_license = self._make_license(admin, is_active=False)
        teacher = CustomUser.objects.create_user(
            email="teacher-exp-5@example.com",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        older_allocation = SchoolCreditAllocation.objects.create(
            license_subscription=older_license,
            user=teacher,
            is_active=False,
            is_admin_allocation=False,
            monthly_allocation=500,
        )
        newer_admin = make_license_school("teacher-exp-admin-6@example.com")[1]
        newer_license = self._make_license(newer_admin, is_active=False)
        newer_allocation = SchoolCreditAllocation.objects.create(
            license_subscription=newer_license,
            user=teacher,
            is_active=False,
            is_admin_allocation=False,
            monthly_allocation=2000,
        )
        # Touch the older row last so -updated_at, not creation order,
        # decides the winner if the test data were built the other way
        # around — force the intended ordering explicitly instead.
        SchoolCreditAllocation.objects.filter(pk=older_allocation.pk).update(
            updated_at=timezone.now() - timedelta(days=10)
        )
        SchoolCreditAllocation.objects.filter(pk=newer_allocation.pk).update(
            updated_at=timezone.now() - timedelta(days=1)
        )
        self.client.force_authenticate(user=teacher)

        response = self.client.get(self.url)

        self.assertEqual(response.data["monthly_allocation"], 2000)


class PriorityOrderExpiredTests(SubscriptionMeStatusTestBase):
    """
    A user with lapsed history on more than one track must get
    whichever track the ACTIVE-case priority order would have picked
    (individual > license-admin > license-teacher), not an arbitrary
    one — same rule as the ACTIVE precedence test above, applied to the
    EXPIRED fallback chain.
    """

    def test_expired_individual_history_wins_over_expired_license_admin_history(self):
        self.make_sub(
            is_active=False,
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
        )
        LicenseSubscription.objects.create(
            school=School.objects.create(name="Priority School A"),
            admin_user=self.user,
            plan=make_plan(
                "LICENSE_PRIORITY",
                PlanTier.CUSTOM,
                40_000_000,
                category=PlanCategory.LICENSE,
            ),
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
            is_active=False,
            max_seats=5,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.data["status"], "EXPIRED")
        self.assertEqual(response.data["subscription_type"], "INDIVIDUAL")

    def test_expired_license_admin_history_wins_over_expired_license_teacher_history(
        self,
    ):
        license_sub = LicenseSubscription.objects.create(
            school=School.objects.create(name="Priority School B"),
            admin_user=self.user,
            plan=make_plan(
                "LICENSE_PRIORITY_B",
                PlanTier.CUSTOM,
                40_000_000,
                category=PlanCategory.LICENSE,
            ),
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
            is_active=False,
            max_seats=5,
        )
        # self.user also has a stale TEACHER allocation elsewhere.
        other_admin = make_license_school("priority-other-admin@example.com")[1]
        other_license = LicenseSubscription.objects.create(
            school=School.objects.create(name="Priority School C"),
            admin_user=other_admin,
            plan=make_plan(
                "LICENSE_PRIORITY_C",
                PlanTier.CUSTOM,
                40_000_000,
                category=PlanCategory.LICENSE,
            ),
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
            is_active=False,
            max_seats=5,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=other_license,
            user=self.user,
            is_active=False,
            is_admin_allocation=False,
            monthly_allocation=500,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.data["status"], "EXPIRED")
        self.assertEqual(response.data["subscription_source"], "LICENSE_ADMIN")
        self.assertEqual(str(response.data["license_id"]), str(license_sub.pk))
