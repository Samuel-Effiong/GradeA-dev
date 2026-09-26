"""
billing/tests/test_subscription_me_status.py
==============================================
Coverage for GET /subscription/me ALWAYS returning 200 with a `status`
field (ACTIVE / EXPIRED / NONE) instead of 404ing when the caller has no
active subscription.

- ACTIVE: unchanged existing behavior for all three
  subscription_source tracks (INDIVIDUAL / LICENSE_TEACHER /
  LICENSE_ADMIN), just carrying the new "status": "ACTIVE" field.
- EXPIRED: INDIVIDUAL track only (per the task's scope) — no active
  UserSubscription/license context, but the caller has a most-recent
  inactive UserSubscription. Same MySubscriptionSerializer shape, with
  next_renewal_date/days_until_renewal nulled out (a lapsed cycle has
  no meaningful "days until renewal").
- NONE: no UserSubscription has ever existed and no license context —
  a flat placeholder payload, everything null/false/0 except "status".

Also covers precedence: an ACTIVE license context always wins over an
inactive individual UserSubscription lying around on the same user (the
resolver's documented first-match-wins order), so EXPIRED is only ever
reached once resolve_user_billing_context returns source=None.
"""

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
