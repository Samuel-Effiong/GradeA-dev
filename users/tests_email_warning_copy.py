"""The "not you?" warning in the verification and password-reset emails.

Founder decision 2026-09-28: the cancelled "Not you?" link is replaced by a
wording change only. These tests pin the approved text verbatim and that
neither email gained a link: the verification email carries only its
activation link, the reset email none at all.
"""

import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import PasswordResetOTP, UserTypes
from users.services import send_user_activation_email

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "a-strong-password-42"  # pragma: allowlist secret

VERIFY_WARNING = (
    "Didn't sign up? Someone may have used your email address. Don't click "
    "the activation link. Just ignore this email and the account won't be "
    "activated. Never share this link or code with anyone, including Grade A+ "
    "staff."
)
RESET_WARNING = (
    "Didn't ask for this? Someone may be trying to get into your account. "
    "Don't share this code with anyone, including Grade A+ staff. Your "
    "password hasn't been changed. If you didn't request this, you can ignore "
    "this email."
)

# Anything a mail client would render as, or turn into, a link.
LINK = re.compile(r"https?://|href\s*=|www\.", re.IGNORECASE)


@override_settings(
    FRONTEND_DOMAIN="teacher.example.test",
    STUDENT_FRONTEND_DOMAIN="student.example.test",
)
class VerificationEmailWarningTests(TestCase):
    def send_for(self, user_type, email):
        user = User.objects.create_user(
            email=email,
            password=PASSWORD,
            first_name="Warn",
            last_name="Ing",
            user_type=user_type,
            is_active=False,
        )
        with patch("users.services.send_email_task.delay") as mock_send:
            send_user_activation_email(user)
        mock_send.assert_called_once()
        return mock_send.call_args.kwargs["merge_data"]

    def test_teacher_email_carries_the_approved_warning(self):
        merge_data = self.send_for(UserTypes.TEACHER, "warn.teacher@example.com")
        self.assertIn(VERIFY_WARNING, merge_data["bottom_content"])

    def test_student_email_carries_the_approved_warning(self):
        merge_data = self.send_for(UserTypes.STUDENT, "warn.student@example.com")
        self.assertIn(VERIFY_WARNING, merge_data["bottom_content"])

    def test_old_safely_ignore_line_is_gone(self):
        merge_data = self.send_for(UserTypes.TEACHER, "warn.old@example.com")
        self.assertNotIn("If you did not create this account", str(merge_data))

    def test_no_link_other_than_the_activation_link(self):
        merge_data = self.send_for(UserTypes.TEACHER, "warn.links@example.com")
        self.assertRegex(merge_data["activation_url"], LINK)
        for key, value in merge_data.items():
            if key == "activation_url" or not isinstance(value, str):
                continue
            with self.subTest(key=key):
                self.assertNotRegex(value, LINK)


@override_settings(CACHES=LOCMEM_CACHE)
class ResetPasswordEmailWarningTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            email="warn.reset@gmail.com",
            password=PASSWORD,
            first_name="Warn",
            last_name="Reset",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )

    def reset_email(self):
        with patch("users.views.safe_delay") as mock_delay:
            response = self.client.post(
                reverse("auth-otp"),
                {"email": self.user.email, "otp_type": "RESET_PASSWORD"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once()
        return mock_delay.call_args.kwargs["message"]

    def test_reset_email_carries_the_approved_warning(self):
        self.assertIn(RESET_WARNING, self.reset_email())

    def test_reset_email_still_carries_the_code(self):
        message = self.reset_email()
        self.assertIn(PasswordResetOTP.objects.get(user=self.user).code, message)

    def test_old_remain_secure_line_is_gone(self):
        self.assertNotIn("your account will remain secure", self.reset_email())

    def test_reset_email_has_no_link(self):
        self.assertNotRegex(self.reset_email(), LINK)
