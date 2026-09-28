"""
POST /auth/register/student must only complete STUDENT invitations.

It finds the row by `activation_token` alone (no email, no user_type) and then
sets the caller's password, activates and verifies the row. A self-registered
TEACHER's pending row carries a 6-digit activation code from the verification
email in the same column, so that code completes the teacher's account
through the student door with a password the caller chose.
"""

import time
from unittest.mock import patch

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

    def test_the_teacher_refusal_is_the_same_400_as_an_unknown_code(self):
        self._pending("pending.teacher@gmail.com", UserTypes.TEACHER)

        teacher_code = self._complete_as_student("123456")
        unknown_code = self._complete_as_student("654321")

        self.assertEqual(teacher_code.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(teacher_code.json(), unknown_code.json())

    def test_a_pending_school_admin_row_cannot_be_completed_either(self):
        admin = self._pending(
            "pending.admin@school.example", UserTypes.SCHOOL_ADMIN, token="adm-tok"
        )

        response = self._complete_as_student("adm-tok")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        admin.refresh_from_db()
        self.assertFalse(admin.is_active)

    def test_renewing_a_teacher_code_is_the_same_400_as_an_unknown_code(self):
        """The renew door had the same token-only lookup; a teacher's code
        reached renew_activation_token()'s ValueError and answered 500."""
        teacher = self._pending("pending.teacher@gmail.com", UserTypes.TEACHER)
        url = reverse("course-renew-activation-token")

        teacher_code = self.client.post(url, {"token": "123456"}, format="json")
        unknown_code = self.client.post(url, {"token": "654321"}, format="json")

        self.assertEqual(teacher_code.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(teacher_code.json(), unknown_code.json())
        teacher.refresh_from_db()
        self.assertEqual(teacher.activation_token, "123456")

    def test_a_pending_student_row_still_completes(self):
        student = self._pending("pending.student@student.local", UserTypes.STUDENT)

        response = self._complete_as_student("123456")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        student.refresh_from_db()
        self.assertTrue(student.is_active)
        self.assertTrue(student.check_password(CALLER_PASSWORD))


@override_settings(
    CACHES=LOCMEM_CACHE,
    REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT=3,
    REGISTER_STUDENT_FAILURE_WINDOW_SECONDS=3600,
)
class RegisterStudentGlobalFailureBudgetTests(APITestCase):
    """Per-IP throttling doesn't bound a guesser spread over many IPs; the
    global budget of failed attempts does."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        # Only the global budget is under test here: take the per-IP bucket
        # out so it can't trip first.
        from users.views import AuthViewSet

        patcher = patch.object(AuthViewSet, "get_throttles", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, token):
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

    def _student(self, token, expires_in_minutes=15):
        return User.objects.create_user(
            email=f"s.{token}@student.local",
            password=None,
            first_name="Pending",
            last_name="Student",
            user_type=UserTypes.STUDENT,
            is_active=False,
            activation_token=token,
            activation_expires=timezone.now()
            + timezone.timedelta(minutes=expires_in_minutes),
        )

    def test_once_the_budget_is_spent_even_a_valid_code_gets_429(self):
        student = self._student("111111")
        for guess in ("900001", "900002", "900003"):
            self.assertEqual(self._post(guess).status_code, 400)

        response = self._post("111111")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        student.refresh_from_db()
        self.assertFalse(student.is_active)

    def test_the_429_says_why_and_when_and_sets_retry_after(self):
        for guess in ("900001", "900002", "900003"):
            self._post(guess)

        with self.assertLogs("users.throttling", level="WARNING") as logs:
            response = self._post("111111")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        retry_after = int(response["Retry-After"])
        self.assertTrue(1 <= retry_after <= 3600, retry_after)
        message = response.json()["message"]
        self.assertIn("paused", message)
        self.assertIn("invitation is still valid", message)
        self.assertEqual(
            [r.event for r in logs.records], ["register_student.budget_refusal"]
        )

    def test_exhausting_the_budget_logs_one_error_for_alerting(self):
        with self.assertLogs("users.throttling", level="WARNING") as logs:
            for guess in ("900001", "900002", "900003"):
                self._post(guess)

        errors = [r for r in logs.records if r.levelname == "ERROR"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].event, "register_student.budget_exhausted")
        self.assertEqual(errors[0].limit, 3)
        self.assertNotIn("900003", " ".join(logs.output) + repr(errors[0].__dict__))

    def test_below_the_budget_a_valid_code_still_works(self):
        student = self._student("111111")
        for guess in ("900001", "900002"):
            self._post(guess)

        response = self._post("111111")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        student.refresh_from_db()
        self.assertTrue(student.is_active)

    def test_the_budget_resets_with_the_next_window(self):
        student = self._student("111111")
        for guess in ("900001", "900002", "900003"):
            self._post(guess)
        self.assertEqual(self._post("111111").status_code, 429)

        with patch("users.throttling.time.time", return_value=time.time() + 3600):
            response = self._post("111111")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        student.refresh_from_db()
        self.assertTrue(student.is_active)

    def test_an_expired_code_counts_as_a_failure(self):
        self._student("222222", expires_in_minutes=-5)
        for _ in range(3):
            self._post("222222")

        self.assertEqual(self._post("222222").status_code, 429)

    def test_a_success_does_not_count(self):
        for token in ("311111", "322222", "333333", "344444"):
            self._student(token)
            self.assertEqual(self._post(token).status_code, 200)

    def test_failures_are_logged_without_the_code_or_any_email(self):
        self._student("111111")
        with self.assertLogs("users.throttling", level="WARNING") as logs:
            self._post("987654")

        record = logs.records[0]
        self.assertEqual(record.reason, "no_match")
        self.assertEqual(record.window_failures, 1)
        rendered = " ".join(logs.output) + repr(record.__dict__)
        self.assertNotIn("987654", rendered)
        self.assertNotIn("@", rendered)
