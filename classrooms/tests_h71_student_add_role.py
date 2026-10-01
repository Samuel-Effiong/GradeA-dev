"""H-71: adding a staff address as a student must not name the account's role.

The student-add routes used to answer "This email belongs to a teacher
account..." or "...cannot be added as a school admin." A teacher could type
any address into the form and learn which ones belong to staff on the
platform. Every non-student role now gets one neutral answer,
NOT_A_STUDENT_MESSAGE, on all three routes (single add, bulk import, direct
add). The server log keeps the account id and its type for an admin.
"""

import re

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, Session, StudentCourse
from classrooms.services import (
    NOT_A_STUDENT_MESSAGE,
    EnrollmentError,
    check_existing_account_may_join,
)
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Any word that names, or narrows down, a role.
ROLE_WORDS = re.compile(
    r"\b(teacher|teachers|admin|admins|administrator|superadmin|super|staff)\b",
    re.I,
)
PROBE_PASSWORD = "Str0ng-h71-pass!"  # pragma: allowlist secret


def make_user(email, user_type):
    user = User.objects.create_user(email=email, password=PROBE_PASSWORD)
    user.user_type = user_type
    user.is_active = True
    user.save()
    return user


@override_settings(CACHES=LOCMEM)
class StudentAddNamesNoRoleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = make_user("owner@h71.test", UserTypes.TEACHER)
        session = Session.objects.create(name="H71", teacher=self.teacher)
        self.course = Course.objects.create(
            name="H71 course", teacher=self.teacher, session=session
        )
        self.staff = {
            "teacher": make_user("other.teacher@h71.test", UserTypes.TEACHER),
            "school admin": make_user("school.admin@h71.test", UserTypes.SCHOOL_ADMIN),
            "super admin": make_user("super.admin@h71.test", UserTypes.SUPER_ADMIN),
        }

    def post(self, url_name, data):
        cache.clear()
        self.client.force_authenticate(self.teacher)
        return self.client.post(reverse(url_name, kwargs={"pk": self.course.id}), data)

    def routes(self, email):
        """(label, response) for every student-add route given `email`."""
        return [
            ("single add", self.post("course-students", {"email": email})),
            (
                "bulk import",
                self.post(
                    "course-bulk-add-students", {"raw_data": f"Probe,Row,{email}"}
                ),
            ),
            (
                "direct add",
                self.post(
                    "course-direct-add-student",
                    {"first_name": "Probe", "last_name": "Row", "email": email},
                ),
            ),
        ]

    def test_no_route_names_the_role_of_a_staff_address(self):
        for role, account in self.staff.items():
            for label, response in self.routes(account.email):
                body = response.content.decode()
                with self.subTest(role=role, route=label):
                    self.assertIsNone(ROLE_WORDS.search(body), body[:300])
                    self.assertFalse(
                        StudentCourse.objects.filter(student=account).exists()
                    )

    def test_single_add_and_bulk_import_give_the_neutral_message(self):
        for role, account in self.staff.items():
            single, bulk, _ = self.routes(account.email)
            with self.subTest(role=role):
                self.assertEqual(single[1].status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(NOT_A_STUDENT_MESSAGE, single[1].content.decode())
                self.assertEqual(bulk[1].data["failure_count"], 1)
                self.assertEqual(
                    bulk[1].data["results"][0]["error"], NOT_A_STUDENT_MESSAGE
                )

    def test_every_role_gets_the_same_answer(self):
        """The answer must not tell one staff role from another."""
        answers = {
            role: [
                (label, response.status_code, response.content)
                for label, response in self.routes(account.email)
            ]
            for role, account in self.staff.items()
        }
        first = answers.pop("teacher")
        for role, answer in answers.items():
            with self.subTest(role=role):
                self.assertEqual(answer, first)

    def test_a_student_address_is_still_added(self):
        """Control: the neutral wording blocks no real student."""
        student = make_user("pupil@h71.test", UserTypes.STUDENT)
        response = self.post("course-students", {"email": student.email})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            StudentCourse.objects.filter(student=student, course=self.course).exists()
        )

    def test_the_service_rule_raises_the_neutral_message_and_logs_ids_only(self):
        for role, account in self.staff.items():
            with self.subTest(role=role):
                with self.assertLogs("classrooms.services.enrollment", "INFO") as logs:
                    with self.assertRaises(EnrollmentError) as raised:
                        check_existing_account_may_join(account, self.course)
                self.assertEqual(str(raised.exception), NOT_A_STUDENT_MESSAGE)
                line = "\n".join(logs.output)
                self.assertIn(str(account.pk), line)
                self.assertNotIn(account.email, line)
