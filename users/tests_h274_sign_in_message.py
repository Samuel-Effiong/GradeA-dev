"""H-274 item (a): sign-in wrong-email-or-password returns a friendly
message with a coded envelope, not simplejwt's raw text."""

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
PASSWORD = "correct-horse-battery-staple"  # pragma: allowlist secret
WRONG = "definitely-not-correct"  # pragma: allowlist secret
EXPECTED_MESSAGE = (
    "That email and password don't match. "
    "Check them and try again, or reset your password."
)


def _make_teacher(email="h274-signin@example.com"):
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name="Sign",
        last_name="In",
        user_type=UserTypes.TEACHER,
        is_active=True,
        email_verified_at=timezone.now(),
    )


@override_settings(CACHES=LOCMEM_CACHE)
class SignInMessageTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = _make_teacher()
        self.url = reverse("login")

    def _login(self, email, password):
        return self.client.post(self.url, {"email": email, "password": password})

    def test_wrong_password_returns_friendly_message(self):
        resp = self._login(self.user.email, WRONG)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn(EXPECTED_MESSAGE, resp.json().get("message", ""))

    def test_wrong_password_returns_coded_envelope(self):
        resp = self._login(self.user.email, WRONG)
        data = resp.json()
        field_errors = data.get("error", {}).get("field_errors", {})
        self.assertEqual(field_errors.get("reason_code"), "INVALID_CREDENTIALS")
        self.assertEqual(field_errors.get("code"), "invalid_credentials")

    def test_nonexistent_email_returns_same_message(self):
        resp = self._login("nobody-here@example.com", WRONG)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn(EXPECTED_MESSAGE, resp.json().get("message", ""))

    def test_nonexistent_email_does_not_leak_existence(self):
        resp_wrong = self._login(self.user.email, WRONG)
        resp_missing = self._login("nobody-here@example.com", WRONG)
        self.assertEqual(
            resp_wrong.json().get("message"),
            resp_missing.json().get("message"),
        )

    def test_locked_account_returns_its_own_message(self):
        from datetime import timedelta

        self.user.locked_until = timezone.now() + timedelta(minutes=15)
        self.user.save(update_fields=["locked_until"])
        resp = self._login(self.user.email, PASSWORD)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn(EXPECTED_MESSAGE, resp.json().get("message", ""))
        self.assertIn(
            "Too many failed sign-in attempts", resp.json().get("message", "")
        )

    def test_simplejwt_text_not_in_response(self):
        resp = self._login(self.user.email, WRONG)
        body = resp.content.decode()
        self.assertNotIn("No active account found with the given credentials", body)
