"""H-43: POST /auth/otp's 202 says nothing about whether an account exists.

Before: every call answered 202, but an unknown address got "If an account
with that email exists, an OTP has been sent." and a real account got "An
OTP has been sent if an account with that email exists." - same status,
different words, so the text alone told a caller whether the address had an
account (1a's D2). Now every 202 branch returns the same bytes.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import PasswordResetOTP, UserTypes

User = get_user_model()
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM_CACHE)
class OtpReplyIsNoOracleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        # The sends happen through these; their return values never reach a
        # response (rule 14).
        for target in (
            "users.views.send_user_activation_email",
            "users.views.safe_delay",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ip = 0

    def account(self, email, verified):
        return User.objects.create_user(
            email=email,
            password="Correct-horse-1",  # pragma: allowlist secret
            first_name="Otp",
            last_name="Oracle",
            user_type=UserTypes.TEACHER,
            is_active=verified,
            email_verified_at=timezone.now() if verified else None,
        )

    def otp(self, email, otp_type):
        # A fresh IP each time: the 5/hour OTP throttle is not under test.
        self.ip += 1
        response = self.client.post(
            reverse("auth-otp"),
            {"email": email, "otp_type": otp_type},
            format="json",
            REMOTE_ADDR=f"10.43.0.{self.ip}",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        return response.content

    def test_every_202_is_byte_identical_to_the_unknown_address_reply(self):
        self.account("pending.h43@example.com", verified=False)
        self.account("verified.h43@example.com", verified=True)
        locked = self.account("locked.h43@example.com", verified=True)
        PasswordResetOTP.objects.create(
            user=locked, locked_until=timezone.now() + timedelta(minutes=30)
        )

        unknown = self.otp("nobody.h43@example.com", "VERIFY_EMAIL")
        replies = {
            "unknown, reset": self.otp("nobody.h43@example.com", "RESET_PASSWORD"),
            "verification sent": self.otp("pending.h43@example.com", "VERIFY_EMAIL"),
            "reset code sent": self.otp("verified.h43@example.com", "RESET_PASSWORD"),
            "reset locked": self.otp("locked.h43@example.com", "RESET_PASSWORD"),
        }

        for branch, body in replies.items():
            with self.subTest(branch=branch):
                self.assertEqual(body, unknown)
