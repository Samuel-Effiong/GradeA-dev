"""Regression: the school-admin teacher list/detail dashboard endpoints must
return `status` as a real JSON boolean, not the stringified `"True"`/`"False"`
that `serializers.CharField()` over `teacher.is_active` used to produce
(caught during a manual review of dashboard/teachers/<id>, unguarded by any
existing test — the one assertion that touched `status`, in
dashboard/tests.py, checked the pre-serialization service dict, and an
`assertTrue("False")` would have passed anyway since any non-empty string is
truthy)."""

from django.test import TestCase
from rest_framework import status as http_status
from rest_framework.test import APIClient

from classrooms.models import School
from users.models import CustomUser, UserTypes

TEST_PASSWORD = "password123"  # pragma: allowlist secret


def make_school_admin(email, school):
    user = CustomUser.objects.create_user(email=email, password=TEST_PASSWORD)
    user.user_type = UserTypes.SCHOOL_ADMIN
    user.is_active = True
    user.school = school
    user.save()
    return user


def make_teacher(email, school, *, is_active=True):
    user = CustomUser.objects.create_user(email=email, password=TEST_PASSWORD)
    user.user_type = UserTypes.TEACHER
    user.is_active = is_active
    user.school = school
    user.save()
    return user


class TeacherStatusFieldTest(TestCase):
    """`status` must be a real bool in both dashboard/teachers and
    dashboard/teachers/<id>, for both an active and an inactive teacher."""

    def setUp(self):
        self.school = School.objects.create(name="Status Field School")
        self.admin = make_school_admin("status-field-admin@example.com", self.school)
        self.active_teacher = make_teacher(
            "status-field-active@example.com", self.school, is_active=True
        )
        self.inactive_teacher = make_teacher(
            "status-field-inactive@example.com", self.school, is_active=False
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_teacher_list_status_is_a_real_boolean(self):
        response = self.client.get("/api/v1/school-admin/dashboard/teachers")
        self.assertEqual(
            response.status_code, http_status.HTTP_200_OK, response.content
        )
        by_id = {row["id"]: row for row in response.data["results"]}
        self.assertIs(by_id[str(self.active_teacher.id)]["status"], True)
        self.assertIs(by_id[str(self.inactive_teacher.id)]["status"], False)

    def test_teacher_detail_status_is_a_real_boolean(self):
        response = self.client.get(
            f"/api/v1/school-admin/dashboard/teachers/{self.active_teacher.id}"
        )
        self.assertEqual(
            response.status_code, http_status.HTTP_200_OK, response.content
        )
        self.assertIs(response.data["status"], True)

        response = self.client.get(
            f"/api/v1/school-admin/dashboard/teachers/{self.inactive_teacher.id}"
        )
        self.assertEqual(
            response.status_code, http_status.HTTP_200_OK, response.content
        )
        self.assertIs(response.data["status"], False)
