"""H-198: POST /auth/otp tells nobody the state of an address.

Two answers used to differ from the unknown address's neutral 202:

- C: VERIFY_EMAIL for a verified, active account answered 400 "Email
  already verified. Please login.";
- G: RESET_PASSWORD for an account that never verified its email and is
  switched off or holds admin power answered 400 "Email not verified."
  (kept by H-164 as a hint to offer VERIFY_EMAIL).

The user's rulings (2026-10-25): both answer the neutral 202 with the
unknown address's bytes. C sends and issues nothing. G issues no reset code
(H-164's no-reset rule is unchanged); a G account WITHOUT admin power is sent
the activation email VERIFY_EMAIL would send, under the same H-53 lock and
the same throttle, and a G account with admin power is sent nothing.
"""

from typing import Any
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from users.models import PasswordResetOTP, UserTypes
from users.throttling import lock_verify_address

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "A-strong-password-198"  # pragma: allowlist secret


@override_settings(CACHES=LOCMEM)
class CodeRequestAnswersAreNeutralTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.ip = 0
        # The two mails, as tests_switched_off_means_out replaces them: the
        # activation mail's task and the reset mail's dispatch.
        activation = patch("users.services.send_email_task")
        self.activation_mail = activation.start()
        self.addCleanup(activation.stop)
        reset = patch("users.views.safe_delay")
        self.reset_mail = reset.start()
        self.addCleanup(reset.stop)

    def make(self, email, **fields):
        values = {
            "email": email,
            "password": PASSWORD,
            "first_name": "Otp",
            "last_name": "Neutral",
            "user_type": UserTypes.TEACHER,
            "is_active": True,
            "email_verified_at": timezone.now(),
        }
        values.update(fields)
        return User.objects.create_user(**values)

    def ask(self, email, otp_type):
        """One code request from a fresh address (the per-IP limit is 5/hour)."""
        self.ip += 1
        return self.client.post(
            reverse("auth-otp"),
            {"email": email, "otp_type": otp_type},
            format="json",
            REMOTE_ADDR=f"10.198.{self.ip // 250}.{self.ip % 250 + 1}",
        )

    def assert_neutral(self, response, otp_type):
        unknown = self.ask("nobody.h198@example.com", otp_type)
        self.assertEqual(response.status_code, unknown.status_code)
        self.assertEqual(response.content, unknown.content)

    def assert_nothing_issued(self, user):
        """No mail, no activation token, no reset code."""
        self.activation_mail.delay.assert_not_called()
        self.reset_mail.assert_not_called()
        user.refresh_from_db()
        self.assertIsNone(user.activation_token)
        self.assertFalse(PasswordResetOTP.objects.filter(user=user).exists())

    # -- C ------------------------------------------------------------------

    def test_a_verified_active_account_asking_for_verification_is_told_nothing(self):
        """R1."""
        user = self.make("verified.h198@example.com")

        response = self.ask(user.email, "VERIFY_EMAIL")

        self.assert_neutral(response, "VERIFY_EMAIL")

    # -- G, no admin power ----------------------------------------------------

    def test_a_never_verified_switched_off_account_asking_for_a_reset_is_told_nothing(
        self,
    ):
        """R2."""
        user = self.make(
            "pending.h198@example.com", is_active=False, email_verified_at=None
        )

        response = self.ask(user.email, "RESET_PASSWORD")

        self.assert_neutral(response, "RESET_PASSWORD")

    def test_that_account_is_sent_the_activation_email_and_no_reset_code(self):
        """R3: what VERIFY_EMAIL would send (the real activation road: a fresh
        token and its mail), and no reset code or reset mail."""
        user = self.make(
            "pending2.h198@example.com", is_active=False, email_verified_at=None
        )

        self.ask(user.email, "RESET_PASSWORD")

        self.activation_mail.delay.assert_called_once()
        user.refresh_from_db()
        self.assertIsNotNone(user.activation_token)
        self.assertFalse(PasswordResetOTP.objects.filter(user=user).exists())
        self.reset_mail.assert_not_called()

    def test_while_the_address_is_locked_that_account_is_sent_nothing(self):
        """R4: H-53's lock applies as it does to VERIFY_EMAIL."""
        user = self.make(
            "pending3.h198@example.com", is_active=False, email_verified_at=None
        )
        lock_verify_address(user.email)

        response = self.ask(user.email, "RESET_PASSWORD")

        self.assert_neutral(response, "RESET_PASSWORD")
        self.assert_nothing_issued(user)

    # -- G, admin power -------------------------------------------------------

    def test_a_never_verified_account_with_admin_power_is_told_and_sent_nothing(
        self,
    ):
        """R5: each of the three marks of admin power."""
        shapes: dict[str, dict[str, Any]] = {
            "staff": {"is_staff": True},
            "superuser flag": {"is_superuser": True},
            "SUPER_ADMIN type, switched off": {
                "user_type": UserTypes.SUPER_ADMIN,
                "is_active": False,
            },
        }
        for n, (name, fields) in enumerate(shapes.items()):
            with self.subTest(name):
                user = self.make(
                    f"power{n}.h198@example.com", email_verified_at=None, **fields
                )

                response = self.ask(user.email, "RESET_PASSWORD")

                self.assert_neutral(response, "RESET_PASSWORD")
                self.assert_nothing_issued(user)

    # -- controls -------------------------------------------------------------

    def test_c_sends_and_issues_nothing(self):
        """K1 (control): nothing was sent or made for C before, and nothing is
        now."""
        user = self.make("verified2.h198@example.com")

        self.ask(user.email, "VERIFY_EMAIL")

        self.assert_nothing_issued(user)

    def test_an_active_account_still_gets_a_reset_code(self):
        """K2 (control): road I, unchanged."""
        user = self.make("active.h198@example.com")

        response = self.ask(user.email, "RESET_PASSWORD")

        self.assert_neutral(response, "RESET_PASSWORD")
        self.assertTrue(PasswordResetOTP.objects.filter(user=user).exists())
        self.reset_mail.assert_called_once()

    def test_a_never_verified_account_still_gets_its_activation_email(self):
        """K3 (control): road F, unchanged."""
        user = self.make(
            "pending4.h198@example.com", is_active=False, email_verified_at=None
        )

        response = self.ask(user.email, "VERIFY_EMAIL")

        self.assert_neutral(response, "VERIFY_EMAIL")
        self.activation_mail.delay.assert_called_once()
        user.refresh_from_db()
        self.assertIsNotNone(user.activation_token)
