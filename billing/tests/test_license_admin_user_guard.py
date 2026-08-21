"""
billing/tests/test_license_admin_user_guard.py
=============================================
Locks down who may be named as a LicenseSubscription's `admin_user`.

BACKGROUND -- QA reported that a superadmin was ending up as the school
admin for schools that had been created. The two school-creation paths
(POST /schools/ and POST /schools/create_with_admin/) turned out to be
clean: neither touches the superadmin's account. The hole was on the
license side.

LicenseSubscriptionService.validate_admin_user() only rejected an
admin_user whose `school` was set AND different from the license's
school. A SUPER_ADMIN has school=None -- platform staff belong to no
tenant -- so they sailed straight through, and a license could be created
naming the superadmin as the school's managing admin. Consequences, all
reproduced before the fix:

  * the license reported the superadmin's email as the school's billing
    contact (LicenseSubscriptionSerializer.admin_email),
  * the school's admin credit allocation (is_admin_allocation=True) was
    granted to the superadmin's wallet instead of the real admin's,
  * IsSchoolAdminOrSuperAdmin.has_object_permission keyed off
    `obj.admin_user == request.user`, so the school's real SCHOOL_ADMIN
    was locked out of managing their own school's license.

Three changes close it:

  * validate_admin_user() requires positive school membership rather than
    merely an absence of contradiction,
  * admin_user is now OPTIONAL and derived from the school when omitted
    (resolve_admin_user) - a license belongs to a school, the school
    already knows who its admin is, so the caller can't get it wrong by
    not being asked,
  * the object permission is scoped by school instead of by admin_user.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.stripe_service import StripeCheckoutService
from classrooms.models import School
from users.models import CustomUser, UserTypes


def _make_plan():
    return SubscriptionPlan.objects.create(
        name=PlanType.PRO,
        display_name="Test License Plan",
        category=PlanCategory.LICENSE,
        tier=PlanTier.PRO,
        monthly_credits=20000,
    )


def _make_superadmin(email="superadmin@example.com"):
    superadmin = CustomUser.objects.create_superuser(
        email=email,
        password="password123",  # pragma: allowlist secret
        first_name="Super",
        last_name="Admin",
    )
    superadmin.user_type = UserTypes.SUPER_ADMIN
    superadmin.is_active = True
    superadmin.save()
    return superadmin


class ValidateAdminUserTest(TestCase):
    """Service-layer guard: LicenseSubscriptionService.validate_admin_user()."""

    def setUp(self):
        self.school = School.objects.create(name="Guard School")
        self.other_school = School.objects.create(name="Other School")
        self.school_admin = CustomUser.objects.create_user(
            email="admin@guard-school.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Ada",
            last_name="Lovelace",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )

    def test_accepts_the_schools_own_admin(self):
        # Should not raise.
        LicenseSubscriptionService.validate_admin_user(self.school_admin, self.school)

    def test_rejects_superadmin_with_no_school(self):
        """The reported bug: school=None used to mean 'no contradiction'."""
        superadmin = _make_superadmin()
        self.assertIsNone(superadmin.school_id)

        with self.assertRaises(ValueError) as ctx:
            LicenseSubscriptionService.validate_admin_user(superadmin, self.school)

        self.assertIn("super admin", str(ctx.exception).lower())

    def test_rejects_superadmin_even_when_attached_to_the_school(self):
        """Attaching the superadmin to the school must not buy them the role."""
        superadmin = _make_superadmin()
        superadmin.school = self.school
        superadmin.save(update_fields=["school"])

        with self.assertRaises(ValueError) as ctx:
            LicenseSubscriptionService.validate_admin_user(superadmin, self.school)

        self.assertIn("super admin", str(ctx.exception).lower())

    def test_rejects_is_superuser_flag_without_super_admin_user_type(self):
        """Either marker alone is enough to disqualify -- they can drift apart."""
        staff = CustomUser.objects.create_user(
            email="staff@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Staff",
            last_name="Member",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        staff.is_superuser = True
        staff.save(update_fields=["is_superuser"])

        with self.assertRaises(ValueError):
            LicenseSubscriptionService.validate_admin_user(staff, self.school)

    def test_rejects_user_with_no_school(self):
        schoolless = CustomUser.objects.create_user(
            email="nomad@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="No",
            last_name="School",
            user_type=UserTypes.SCHOOL_ADMIN,
        )

        with self.assertRaises(ValueError) as ctx:
            LicenseSubscriptionService.validate_admin_user(schoolless, self.school)

        self.assertIn("does not belong to any school", str(ctx.exception))

    def test_rejects_admin_of_a_different_school(self):
        outsider = CustomUser.objects.create_user(
            email="admin@other-school.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Other",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.other_school,
        )

        with self.assertRaises(ValueError) as ctx:
            LicenseSubscriptionService.validate_admin_user(outsider, self.school)

        self.assertIn("not authorized", str(ctx.exception))

    def test_still_rejects_students(self):
        student = CustomUser.objects.create_user(
            email="student@guard-school.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Stu",
            last_name="Dent",
            user_type=UserTypes.STUDENT,
            school=self.school,
        )

        with self.assertRaises(ValueError) as ctx:
            LicenseSubscriptionService.validate_admin_user(student, self.school)

        self.assertIn("Student users cannot manage", str(ctx.exception))

    def test_create_license_subscription_refuses_superadmin_admin_user(self):
        """The guard has to hold at the service entry point, not just in
        isolation -- this is the call the Stripe webhook makes."""
        superadmin = _make_superadmin()

        with self.assertRaises(ValueError):
            LicenseSubscriptionService.create_license_subscription(
                school=self.school,
                plan=_make_plan(),
                admin_user=superadmin,
                contract_months=12,
                max_seats=5,
                billing_method=LicenseBillingMethod.OFFLINE,
            )

        self.assertFalse(
            LicenseSubscription.objects.filter(school=self.school).exists()
        )
        self.assertFalse(
            SchoolCreditAllocation.objects.filter(user=superadmin).exists()
        )

    def test_stripe_checkout_refuses_superadmin_before_calling_stripe(self):
        """Reject at checkout creation, not after the school has paid."""
        superadmin = _make_superadmin()

        with patch("billing.stripe_service.stripe") as mock_stripe:
            with self.assertRaises(ValueError):
                StripeCheckoutService.create_license_session(
                    school=self.school,
                    plan=_make_plan(),
                    admin_user=superadmin,
                    contract_months=12,
                    max_seats=5,
                    teacher_emails=[],
                    custom_price_cents=10000,
                    success_url="https://example.com/ok",
                    cancel_url="https://example.com/no",
                )

            mock_stripe.checkout.Session.create.assert_not_called()


class ResolveAdminUserTest(TestCase):
    """admin_user is derived from the school when the caller omits it."""

    def setUp(self):
        self.school = School.objects.create(name="Resolve School")
        self.plan = _make_plan()

    def _make_admin(self, email, is_active=True, school=None):
        # NB: CustomUser.is_active defaults to False (users/models.py) - an
        # invited admin is inactive until they complete registration - so
        # this has to be passed explicitly, not assumed.
        return CustomUser.objects.create_user(
            email=email,
            password="password123",  # pragma: allowlist secret
            first_name="Some",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school if school is None else school,
            is_active=is_active,
        )

    def test_derives_the_schools_only_admin(self):
        admin = self._make_admin("only@resolve.edu")

        self.assertEqual(
            LicenseSubscriptionService.resolve_admin_user(self.school), admin
        )

    def test_derives_an_invited_admin_who_has_not_activated_yet(self):
        """Schools are onboarded before they buy, so the admin created by
        create_with_admin is still is_active=False at license time."""
        invited = self._make_admin("invited@resolve.edu", is_active=False)

        self.assertEqual(
            LicenseSubscriptionService.resolve_admin_user(self.school), invited
        )

    def test_prefers_an_active_admin_over_an_inactive_one(self):
        self._make_admin("stale@resolve.edu", is_active=False)
        active = self._make_admin("active@resolve.edu")

        self.assertEqual(
            LicenseSubscriptionService.resolve_admin_user(self.school), active
        )

    def test_picks_the_earliest_when_several_are_active(self):
        first = self._make_admin("first@resolve.edu")
        self._make_admin("second@resolve.edu")

        self.assertEqual(
            LicenseSubscriptionService.resolve_admin_user(self.school), first
        )

    def test_ignores_admins_of_other_schools_and_non_admins(self):
        other_school = School.objects.create(name="Elsewhere")
        self._make_admin("elsewhere@resolve.edu", school=other_school)
        CustomUser.objects.create_user(
            email="teacher@resolve.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Tea",
            last_name="Cher",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )

        with self.assertRaises(ValueError) as ctx:
            LicenseSubscriptionService.resolve_admin_user(self.school)

        self.assertIn("no school admin", str(ctx.exception))

    def test_school_with_no_admin_is_a_clear_error_not_a_crash(self):
        with self.assertRaises(ValueError) as ctx:
            LicenseSubscriptionService.resolve_admin_user(self.school)

        self.assertIn("no school admin", str(ctx.exception))

    def test_an_explicitly_passed_admin_is_honoured(self):
        self._make_admin("default@resolve.edu")
        designated = self._make_admin("designated@resolve.edu")

        self.assertEqual(
            LicenseSubscriptionService.resolve_admin_user(self.school, designated),
            designated,
        )

    def test_an_explicitly_passed_admin_is_still_validated(self):
        """Supplying a value must not be a way around the guard."""
        self._make_admin("default@resolve.edu")
        superadmin = _make_superadmin()

        with self.assertRaises(ValueError):
            LicenseSubscriptionService.resolve_admin_user(self.school, superadmin)

    def test_create_license_subscription_without_an_admin_user(self):
        admin = self._make_admin("service@resolve.edu")

        license_sub = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            contract_months=12,
            max_seats=5,
            billing_method=LicenseBillingMethod.OFFLINE,
        )

        self.assertEqual(license_sub.admin_user, admin)
        self.assertTrue(
            license_sub.allocations.filter(
                user=admin, is_admin_allocation=True
            ).exists()
        )

    def test_create_license_subscription_refuses_when_school_has_no_admin(self):
        with self.assertRaises(ValueError):
            LicenseSubscriptionService.create_license_subscription(
                school=self.school,
                plan=self.plan,
                contract_months=12,
                max_seats=5,
                billing_method=LicenseBillingMethod.OFFLINE,
            )

        self.assertFalse(LicenseSubscription.objects.exists())


class LicenseCreationAPIGuardTest(APITestCase):
    """API-layer behaviour of POST /license-subscriptions/."""

    def setUp(self):
        self.school = School.objects.create(name="API Guard School")
        self.plan = _make_plan()
        self.superadmin = _make_superadmin()
        self.school_admin = CustomUser.objects.create_user(
            email="admin@api-guard.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Ada",
            last_name="Lovelace",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.client.force_authenticate(user=self.superadmin)
        self.url = reverse("license-subscription-list")

    def _payload(self, admin_user=None):
        payload = {
            "school": str(self.school.id),
            "plan": str(self.plan.id),
            "contract_months": 12,
            "max_seats": 5,
            "billing_method": LicenseBillingMethod.OFFLINE,
            "custom_price_cents": 10000,
        }
        if admin_user is not None:
            payload["admin_user"] = str(admin_user.id)
        return payload

    def test_admin_user_may_be_omitted_and_is_taken_from_the_school(self):
        """The normal path: the caller never names an admin at all, so it
        cannot name the wrong one."""
        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["admin_email"], self.school_admin.email)

        license_sub = LicenseSubscription.objects.get(school=self.school)
        self.assertEqual(license_sub.admin_user, self.school_admin)
        self.assertTrue(
            license_sub.allocations.filter(
                user=self.school_admin, is_admin_allocation=True
            ).exists()
        )

    def test_omitted_admin_user_on_a_school_with_none_is_a_400(self):
        adminless = School.objects.create(name="Adminless School")
        payload = self._payload()
        payload["school"] = str(adminless.id)

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("admin_user", response.data)
        self.assertFalse(LicenseSubscription.objects.exists())

    def test_superadmin_cannot_name_themselves_as_the_schools_admin(self):
        response = self.client.post(
            self.url, self._payload(self.superadmin), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("admin_user", response.data)
        self.assertFalse(LicenseSubscription.objects.exists())
        # The school's admin credit allowance must not have been diverted.
        self.assertFalse(
            SchoolCreditAllocation.objects.filter(user=self.superadmin).exists()
        )

    def test_superadmin_can_still_create_a_license_for_the_real_admin(self):
        """The guard must not break the legitimate flow it protects."""
        response = self.client.post(
            self.url, self._payload(self.school_admin), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["admin_email"], self.school_admin.email)

        license_sub = LicenseSubscription.objects.get(school=self.school)
        self.assertEqual(license_sub.admin_user, self.school_admin)
        self.assertTrue(
            license_sub.allocations.filter(
                user=self.school_admin, is_admin_allocation=True
            ).exists()
        )

    def test_admin_from_another_school_is_rejected(self):
        other_school = School.objects.create(name="Somewhere Else")
        outsider = CustomUser.objects.create_user(
            email="admin@somewhere-else.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Other",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=other_school,
        )

        response = self.client.post(self.url, self._payload(outsider), format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(LicenseSubscription.objects.exists())


class LicenseObjectPermissionScopeTest(APITestCase):
    """A school admin's access to their license is scoped by school, not by
    whether they happen to be the row's admin_user."""

    def setUp(self):
        self.school = School.objects.create(name="Two Admin School")
        self.other_school = School.objects.create(name="Unrelated School")
        self.plan = _make_plan()

        self.named_admin = CustomUser.objects.create_user(
            email="named@two-admin.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Named",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.second_admin = CustomUser.objects.create_user(
            email="second@two-admin.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Second",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.outsider_admin = CustomUser.objects.create_user(
            email="admin@unrelated.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Outsider",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.other_school,
        )

        self.license_sub = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            admin_user=self.named_admin,
            contract_months=12,
            max_seats=5,
            billing_method=LicenseBillingMethod.OFFLINE,
        )
        self.detail_url = reverse(
            "license-subscription-detail", kwargs={"pk": self.license_sub.pk}
        )

    def test_named_admin_can_retrieve(self):
        self.client.force_authenticate(user=self.named_admin)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_second_admin_of_the_same_school_is_not_locked_out(self):
        """Used to 403: the license listed for them but detail refused,
        because access keyed off admin_user rather than school."""
        self.client.force_authenticate(user=self.second_admin)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_of_another_school_still_cannot_reach_it(self):
        self.client.force_authenticate(user=self.outsider_admin)
        response = self.client.get(self.detail_url)
        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )

    def test_teacher_cannot_reach_it(self):
        teacher = CustomUser.objects.create_user(
            email="teacher@two-admin.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Tea",
            last_name="Cher",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        self.client.force_authenticate(user=teacher)
        response = self.client.get(self.detail_url)
        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )
