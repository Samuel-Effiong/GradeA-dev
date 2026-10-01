"""A school admin's "Add teachers" must not disclose another school.

Adding a teacher who already belongs to a different school failed with
"Teacher '<email>' already belongs to school '<other school's name>'. Cannot
enroll under '<own school>'." That told any school admin which school an
arbitrary address belongs to - a cross-tenant disclosure. The same text went
to the log with the teacher's email. The refusal stays; its wording is now
generic, and the log line carries ids only.
"""

from unittest.mock import patch

from django.urls import reverse
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

RIVAL = "Rival Academy of Secret Names"
STUDENT_EMAIL = "a.student@rival-academy.edu"
NOT_A_TEACHER = "This email can't be added as a teacher."
TEACHER_EMAIL = "taken.teacher@rival-academy.edu"
GENERIC = "This teacher already belongs to another school."


class AddTeachersOtherSchoolTest(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="Own School")
        rival = School.objects.create(name=RIVAL)
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Leak Test Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@own-school.edu",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        self.taken = CustomUser.objects.create_user(
            email=TEACHER_EMAIL,
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=rival,
            is_active=True,
        )
        mail = patch("billing.license_service.send_email_task")
        mail.start()
        self.addCleanup(mail.stop)
        self.licence = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=plan,
            teacher_emails=[],
            max_seats=5,
            billing_method=LicenseBillingMethod.OFFLINE,
        )
        self.client.force_authenticate(user=self.admin)

    def add(self):
        return self.client.post(
            reverse(
                "license-subscription-add-teachers", kwargs={"pk": self.licence.pk}
            ),
            {"teacher_emails": [TEACHER_EMAIL]},
            format="json",
        )

    def test_the_refusal_does_not_name_the_other_school(self):
        with self.assertLogs("billing.license_service", level="DEBUG") as logs:
            response = self.add()

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual((data["successful"], data["failed"]), (0, 1))
        self.assertEqual(data["errors"][0]["error"], GENERIC)
        self.assertNotIn(RIVAL, response.content.decode())
        # The log keeps the ids, never the other school's name or the email.
        logged = "\n".join(logs.output)
        self.assertNotIn(RIVAL, logged)
        self.assertNotIn(TEACHER_EMAIL, logged)
        self.assertIn(str(self.taken.id), logged)

    def test_a_non_teacher_account_is_refused_without_naming_its_role(self):
        """SM ruling: the role of an arbitrary address is not disclosed
        either - not in the response, not in the log."""
        student = CustomUser.objects.create_user(
            email=STUDENT_EMAIL,
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        with self.assertLogs("billing.license_service", level="DEBUG") as logs:
            response = self.client.post(
                reverse(
                    "license-subscription-add-teachers",
                    kwargs={"pk": self.licence.pk},
                ),
                {"teacher_emails": [STUDENT_EMAIL]},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["errors"][0]["error"], NOT_A_TEACHER)
        body = response.content.decode().lower()
        for role in ("student", "school admin", "school_admin", "super"):
            self.assertNotIn(role, body.replace(STUDENT_EMAIL.lower(), ""))
        logged = "\n".join(logs.output)
        self.assertNotIn(STUDENT_EMAIL, logged)
        for role in ("student", "school admin", "school_admin", "super"):
            self.assertNotIn(role, logged.lower())
        self.assertIn(str(student.id), logged)

    def test_the_teacher_is_still_refused(self):
        self.add()
        self.taken.refresh_from_db()
        self.assertNotEqual(self.taken.school_id, self.school.id)
        self.assertFalse(
            SchoolCreditAllocation.objects.filter(
                license_subscription=self.licence, user=self.taken
            ).exists()
        )
