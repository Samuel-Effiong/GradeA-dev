"""
H-203: the three student-add ROUTES refuse a STUDENT-typed account that carries
admin power, as they refuse a teacher or an admin (H-71).

THE PRINCIPLE (users/admin_power.py): an account with admin power that has
never been verified can be entered only with its password; no road that proves
only control of a mailbox signs it in, verifies it or activates it.

The gate is the one shared function `check_existing_account_may_join`; these
tests go through the ROUTES (the course `students` action, `direct-add-student`
and `bulk-add-students`), because a wiring difference between a route and the
service would show only there. Before H-203 a STUDENT-typed row with is_staff
passed the gate: the single add and the import set it a fresh password and
mailed it (it had never signed in), and the direct add enrolled it.

Run with:
    python manage.py test classrooms.tests_h203_admin_power_routes
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, School, Session, StudentCourse
from classrooms.services import NOT_A_STUDENT_MESSAGE
from classrooms.tests_support_add_by_email import add_by_email
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
ROW_PASSWORD = "Row-pass-h203"  # pragma: allowlist secret
DIRECT_REFUSALS = (status.HTTP_400_BAD_REQUEST, status.HTTP_500_INTERNAL_SERVER_ERROR)
# The Phase 2 line words a bulk-import row's error with the row number and a
# lower-case first letter (pinned in classrooms.tests_s7d_roster_codes).
ROW_ONE_NOT_A_STUDENT = "Row 1: this email can't be added as a student."


@override_settings(CACHES=LOCMEM)
class StudentAddRoutesAdminPowerTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        invitation = patch(
            "classrooms.services.notifications.send_student_login_invitation_email"
        )
        self.invitation = invitation.start()
        self.addCleanup(invitation.stop)
        for target in ("classrooms.services.notifications.safe_delay",):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        school = School.objects.create(name="H203 Routes High")
        self.teacher = User.objects.create_user(
            email="owner@h203routes.example",
            password=ROW_PASSWORD,
            user_type=UserTypes.TEACHER,
            school=school,
            is_active=True,
        )
        session = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Maths", teacher=self.teacher, session=session
        )
        self.client.force_authenticate(self.teacher)

    def row(self, email, **flags):
        return User.objects.create_user(
            email=email,
            password=ROW_PASSWORD,
            user_type=UserTypes.STUDENT,
            is_active=True,
            **flags,
        )

    def post(self, url_name, data):
        cache.clear()
        return self.client.post(reverse(url_name, kwargs={"pk": self.course.id}), data)

    def single(self, email):
        return self.post("course-students", add_by_email(email))

    def bulk(self, email):
        return self.post("course-bulk-add-students", {"raw_data": f"Probe,Row,{email}"})

    def direct(self, email):
        return self.post(
            "course-direct-add-student",
            {"first_name": "Probe", "last_name": "Row", "email": email},
        )

    def assert_untouched(self, account, password_before):
        account.refresh_from_db()
        self.assertEqual(account.password, password_before)
        self.assertFalse(StudentCourse.objects.filter(student=account).exists())
        self.invitation.assert_not_called()

    # -- the refusal, on each route ----------------------------------------------

    def test_single_add_refuses_a_student_typed_row_carrying_is_staff(self):
        account = self.row("single.h203@x.example", is_staff=True)
        before = account.password

        response = self.single(account.email)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], NOT_A_STUDENT_MESSAGE)
        self.assert_untouched(account, before)

    def test_single_add_refuses_a_student_typed_row_carrying_is_superuser(self):
        account = self.row("singlesu.h203@x.example", is_superuser=True)
        before = account.password

        response = self.single(account.email)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], NOT_A_STUDENT_MESSAGE)
        self.assert_untouched(account, before)

    def test_bulk_import_refuses_a_student_typed_row_carrying_is_staff(self):
        account = self.row("bulk.h203@x.example", is_staff=True)
        before = account.password

        response = self.bulk(account.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["failure_count"], 1)
        self.assertEqual(response.data["success_count"], 0)
        self.assertEqual(response.data["results"][0]["error"], ROW_ONE_NOT_A_STUDENT)
        self.assert_untouched(account, before)

    def test_bulk_import_refuses_a_student_typed_row_carrying_is_superuser(self):
        account = self.row("bulksu.h203@x.example", is_superuser=True)
        before = account.password

        response = self.bulk(account.email)

        self.assertEqual(response.data["failure_count"], 1)
        self.assertEqual(response.data["results"][0]["error"], ROW_ONE_NOT_A_STUDENT)
        self.assert_untouched(account, before)

    def test_direct_add_refuses_a_student_typed_row_carrying_is_staff(self):
        account = self.row("direct.h203@x.example", is_staff=True)
        before = account.password

        response = self.direct(account.email)

        self.assertIn(response.status_code, DIRECT_REFUSALS)
        self.assert_untouched(account, before)

    def test_direct_add_refuses_a_student_typed_row_carrying_is_superuser(self):
        account = self.row("directsu.h203@x.example", is_superuser=True)
        before = account.password

        response = self.direct(account.email)

        self.assertIn(response.status_code, DIRECT_REFUSALS)
        self.assert_untouched(account, before)

    # -- controls: nothing else changes -----------------------------------------

    def test_single_add_still_adds_an_ordinary_student(self):
        """Green on the old code too."""
        account = self.row("pupil.h203@x.example")

        response = self.single(account.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            StudentCourse.objects.filter(student=account, course=self.course).exists()
        )

    def test_bulk_import_still_adds_an_ordinary_student(self):
        """Green on the old code too."""
        account = self.row("pupilbulk.h203@x.example")

        response = self.bulk(account.email)

        self.assertEqual(response.data["success_count"], 1)
        self.assertTrue(
            StudentCourse.objects.filter(student=account, course=self.course).exists()
        )

    def test_direct_add_still_adds_an_ordinary_student(self):
        """Green on the old code too."""
        account = self.row("pupildirect.h203@x.example")

        response = self.direct(account.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            StudentCourse.objects.filter(student=account, course=self.course).exists()
        )
