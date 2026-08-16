"""
Rate-limiting and OTP-lockout tests for the unauthenticated auth surface.

Before these, the project had no throttling at all and `PasswordResetOTP`
had no attempt counter, so a short numeric reset code could be guessed at
unlimited speed for its full 15-minute validity window - an account
takeover path requiring nothing but the victim's email address.

Two mechanics worth knowing when editing this file:

- DRF binds `SimpleRateThrottle.THROTTLE_RATES` to the settings dict at
  class-definition time (rest_framework/throttling.py), so
  `override_settings(REST_FRAMEWORK=...)` does NOT change a rate that is
  already loaded. Tightening a rate for a test therefore has to patch the
  class attribute, which is what `tightened_rate` below does.
- Throttle counters live in the default cache, so every test pins it to
  LocMem and clears it. Without that, counts leak between tests and
  between this suite and anything else sharing Redis.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from users.models import PasswordResetOTP, UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def tightened_rate(scope, rate):
    """Temporarily lower one named throttle bucket, so a test needs few calls."""
    return patch.dict(SimpleRateThrottle.THROTTLE_RATES, {scope: rate})


@override_settings(CACHES=LOCMEM_CACHE)
class AuthThrottlingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="throttle.user@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Throttle",
            last_name="User",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )

    def tearDown(self):
        cache.clear()

    def test_otp_requests_are_throttled(self):
        url = reverse("auth-otp")
        payload = {"email": self.user.email, "otp_type": "RESET_PASSWORD"}

        with tightened_rate("otp_request", "3/hour"):
            for _ in range(3):
                response = self.client.post(url, payload)
                self.assertNotEqual(
                    response.status_code, status.HTTP_429_TOO_MANY_REQUESTS
                )

            response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_login_is_throttled(self):
        url = reverse("login")
        payload = {
            "email": self.user.email,
            "password": "wrong-password",  # pragma: allowlist secret
        }

        with tightened_rate("login", "3/min"):
            for _ in range(3):
                response = self.client.post(url, payload)
                self.assertNotEqual(
                    response.status_code, status.HTTP_429_TOO_MANY_REQUESTS
                )

            response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_registration_is_throttled(self):
        url = reverse("auth-register")

        with tightened_rate("register", "2/hour"):
            for index in range(2):
                response = self.client.post(
                    url,
                    {
                        "email": f"throttle.reg{index}@example.com",
                        "password": "some-strong-password-1",  # pragma: allowlist secret
                        "first_name": "Reg",
                        "last_name": "Throttle",
                    },
                )
                self.assertNotEqual(
                    response.status_code, status.HTTP_429_TOO_MANY_REQUESTS
                )

            response = self.client.post(
                url,
                {
                    "email": "throttle.reg-final@example.com",
                    "password": "some-strong-password-1",  # pragma: allowlist secret
                    "first_name": "Reg",
                    "last_name": "Throttle",
                },
            )

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


@override_settings(CACHES=LOCMEM_CACHE)
class PasswordResetOTPLockoutTests(APITestCase):
    NEW_PASSWORD = "a-brand-new-password-42"  # pragma: allowlist secret

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="lockout.user@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Lockout",
            last_name="User",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        self.otp = PasswordResetOTP.objects.create(user=self.user)
        self.code = str(self.otp.generate_code())
        self.url = reverse("auth-reset-password")

    def tearDown(self):
        cache.clear()

    @property
    def wrong_code(self):
        """A value the same shape as a real code but definitely not this one."""
        return "0" * len(self.code) if self.code != "0" * len(self.code) else "1" * 6

    def submit(self, otp):
        return self.client.post(
            self.url,
            {
                "email": self.user.email,
                "otp": otp,
                "new_password": self.NEW_PASSWORD,
            },
        )

    def test_brute_force_locks_out_even_with_correct_code(self):
        """
        The important one: once the attempt budget is spent, the *correct*
        code must stop working too. Otherwise an attacker who happens to
        guess right on the last permitted try still takes the account.
        """
        for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
            self.assertEqual(
                self.submit(self.wrong_code).status_code,
                status.HTTP_400_BAD_REQUEST,
            )

        self.otp.refresh_from_db()
        self.assertEqual(self.otp.attempts, PasswordResetOTP.MAX_ATTEMPTS)
        self.assertIsNotNone(self.otp.locked_until)
        self.assertTrue(self.otp.is_locked())

        response = self.submit(self.code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password(self.NEW_PASSWORD))

    def test_correct_code_still_works_within_attempt_budget(self):
        """Regression guard: a couple of typos must not break real recovery."""
        for _ in range(2):
            self.submit(self.wrong_code)

        response = self.submit(self.code)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.NEW_PASSWORD))
        self.assertFalse(PasswordResetOTP.objects.filter(user=self.user).exists())

    def test_generate_code_clears_the_lockout(self):
        """
        Requesting a fresh code is the intended way out of a lockout - and
        the reason OTPRequestThrottle has to stay on the issuing endpoint,
        or this doubles as the attacker's reset button.
        """
        self.otp.attempts = PasswordResetOTP.MAX_ATTEMPTS
        self.otp.locked_until = timezone.now() + timezone.timedelta(minutes=30)
        self.otp.save(update_fields=["attempts", "locked_until"])

        self.otp.generate_code()

        self.otp.refresh_from_db()
        self.assertEqual(self.otp.attempts, 0)
        self.assertIsNone(self.otp.locked_until)
        self.assertFalse(self.otp.is_locked())
