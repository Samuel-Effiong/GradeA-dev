"""
Access control for the QA console views: bare 404 in every denial case
(disabled, not logged in, logged in but not a superadmin), matching
qa_time_travel.py's "no hint this tool exists" rationale. Exercised
through the real URL (qa-console-state) rather than calling
_qa_console_required directly, so a mistake in HOW the decorator is
attached to a view would also be caught.
"""

from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from users.models import CustomUser, UserTypes


def _make_user(user_type, is_superuser=False):
    # is_active defaults to False on CustomUser (see users/models.py) --
    # force_login goes through the REAL AuthenticationMiddleware, whose
    # ModelBackend.get_user() refuses to resolve an inactive user and
    # silently falls back to AnonymousUser, so this must be explicit.
    return CustomUser.objects.create_user(
        email=f"{user_type.lower()}-{uuid4().hex[:10]}@example.com",
        password="testpass123",  # pragma: allowlist secret
        user_type=user_type,
        is_superuser=is_superuser,
        is_active=True,
    )


class QaConsolePermissionTests(TestCase):
    def setUp(self):
        self.url = reverse("qa-console-state")

    def _get(self):
        return self.client.get(self.url)

    @patch("billing.qa_console.live_qa_enabled", return_value=True)
    def test_anonymous_user_gets_404(self, _mock_enabled):
        response = self._get()
        self.assertEqual(response.status_code, 404)

    @patch("billing.qa_console.live_qa_enabled", return_value=True)
    def test_teacher_gets_404(self, _mock_enabled):
        teacher = _make_user(UserTypes.TEACHER)
        self.client.force_login(teacher)
        response = self._get()
        self.assertEqual(response.status_code, 404)

    @patch("billing.qa_console.live_qa_enabled", return_value=True)
    def test_super_admin_type_without_is_superuser_gets_404(self, _mock_enabled):
        """Both conditions of IsSuperAdmin's own check are required —
        matching classrooms.permissions.IsSuperAdmin exactly."""
        almost = _make_user(UserTypes.SUPER_ADMIN, is_superuser=False)
        self.client.force_login(almost)
        response = self._get()
        self.assertEqual(response.status_code, 404)

    @patch("billing.qa_console.live_qa_enabled", return_value=True)
    def test_is_superuser_without_super_admin_type_gets_404(self, _mock_enabled):
        almost = _make_user(UserTypes.TEACHER, is_superuser=True)
        self.client.force_login(almost)
        response = self._get()
        self.assertEqual(response.status_code, 404)

    @patch("billing.qa_console.live_qa_enabled", return_value=False)
    def test_real_superadmin_still_gets_404_when_disabled(self, _mock_enabled):
        admin = _make_user(UserTypes.SUPER_ADMIN, is_superuser=True)
        self.client.force_login(admin)
        response = self._get()
        self.assertEqual(response.status_code, 404)

    @patch("billing.qa_console.live_qa_enabled", return_value=True)
    def test_real_superadmin_gets_through_when_enabled(self, _mock_enabled):
        admin = _make_user(UserTypes.SUPER_ADMIN, is_superuser=True)
        self.client.force_login(admin)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        # Asserts the payload's meaning, not its exact shape -- this test
        # is about the permission gate letting a superadmin through, and
        # should not fail every time the state endpoint grows a field.
        self.assertIsNone(response.json()["subscriber"])

    @patch("billing.qa_console.live_qa_enabled", return_value=True)
    def test_console_page_itself_is_gated_the_same_way(self, _mock_enabled):
        response = self.client.get(reverse("qa-console"))
        self.assertEqual(response.status_code, 404)

        admin = _make_user(UserTypes.SUPER_ADMIN, is_superuser=True)
        self.client.force_login(admin)
        response = self.client.get(reverse("qa-console"))
        self.assertEqual(response.status_code, 200)
