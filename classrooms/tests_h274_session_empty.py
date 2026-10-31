"""H-274 item (d): reproduce "creating a session gives a 500 when the
session id/value is empty" as reported in the QA catalogue §3."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Session
from users.models import CustomUser, UserTypes


class SessionCreationEmptyFieldsTest(APITestCase):
    """POST /api/classrooms/sessions/ with empty or null fields."""

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher-h274@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="H274",
        )
        self.teacher.user_type = UserTypes.TEACHER
        self.teacher.is_active = True
        self.teacher.save()
        self.client.force_authenticate(user=self.teacher)
        self.url = reverse("session-list")

    def test_empty_name_returns_400_not_500(self):
        response = self.client.post(self.url, {"name": ""}, format="json")
        self.assertLess(
            response.status_code,
            500,
            f"Expected 4xx, got {response.status_code}: {response.data}",
        )

    def test_null_name_returns_400_not_500(self):
        response = self.client.post(self.url, {"name": None}, format="json")
        self.assertLess(
            response.status_code,
            500,
            f"Expected 4xx, got {response.status_code}: {response.data}",
        )

    def test_whitespace_only_name_returns_400_not_500(self):
        response = self.client.post(self.url, {"name": "   "}, format="json")
        self.assertLess(
            response.status_code,
            500,
            f"Expected 4xx, got {response.status_code}: {response.data}",
        )

    def test_missing_name_returns_400_not_500(self):
        response = self.client.post(self.url, {}, format="json")
        self.assertLess(
            response.status_code,
            500,
            f"Expected 4xx, got {response.status_code}: {response.data}",
        )

    def test_empty_id_in_body_returns_400_not_500(self):
        """Client sends id="" in the create body."""
        response = self.client.post(
            self.url, {"id": "", "name": "Valid Name"}, format="json"
        )
        self.assertLess(
            response.status_code,
            500,
            f"Expected 4xx, got {response.status_code}: {response.data}",
        )

    def test_null_id_in_body_returns_400_not_500(self):
        """Client sends id=null in the create body."""
        response = self.client.post(
            self.url, {"id": None, "name": "Valid Name"}, format="json"
        )
        self.assertLess(
            response.status_code,
            500,
            f"Expected 4xx, got {response.status_code}: {response.data}",
        )

    def test_valid_creation_still_works(self):
        response = self.client.post(self.url, {"name": "Fall 2026"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Session.objects.filter(name="Fall 2026").exists())
