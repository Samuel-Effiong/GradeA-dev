"""
POST /auth/register/student must only complete STUDENT invitations.

It finds the row by `activation_token` alone (no email, no user_type) and then
sets the caller's password, activates and verifies the row. A self-registered
TEACHER's pending row carries a 6-digit activation code from the verification
email in the same column, so that code completes the teacher's account
through the student door with a password the caller chose.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CALLER_PASSWORD = "Caller-Chosen-Pw-77"  # pragma: allowlist secret


@override_settings(CACHES=LOCMEM_CACHE)
class RegisterStudentTokenScopeTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _pending(self, email, user_type, token="123456"):
        return User.objects.create_user(
            email=email,
            password=None,
            first_name="Pending",
            last_name="Row",
            user_type=user_type,
            is_active=False,
            activation_token=token,
            activation_expires=timezone.now() + timezone.timedelta(minutes=15),
        )

    def _complete_as_student(self, token):
        return self.client.post(
            reverse("auth-register-student"),
            {
                "token": token,
                "first_name": "Caller",
                "last_name": "Chosen",
                "password": CALLER_PASSWORD,
            },
            format="json",
        )

    def test_a_pending_teacher_row_cannot_be_completed_through_the_student_door(self):
        teacher = self._pending("pending.teacher@gmail.com", UserTypes.TEACHER)

        self._complete_as_student("123456")

        login = self.client.post(
            reverse("login"),
            {"email": "pending.teacher@gmail.com", "password": CALLER_PASSWORD},
            format="json",
        )
        self.assertNotEqual(login.status_code, status.HTTP_200_OK)
        teacher.refresh_from_db()
        self.assertFalse(teacher.is_active)
        self.assertIsNone(teacher.email_verified_at)
        self.assertFalse(teacher.check_password(CALLER_PASSWORD))

    def test_a_pending_student_row_still_completes(self):
        student = self._pending("pending.student@student.local", UserTypes.STUDENT)

        response = self._complete_as_student("123456")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        student.refresh_from_db()
        self.assertTrue(student.is_active)
        self.assertTrue(student.check_password(CALLER_PASSWORD))
