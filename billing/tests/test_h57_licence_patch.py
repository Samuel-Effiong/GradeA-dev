"""
billing/tests/test_h57_licence_patch.py
=======================================
H-57: PATCH /license-subscriptions/<id>/ used to answer 200 while silently
dropping every field its update() does not apply (it applies only
auto_renew and custom_price_cents). A caller who PATCHed max_seats believed
the seat count had changed.

Now a CHANGED value for any of those fields is a 400 that names the route
that does make the change, and nothing in that request is applied. An
UNCHANGED value, as a client echoing the whole object back sends, is still
accepted, with the patchable fields applied.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import LicenseBillingMethod, PlanTier, PlanType
from billing.serializers import LICENCE_NOT_PATCHABLE
from billing.tests.test_h28_licence_stripe_divergence import _make_plan
from billing.tests.test_license_cancellation import _make_license
from classrooms.models import School
from users.models import CustomUser, UserTypes

PASSWORD = "Str0ng-h57-pass!"  # pragma: allowlist secret


class LicencePatchTests(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="H57 School")
        self.other_school = School.objects.create(name="H57 Other")
        self.admin = CustomUser.objects.create_user(
            email="admin@h57.school.edu",
            password=PASSWORD,
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.other_admin = CustomUser.objects.create_user(
            email="admin2@h57.school.edu",
            password=PASSWORD,
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.superadmin = CustomUser.objects.create_superuser(
            email="root@h57.gradea.com",
            password=PASSWORD,
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.plan = _make_plan(PlanTier.PRO, PlanType.PRO, "1000.00", "price_h57_pro")
        self.other_plan = _make_plan(
            PlanTier.POWER, PlanType.POWER, "2000.00", "price_h57_power"
        )
        self.licence = _make_license(
            self.school,
            self.admin,
            self.plan,
            LicenseBillingMethod.STRIPE,
            stripe_subscription_id="sub_h57",
            max_seats=10,
            contract_months=12,
        )
        self.url = reverse(
            "license-subscription-detail", kwargs={"pk": self.licence.pk}
        )
        self.client.force_authenticate(self.superadmin)

    def stored(self):
        self.licence.refresh_from_db()
        return {
            "max_seats": self.licence.max_seats,
            "plan": self.licence.plan_id,
            "billing_method": self.licence.billing_method,
            "contract_months": self.licence.contract_months,
            "school": self.licence.school_id,
            "admin_user": self.licence.admin_user_id,
            "stripe_subscription_id": self.licence.stripe_subscription_id,
            "auto_renew": self.licence.auto_renew,
            "custom_price_cents": self.licence.custom_price_cents,
        }

    def changed(self):
        """A different, otherwise valid value for every non-patchable field."""
        return {
            "max_seats": 25,
            "plan": str(self.other_plan.pk),
            "billing_method": LicenseBillingMethod.OFFLINE,
            "contract_months": 9,
            "school": str(self.other_school.pk),
            "admin_user": str(self.other_admin.pk),
            "stripe_subscription_id": "sub_h57_other",
        }

    def unchanged(self):
        return {
            "max_seats": 10,
            "plan": str(self.plan.pk),
            "billing_method": LicenseBillingMethod.STRIPE,
            "contract_months": 12,
            "school": str(self.school.pk),
            "admin_user": str(self.admin.pk),
            "stripe_subscription_id": "sub_h57",
        }

    def test_a_changed_max_seats_is_refused_and_changes_nothing(self):
        before = self.stored()
        response = self.client.patch(
            self.url, {"max_seats": 25, "auto_renew": False}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("update_seats", str(response.data["max_seats"]))
        # auto_renew rode along and was NOT applied: the whole PATCH failed.
        self.assertEqual(self.stored(), before)

    def test_an_unchanged_max_seats_is_accepted_with_auto_renew_applied(self):
        response = self.client.patch(
            self.url, {"max_seats": 10, "auto_renew": False}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(self.stored()["auto_renew"])
        self.assertEqual(self.stored()["max_seats"], 10)

    def test_every_changed_non_patchable_field_is_refused(self):
        before = self.stored()
        for field, value in self.changed().items():
            with self.subTest(field=field):
                response = self.client.patch(
                    self.url, {field: value, "auto_renew": False}, format="json"
                )
                self.assertEqual(
                    response.status_code, status.HTTP_400_BAD_REQUEST, response.data
                )
                self.assertEqual(
                    str(response.data[field][0]), LICENCE_NOT_PATCHABLE[field]
                )
                self.assertEqual(self.stored(), before)

    def test_the_whole_object_echoed_back_unchanged_is_accepted(self):
        """What a frontend that PATCHes back what it holds sends."""
        payload = {
            **self.unchanged(),
            "auto_renew": False,
            "custom_price_cents": 12_345,
            "teacher_emails": [],
            "carry_forward_teachers": True,
        }
        response = self.client.patch(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        after = self.stored()
        self.assertFalse(after["auto_renew"])
        self.assertEqual(after["custom_price_cents"], 12_345)
        self.assertEqual(after["max_seats"], 10)

    def test_a_blank_stripe_id_echo_matches_a_null_stored_one(self):
        self.licence.stripe_subscription_id = None
        self.licence.save(update_fields=["stripe_subscription_id"])
        response = self.client.patch(
            self.url, {"stripe_subscription_id": "", "auto_renew": False}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_teacher_emails_are_refused_when_given(self):
        before = self.stored()
        response = self.client.patch(
            self.url,
            {"teacher_emails": ["new.teacher@h57.school.edu"], "auto_renew": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("add_teachers", str(response.data["teacher_emails"]))
        self.assertEqual(self.stored(), before)

    def test_carry_forward_teachers_alone_is_ignored(self):
        """It defaults to True and only governs creating a replacement
        licence, so an echo of it changes nothing and is not refused."""
        before = self.stored()
        response = self.client.patch(
            self.url, {"carry_forward_teachers": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(self.stored(), before)
