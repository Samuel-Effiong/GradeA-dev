"""
billing/tests_h265_user_facing_errors.py
========================================
H-265: a library ValueError never reaches a client as text.

About 20 views answered ``{"detail": str(exc)}`` for any caught ValueError,
so a ValueError raised by a library inside the service call (Stripe SDK,
Django, Decimal, int()) was shown verbatim. Each site now answers a fixed
sentence for a bare ValueError, with the same status code as before, and
only the user-facing classes (BillingRequestError, LicenseRequestError,
dashboard QueryParamError) pass their text through.

Two kinds of test, per docs/evidence/h265-user-facing-errors/DESIGN.md:

- ``test_<site>_hides_library_error_text``: the called service is patched to
  raise ``ValueError("sk_live_LEAK internal detail")``; the response message
  is the site's fallback sentence and "sk_live_LEAK" is nowhere in the body.
  The API renderer (users/renderers.py) wraps every error body, so the shown
  text is ``response.json()["message"]``.
- ``test_<site>_shows_a_written_refusal`` (sites 2, 3, 12): the service is
  real and is driven into one of its own refusals; that sentence still
  reaches the client.

Run with:
    python manage.py test billing.tests_h265_user_facing_errors
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BillingInterval,
    CreditWallet,
    LicenseBillingMethod,
    LicenseOverageOfflineRequest,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.qa_time_travel import QATimeTravelService
from billing.services import ManualCreditService
from billing.stripe_service import (
    IndividualPlanChangeService,
    StripeCheckoutService,
    StripeOverageService,
    StripeSubscriptionScheduleService,
    SubscriptionReactivationService,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

LEAK = "sk_live_LEAK internal detail"


def library_error():
    return ValueError(LEAK)


def make_superadmin(email):
    return CustomUser.objects.create_superuser(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        first_name="Super",
        last_name="Admin",
        user_type=UserTypes.SUPER_ADMIN,
    )


def make_user(email, user_type, **extra):
    return CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        first_name="Test",
        last_name="User",
        user_type=user_type,
        is_active=True,
        **extra,
    )


def make_individual_plan(name, tier, price_cents, stripe_price_id, **extra):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=BillingInterval.MONTHLY,
        price_cents=price_cents,
        monthly_credits=30_000_000,
        stripe_price_id=stripe_price_id,
        is_active=True,
        **extra,
    )


class LeakAssertions(APITestCase):
    """Shared assertion; no tests of its own, so the runner finds none here."""

    def assert_hidden(self, response, status_code, fallback):
        self.assertEqual(response.status_code, status_code, response.content)
        self.assertEqual(response.json()["message"], fallback)
        self.assertNotIn(b"sk_live_LEAK", response.content)


# ----------------------------------------------------------------------
# billing/license_views.py (sites 1-9) and serializers.py (site 18)
# ----------------------------------------------------------------------


class LicenseRouteTests(LeakAssertions):
    def setUp(self):
        cache.clear()
        mail = patch("billing.license_service.send_email_task")
        mail.start()
        self.addCleanup(mail.stop)

        self.school = School.objects.create(name="H265 School")
        self.school_admin = make_user(
            "admin@h265-school.edu", UserTypes.SCHOOL_ADMIN, school=self.school
        )
        self.teacher = make_user(
            "teacher@h265-school.edu", UserTypes.TEACHER, school=self.school
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H265 License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            overage_block_size=5000,
            overage_block_price=299,
            stripe_price_id="price_h265_license",
        )
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.school_admin,
            plan=self.plan,
            billing_method=LicenseBillingMethod.OFFLINE,
            max_seats=5,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.superadmin = make_superadmin("root@h265.example")
        self.client.force_authenticate(user=self.superadmin)

    def detail_url(self, action):
        return reverse(
            f"license-subscription-{action}", kwargs={"pk": self.license_sub.pk}
        )

    def create_payload(self, billing_method):
        return {
            "school": str(self.school.id),
            "plan": str(self.plan.id),
            "contract_months": 12,
            "max_seats": 2,
            "billing_method": billing_method,
            "custom_price_cents": 10000,
            "teacher_emails": [],
        }

    # site 1: license_views.py:326
    def test_create_license_session_hides_library_error_text(self):
        with patch.object(
            StripeCheckoutService,
            "create_license_session",
            side_effect=library_error(),
        ):
            response = self.client.post(
                reverse("license-subscription-list"),
                self.create_payload(LicenseBillingMethod.STRIPE),
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't start the checkout. Please try again.",
        )

    # site 2: license_views.py:669
    def test_change_plan_hides_library_error_text(self):
        with patch.object(
            LicenseSubscriptionService, "select_plan", side_effect=library_error()
        ):
            response = self.client.post(
                self.detail_url("change-plan"),
                {"plan": str(self.plan.id)},
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't change the license plan. Please try again.",
        )

    def test_change_plan_shows_a_written_refusal(self):
        response = self.client.post(
            self.detail_url("change-plan"), {"plan": str(self.plan.id)}, format="json"
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertEqual(
            response.json()["message"],
            "License is already on this plan with the same price.",
        )

    # site 3: license_views.py:722
    def test_update_seats_hides_library_error_text(self):
        with patch.object(
            LicenseSubscriptionService, "update_seats", side_effect=library_error()
        ):
            response = self.client.post(
                self.detail_url("update-seats"), {"max_seats": 7}, format="json"
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't update the seats. Please try again.",
        )

    def test_update_seats_shows_a_written_refusal(self):
        response = self.client.post(
            self.detail_url("update-seats"), {"max_seats": 5}, format="json"
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertEqual(
            response.json()["message"], "License already has this many seats."
        )

    # site 4: license_views.py:789
    def test_purchase_overage_hides_library_error_text(self):
        with patch.object(
            LicenseSubscriptionService,
            "initiate_overage_purchase",
            side_effect=library_error(),
        ):
            response = self.client.post(
                self.detail_url("purchase-overage"),
                {"total_blocks": 1, "allocations": {str(self.teacher.id): 1}},
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't start the overage purchase. Please try again.",
        )

    # site 5: license_views.py:865
    def test_renew_offline_hides_library_error_text(self):
        with patch.object(
            LicenseSubscriptionService,
            "process_offline_renewal",
            side_effect=library_error(),
        ):
            response = self.client.post(
                self.detail_url("renew-offline"),
                {
                    "new_billing_cycle_end": (
                        timezone.now() + timedelta(days=395)
                    ).isoformat()
                },
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't renew the license. Please try again.",
        )

    # site 6: license_views.py:900
    def test_convert_to_stripe_hides_library_error_text(self):
        with patch.object(
            StripeCheckoutService,
            "create_license_conversion_session",
            side_effect=library_error(),
        ):
            response = self.client.post(
                self.detail_url("convert-to-stripe"), {}, format="json"
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't start the switch to card billing. Please try again.",
        )

    # site 7: license_views.py:928
    def test_convert_to_offline_hides_library_error_text(self):
        with patch.object(
            LicenseSubscriptionService,
            "convert_license_to_offline",
            side_effect=library_error(),
        ):
            response = self.client.post(
                self.detail_url("convert-to-offline"), {}, format="json"
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't switch the license to offline billing. Please try again.",
        )

    # site 8: license_views.py:964
    def test_cancel_license_hides_library_error_text(self):
        with patch.object(
            LicenseSubscriptionService,
            "cancel_license_subscription",
            side_effect=library_error(),
        ):
            response = self.client.post(self.detail_url("cancel"), {}, format="json")

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't cancel the license. Please try again.",
        )

    # site 9: license_views.py:1002
    def test_grant_teacher_overage_hides_library_error_text(self):
        with patch.object(
            LicenseSubscriptionService,
            "grant_manual_teacher_overage",
            side_effect=library_error(),
        ):
            response = self.client.post(
                self.detail_url("grant-teacher-overage"),
                {"teacher_id": str(self.teacher.id), "blocks": 1},
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't grant the overage credits. Please try again.",
        )

    # site 18: serializers.py:2183 (LicenseSubscriptionSerializer.validate,
    # reached through POST license-subscriptions). The field error
    # {"admin_user": ...} is flattened with its field label.
    def test_resolve_admin_user_hides_library_error_text(self):
        with patch.object(
            LicenseSubscriptionService,
            "resolve_admin_user",
            side_effect=library_error(),
        ):
            response = self.client.post(
                reverse("license-subscription-list"),
                self.create_payload(LicenseBillingMethod.OFFLINE),
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "Admin user: This admin user can't be used.",
        )


# ----------------------------------------------------------------------
# billing/license_overage_offline_views.py (sites 14, 15)
# ----------------------------------------------------------------------


class OfflineOverageRequestRouteTests(LeakAssertions):
    def setUp(self):
        self.school = School.objects.create(name="H265 Offline School")
        self.school_admin = make_user(
            "admin@h265-offline.edu", UserTypes.SCHOOL_ADMIN, school=self.school
        )
        self.teacher = make_user(
            "teacher@h265-offline.edu", UserTypes.TEACHER, school=self.school
        )
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H265 Offline License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            overage_block_size=5000,
            overage_block_price=299,
        )
        license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.school_admin,
            plan=plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.request_row = LicenseOverageOfflineRequest.objects.create(
            license_subscription=license_sub,
            requested_by=self.school_admin,
            total_blocks=1,
            allocations={str(self.teacher.id): 1},
            block_size_snapshot=5000,
            unit_price_cents_snapshot=299,
            amount_cents_quoted=299,
        )
        self.client.force_authenticate(
            user=make_superadmin("root@h265-offline.example")
        )

    # site 14: license_overage_offline_views.py:115
    def test_approve_overage_offline_request_hides_library_error_text(self):
        with patch.object(
            LicenseSubscriptionService,
            "approve_overage_offline_request",
            side_effect=library_error(),
        ):
            response = self.client.post(
                reverse(
                    "license-overage-offline-request-approve",
                    kwargs={"pk": self.request_row.pk},
                ),
                {"amount_confirmed_cents": 299},
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't approve this request. Please try again.",
        )

    # site 15: license_overage_offline_views.py:143
    def test_reject_overage_offline_request_hides_library_error_text(self):
        with patch.object(
            LicenseSubscriptionService,
            "reject_overage_offline_request",
            side_effect=library_error(),
        ):
            response = self.client.post(
                reverse(
                    "license-overage-offline-request-reject",
                    kwargs={"pk": self.request_row.pk},
                ),
                {"rejection_reason": "Payment never arrived."},
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't reject this request. Please try again.",
        )


# ----------------------------------------------------------------------
# billing/views.py (sites 10-13)
# ----------------------------------------------------------------------


class SubscriptionRouteTests(LeakAssertions):
    def setUp(self):
        # The cancel/resume/select-plan routes share a cache-backed per-user
        # lock; a leaked key would answer every request with a 400.
        cache.clear()
        self.user = make_user("h265-individual@example.com", UserTypes.TEACHER)
        self.plan = make_individual_plan(
            PlanType.PRO,
            PlanTier.PRO,
            2999,
            "price_h265_pro",
            overage_block_size=500,
            overage_block_price=400,
            max_overage_blocks=10,
            stripe_overage_price_id="price_h265_overage",
        )
        CreditWallet.objects.get_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_h265"}
        )
        self.client.force_authenticate(user=self.user)

    def make_sub(self, **overrides):
        now = timezone.now()
        defaults = {
            "user": self.user,
            "plan": self.plan,
            "is_active": True,
            "auto_renew": True,
            "is_trial": False,
            "billing_cycle_start": now,
            "billing_cycle_end": now + relativedelta(months=1),
            "next_credit_grant_at": now + relativedelta(months=1),
            "stripe_subscription_id": "sub_h265",
            "stripe_status": StripeSubscriptionStatus.ACTIVE,
        }
        defaults.update(overrides)
        return UserSubscription.objects.create(**defaults)

    def select_plan_payload(self):
        return {
            "plan_id": str(self.plan.id),
            "success_url": "https://example.com/ok",
            "cancel_url": "https://example.com/cancel",
        }

    # site 10: views.py:978
    def test_release_schedule_on_cancel_hides_library_error_text(self):
        self.make_sub(stripe_schedule_id="sub_sched_h265")

        with patch.object(
            StripeSubscriptionScheduleService,
            "release_schedule",
            side_effect=library_error(),
        ):
            response = self.client.post(reverse("subscription-cancel"))

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't cancel the scheduled plan change. Please try again.",
        )

    # site 11: views.py:1178
    def test_resume_hides_library_error_text(self):
        self.make_sub(auto_renew=False)

        with patch.object(
            SubscriptionReactivationService,
            "reactivate_if_cancelling",
            side_effect=library_error(),
        ):
            response = self.client.post(reverse("subscription-resume"))

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't resume the subscription. Please try again.",
        )

    # site 12: views.py:1295
    def test_select_plan_hides_library_error_text(self):
        self.make_sub()

        with patch.object(
            IndividualPlanChangeService, "select_plan", side_effect=library_error()
        ):
            response = self.client.post(
                reverse("subscription-select-plan"),
                self.select_plan_payload(),
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't change your plan. Please try again.",
        )

    def test_select_plan_shows_a_written_refusal(self):
        self.make_sub()

        response = self.client.post(
            reverse("subscription-select-plan"),
            self.select_plan_payload(),
            format="json",
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertEqual(
            response.json()["message"], "You are already subscribed to this plan."
        )

    # site 13: views.py:1343
    def test_purchase_individual_overage_hides_library_error_text(self):
        self.make_sub()

        with patch.object(
            StripeOverageService,
            "create_overage_checkout_session",
            side_effect=library_error(),
        ):
            response = self.client.post(
                reverse("subscription-purchase-overage"),
                {
                    "success_url": "https://example.com/ok",
                    "cancel_url": "https://example.com/cancel",
                    "quantity": 1,
                },
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't start the overage checkout. Please try again.",
        )


# ----------------------------------------------------------------------
# billing/qa_time_travel.py (site 19)
# ----------------------------------------------------------------------


class QATimeTravelRouteTests(LeakAssertions):
    def setUp(self):
        user = make_user("h265-qa@example.com", UserTypes.TEACHER)
        plan = make_individual_plan(
            PlanType.STANDARD, PlanTier.STANDARD, 999, "price_h265_qa"
        )
        now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=1),
        )
        self.client.force_authenticate(user=make_superadmin("root@h265-qa.example"))

    # site 19: qa_time_travel.py:1018
    @override_settings(ENABLE_BILLING_TIME_TRAVEL=True)
    @patch("billing.qa_time_travel.stripe.api_key", "sk_test_h265")
    def test_rewind_individual_subscription_hides_library_error_text(self):
        with patch.object(
            QATimeTravelService,
            "rewind_individual_subscription",
            side_effect=library_error(),
        ):
            response = self.client.post(
                reverse("qa-time-travel"),
                {
                    "subscription_type": "INDIVIDUAL",
                    "subscription_id": str(self.sub.pk),
                    "mode": "full_renewal",
                    "advance_stripe_test_clock": False,
                },
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "The time-travel request failed. Please try again.",
        )


# ----------------------------------------------------------------------
# billing/views_admin_credits.py (site 20)
# ----------------------------------------------------------------------


class AdminCreditRouteTests(LeakAssertions):
    def setUp(self):
        self.teacher = make_user("h265-credit@example.com", UserTypes.TEACHER)
        self.client.force_authenticate(user=make_superadmin("root@h265-credit.example"))

    # site 20: views_admin_credits.py:143 (raised as a DRF
    # ValidationError(str(e)), a list body, flattened to the one sentence)
    def test_top_up_credits_hides_library_error_text(self):
        with patch.object(
            ManualCreditService, "top_up_credits", side_effect=library_error()
        ):
            response = self.client.post(
                reverse("admin-credits-grant"),
                {"user_id": str(self.teacher.id), "blocks": 2, "reason": "goodwill"},
                format="json",
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "We couldn't top up the credits. Please try again.",
        )


# ----------------------------------------------------------------------
# dashboard/views.py (sites 16, 17)
# ----------------------------------------------------------------------


class DashboardQueryParamRouteTests(LeakAssertions):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="H265 Dashboard School")

    # site 16: dashboard/views.py:2515
    def test_unit_performance_hides_library_error_text(self):
        admin = make_user(
            "admin@h265-dashboard.edu", UserTypes.SCHOOL_ADMIN, school=self.school
        )
        self.client.force_authenticate(user=admin)

        with patch("dashboard.views._bounded_int_param", side_effect=library_error()):
            response = self.client.get(reverse("school-admin-unit-performance"))

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "One of the query parameters is not valid.",
        )

    # site 17: dashboard/views.py:3586
    def test_teacher_assignment_analytics_hides_library_error_text(self):
        teacher = make_user(
            "teacher@h265-dashboard.edu", UserTypes.TEACHER, school=self.school
        )
        self.client.force_authenticate(user=teacher)

        with patch("dashboard.views._bounded_int_param", side_effect=library_error()):
            response = self.client.get(
                reverse(
                    "teacher-admin-assignments",
                    kwargs={"assignment_id": str(uuid.uuid4())},
                )
            )

        self.assert_hidden(
            response,
            status.HTTP_400_BAD_REQUEST,
            "One of the query parameters is not valid.",
        )
