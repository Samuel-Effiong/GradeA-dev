"""
Hotfix, widened (founder report): a school admin's "Add teachers" on a full
licence answered 400 with the GENERIC "We couldn't add these teachers to the
license...", hiding the reason.

add_teachers_batch refused with a bare ValueError, which the view's
`describe_user_error` fallback does not show. Its user-input refusals (no
seats, inactive licence), and remove_teacher_from_license's "not on this
licence", are now LicenseRequestError, which `AutoGrader.error_messages`
lists as user-facing, so the school admin sees the actual reason. Anything
else keeps the generic fallback.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

FULL = (
    "Your licence has no seats left (2 of 2 in use). "
    "Remove a teacher or ask us to add seats."
)
ONE_LEFT_ADDING_TWO = (
    "Your licence has 1 seat left, but you're adding 2 teachers (1 of 2 in use). "
    "Add fewer teachers, remove a teacher, or ask us to add seats."
)
GENERIC_ADD = (
    "We couldn't add these teachers to the license. Please try again, or "
    "contact support if this continues."
)


class LicenseTeacherChangesTest(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="Teacher Change School")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Teacher Change Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@teacherchange.edu",
            password="password123",  # pragma: allowlist secret
            first_name="School",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        mail = patch("billing.license_service.send_email_task")
        self.mail = mail.start()
        self.addCleanup(mail.stop)
        self.client.force_authenticate(user=self.admin)

    def licence(self, teachers, max_seats=2):
        licence = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            teacher_emails=teachers,
            max_seats=max_seats,
            billing_method=LicenseBillingMethod.OFFLINE,
        )
        self.mail.reset_mock()
        return licence

    def add(self, licence, emails):
        return self.client.post(
            reverse("license-subscription-add-teachers", kwargs={"pk": licence.pk}),
            {"teacher_emails": emails},
            format="json",
        )

    def teacher_allocations(self, licence):
        return SchoolCreditAllocation.objects.filter(
            license_subscription=licence, is_admin_allocation=False
        )

    def test_a_full_licence_says_so(self):
        licence = self.licence(["t1@teacherchange.edu", "t2@teacherchange.edu"])
        before = list(self.teacher_allocations(licence).values_list("pk", "is_active"))

        response = self.add(licence, ["new@teacherchange.edu"])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["message"], FULL)
        # Nothing written, nobody invited.
        self.assertEqual(
            list(self.teacher_allocations(licence).values_list("pk", "is_active")),
            before,
        )
        self.assertFalse(
            CustomUser.objects.filter(email="new@teacherchange.edu").exists()
        )
        self.mail.assert_not_called()

    def test_fewer_seats_than_teachers_says_how_many(self):
        licence = self.licence(["t1@teacherchange.edu"])

        response = self.add(
            licence, ["new1@teacherchange.edu", "new2@teacherchange.edu"]
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["message"], ONE_LEFT_ADDING_TWO)
        self.assertEqual(self.teacher_allocations(licence).count(), 1)
        self.mail.assert_not_called()

    def test_a_free_seat_still_adds_the_teacher(self):
        """Positive control."""
        licence = self.licence(["t1@teacherchange.edu"])

        response = self.add(licence, ["new@teacherchange.edu"])

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.json()["data"]["successful"], 1)
        self.assertEqual(
            self.teacher_allocations(licence).filter(is_active=True).count(), 2
        )

    def test_an_inactive_licence_says_so(self):
        licence = self.licence(["t1@teacherchange.edu"])
        licence.is_active = False
        licence.save(update_fields=["is_active"])

        response = self.add(licence, ["new@teacherchange.edu"])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.json()["message"],
            "This licence isn't active, so teachers can't be added to it.",
        )

    def test_any_other_error_keeps_the_generic_message(self):
        """Only the typed refusals are shown; a bug's text is not."""
        licence = self.licence(["t1@teacherchange.edu"])
        with patch.object(
            LicenseSubscriptionService,
            "add_teachers_batch",
            side_effect=ValueError("internal detail"),
        ):
            response = self.add(licence, ["new@teacherchange.edu"])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["message"], GENERIC_ADD)
        self.assertNotIn("internal detail", response.content.decode())

    def test_removing_a_teacher_not_on_the_licence_says_so(self):
        licence = self.licence(["t1@teacherchange.edu"])
        outsider = CustomUser.objects.create_user(
            email="outsider@teacherchange.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Out",
            last_name="Sider",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

        response = self.client.post(
            reverse("license-subscription-remove-teachers", kwargs={"pk": licence.pk}),
            {"teacher_ids": [str(outsider.id)]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()["data"]
        self.assertEqual((data["successful"], data["failed"]), (0, 1))
        self.assertEqual(
            data["errors"][0]["error"],
            "This teacher isn't an active teacher on this licence.",
        )
