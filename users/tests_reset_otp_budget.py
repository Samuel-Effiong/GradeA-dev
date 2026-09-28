"""
AUTHZ-L2: the reset-OTP guess budget must not be wipeable by re-requesting.

Before the fix, POST /auth/otp {RESET_PASSWORD} called generate_code(), which
set attempts=0 and locked_until=None. So an attacker could burn the 5-guess
budget, re-request a code (wiping the lock) and repeat, limited only by the
per-IP OTPRequestThrottle - which a multi-IP attacker sidesteps. The budget is
per ACCOUNT, so it has to survive a re-request; every test here widens the
per-IP throttles (modelling many IPs) so that only the account-level budget
is under test.

The suite talks to the real endpoints and reads the OTP row only where a test
plays the role of the victim's mailbox (email delivery is mocked).
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework.throttling import SimpleRateThrottle

from users.models import CustomUser, PasswordResetOTP, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
NEW_PASSWORD = "brand-new-Passw0rd-42"  # pragma: allowlist secret
MANY_IPS = {  # pragma: allowlist secret
    "otp_request": "1000000/hour",
    "password_reset": "1000000/hour",  # pragma: allowlist secret
}


def make_user(email="victim@example.com"):
    return CustomUser.objects.create_user(
        email=email,
        password="original-Passw0rd-1",  # pragma: allowlist secret
        first_name="Vic",
        last_name="Tim",
        user_type=UserTypes.TEACHER,
        is_active=True,
        email_verified_at=timezone.now(),
    )


class Base(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        p = patch.dict(SimpleRateThrottle.THROTTLE_RATES, MANY_IPS)
        p.start()
        self.addCleanup(p.stop)
        m = patch("users.views.safe_delay")
        self.mail = m.start()
        self.addCleanup(m.stop)
        self.user = make_user()
        self.otp_url = reverse("auth-otp")
        self.reset_url = reverse("auth-reset-password")

    def request_code(self, client=None):
        return (client or self.client).post(
            self.otp_url,
            {"email": self.user.email, "otp_type": "RESET_PASSWORD"},
            format="json",
        )

    def guess(self, code, client=None):
        return (client or self.client).post(
            self.reset_url,
            {"email": self.user.email, "otp": code, "new_password": NEW_PASSWORD},
            format="json",
        )

    def wrong(self):
        code = PasswordResetOTP.objects.get(user=self.user).code
        return "000000" if code != "000000" else "000001"

    def row(self):
        return PasswordResetOTP.objects.get(user=self.user)

    @staticmethod
    def is_lockout_reply(response):
        return (
            response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
            and response.json()["error"]["field_errors"]["code"] == "RESET_LOCKED"
        )


@override_settings(CACHES=LOCMEM)
class CounterWipeAdversarialTests(Base):
    def test_resend_does_not_clear_a_lock(self):
        self.request_code()
        for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
            self.guess(self.wrong())
        self.assertTrue(self.row().is_locked())
        locked_until = self.row().locked_until
        code_before = self.row().code

        response = self.request_code()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.row().refresh_from_db()
        self.assertTrue(self.row().is_locked(), "re-requesting wiped the lock")
        self.assertEqual(self.row().locked_until, locked_until)
        self.assertEqual(self.row().code, code_before)

    def test_a_locked_account_is_refused_even_with_the_current_correct_code(self):
        self.request_code()
        for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
            self.guess(self.wrong())
        self.request_code()
        response = self.guess(self.row().code)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertTrue(self.is_lockout_reply(response))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("original-Passw0rd-1"))

    def test_locked_resend_sends_no_email_and_looks_identical_to_the_caller(self):
        self.request_code()
        ok = self.request_code()
        for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
            self.guess(self.wrong())
        self.mail.reset_mock()
        locked = self.request_code()
        self.assertEqual((locked.status_code, locked.data), (ok.status_code, ok.data))
        self.mail.assert_not_called()

    def test_locking_emits_a_warning_naming_the_account_but_not_the_code(self):
        self.request_code()
        code = self.row().code
        with self.assertLogs("users.models", level="WARNING") as logs:
            for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
                self.guess(self.wrong())
        locked = [r for r in logs.records if r.msg == "password_reset_otp_locked"]
        self.assertEqual(len(locked), 1)
        self.assertEqual(locked[0].user_id, str(self.user.pk))
        self.assertNotIn(code, logs.output[0])
        self.assertNotIn(self.user.email, logs.output[0])

    def test_attempts_carry_across_a_resend(self):
        self.request_code()
        for _ in range(3):
            self.guess(self.wrong())
        self.request_code()
        self.assertEqual(self.row().attempts, 3)
        for _ in range(2):
            self.guess(self.wrong())
        self.assertTrue(self.row().is_locked())

    def test_many_ip_brute_force_cycle_gets_only_one_budget(self):
        """The takeover loop: 5 guesses, resend, repeat. Count how many
        guesses were actually EVALUATED (not refused as locked)."""
        self.request_code()
        evaluated = 0
        for _ in range(40):
            attacker = APIClient()  # a fresh client per cycle ~ a fresh IP
            for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
                r = self.guess(self.wrong(), attacker)
                if not self.is_lockout_reply(r):
                    evaluated += 1
            self.request_code(attacker)
        self.assertLessEqual(evaluated, PasswordResetOTP.MAX_ATTEMPTS)

    def test_the_correct_code_still_works_after_the_budget_is_spent_elsewhere(self):
        """Budget exhaustion must not let a wrong-guess flood succeed."""
        self.request_code()
        for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
            self.guess(self.wrong())
        self.assertEqual(
            self.guess(self.row().code).status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )


@override_settings(CACHES=LOCMEM)
class AccountIsolationTests(Base):
    """Gate 9: the budget is per account - one victim's lock must not touch
    another account, and guesses at one account must not spend another's."""

    def test_locking_one_account_does_not_affect_another(self):
        other = make_user("bystander@example.com")
        self.request_code()
        for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
            self.guess(self.wrong())
        self.assertTrue(self.row().is_locked())

        self.client.post(
            self.otp_url,
            {"email": other.email, "otp_type": "RESET_PASSWORD"},
            format="json",
        )
        other_row = PasswordResetOTP.objects.get(user=other)
        self.assertFalse(other_row.is_locked())
        self.assertEqual(other_row.attempts, 0)
        ok = self.client.post(
            self.reset_url,
            {"email": other.email, "otp": other_row.code, "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)

    def test_wrong_guesses_at_one_account_do_not_count_against_another(self):
        other = make_user("bystander2@example.com")
        self.request_code()
        self.client.post(
            self.otp_url,
            {"email": other.email, "otp_type": "RESET_PASSWORD"},
            format="json",
        )
        for _ in range(3):
            self.guess(self.wrong())
        self.assertEqual(PasswordResetOTP.objects.get(user=other).attempts, 0)


@override_settings(CACHES=LOCMEM)
class LegitimateRecoveryTests(Base):
    def test_mistype_then_resend_then_reset_works(self):
        self.request_code()
        self.guess(self.wrong())
        self.guess(self.wrong())
        self.request_code()
        response = self.guess(self.row().code)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertFalse(PasswordResetOTP.objects.filter(user=self.user).exists())

    def test_a_fresh_reset_after_success_has_a_full_budget(self):
        self.request_code()
        for _ in range(3):
            self.guess(self.wrong())
        self.guess(self.row().code)  # success deletes the row
        self.request_code()
        self.assertEqual(self.row().attempts, 0)

    def test_resend_after_the_lock_expires_recovers_with_a_full_budget(self):
        self.request_code()
        for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
            self.guess(self.wrong())
        PasswordResetOTP.objects.filter(user=self.user).update(
            locked_until=timezone.now() - timedelta(seconds=1)
        )
        self.request_code()
        self.assertFalse(self.row().is_locked())
        self.assertEqual(self.row().attempts, 0)
        self.assertEqual(self.guess(self.row().code).status_code, status.HTTP_200_OK)

    def test_a_resent_code_is_valid_even_if_the_row_is_older_than_15_minutes(self):
        """Adjacent bug: created_at was set once (auto_now_add), so a code
        re-issued 16+ minutes after the row was first created was rejected
        as expired the moment it arrived."""
        self.request_code()
        PasswordResetOTP.objects.filter(user=self.user).update(
            created_at=timezone.now() - timedelta(minutes=16)
        )
        self.request_code()
        self.assertEqual(self.guess(self.row().code).status_code, status.HTTP_200_OK)

    def test_stale_partial_attempts_do_not_haunt_a_later_reset(self):
        self.request_code()
        for _ in range(4):
            self.guess(self.wrong())
        PasswordResetOTP.objects.filter(user=self.user).update(
            created_at=timezone.now() - timedelta(minutes=16)
        )
        self.request_code()
        self.assertEqual(self.row().attempts, 0)


@override_settings(CACHES=LOCMEM)
class ConcurrencyTests(TransactionTestCase):
    """register_failure was a read-modify-write: N parallel wrong guesses could
    all read attempts=k and write k+1, so a parallel burst got far more than
    MAX_ATTEMPTS evaluations. TransactionTestCase so threads really commit."""

    WORKERS = 20

    def setUp(self):
        cache.clear()
        p = patch.dict(SimpleRateThrottle.THROTTLE_RATES, MANY_IPS)
        p.start()
        self.addCleanup(p.stop)
        self.user = make_user("race@example.com")
        PasswordResetOTP.objects.create(user=self.user).generate_code()

    def test_parallel_wrong_guesses_are_all_counted(self):
        barrier = threading.Barrier(self.WORKERS)
        url = reverse("auth-reset-password")

        # Unreachable budget, so every guess is evaluated and must be counted.
        def go():
            client = APIClient()
            try:
                barrier.wait()
                return client.post(
                    url,
                    {
                        "email": self.user.email,
                        "otp": "not-it",
                        "new_password": NEW_PASSWORD,
                    },
                    format="json",
                ).status_code
            finally:
                connection.close()

        with patch.object(PasswordResetOTP, "MAX_ATTEMPTS", 10_000):
            with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
                list(pool.map(lambda _: go(), range(self.WORKERS)))

        self.assertEqual(
            PasswordResetOTP.objects.get(user=self.user).attempts,
            self.WORKERS,
            "an increment was lost under concurrency",
        )


@override_settings(CACHES=LOCMEM)
class LockedReset429Tests(Base):
    """Founder decision 2026-09-28: while the reset code is locked,
    POST /auth/reset-password answers 429 and tells the person why, until
    when, and that nothing changed. /auth/otp stays generic (covered by
    test_locked_resend_sends_no_email_and_looks_identical_to_the_caller)."""

    def lock(self):
        self.request_code()
        for _ in range(PasswordResetOTP.MAX_ATTEMPTS - 1):
            self.guess(self.wrong())
        return self.guess(self.wrong())  # the guess that spends the budget

    def test_the_guess_that_spends_the_budget_already_gets_the_429(self):
        response = self.lock()

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertTrue(self.row().is_locked())

    def test_the_429_carries_code_locked_until_retry_after_and_the_message(self):
        self.lock()
        before = timezone.now()

        response = self.guess(self.row().code)

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        body = response.json()
        self.assertIs(body["success"], False)
        fields = body["error"]["field_errors"]
        self.assertEqual(fields["code"], "RESET_LOCKED")
        locked_until = self.row().locked_until
        self.assertEqual(fields["locked_until"], locked_until.isoformat())
        self.assertTrue(fields["locked_until"].endswith("+00:00"))
        retry_after = fields["retry_after_seconds"]
        self.assertIsInstance(retry_after, int)
        self.assertTrue(
            0 < retry_after <= int((locked_until - before).total_seconds()) + 1
        )
        self.assertEqual(response["Retry-After"], str(retry_after))
        # The founder's wording, as the envelope's top-level message.
        minutes = -(-retry_after // 60)
        expected = (
            "For your security, password reset is paused on this account "
            "because the code was entered incorrectly 5 times. You can request "
            f"a new code after {locked_until:%H:%M} UTC (in {minutes} minutes). "
            "Your password has not been changed, and you can still sign in "
            "with your current password. If you didn't try to reset your "
            "password, someone else may have. Your account is still safe."
        )
        self.assertEqual(fields["message"], expected)
        self.assertEqual(body["message"], expected)

    def test_the_password_is_unchanged_and_still_signs_in(self):
        self.lock()
        self.guess(self.row().code)

        login = self.client.post(
            reverse("login"),
            {
                "email": self.user.email,
                "password": "original-Passw0rd-1",  # pragma: allowlist secret
            },
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK, login.content)

    def test_an_ordinary_wrong_guess_is_still_the_generic_400(self):
        self.request_code()

        response = self.guess(self.wrong())

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.json()["message"], "Invalid email, OTP code, or new password."
        )


class StructuredRefusalRenderingTests(APITestCase):
    """users.renderers: an error body carrying a string `code` and a string
    `message` shows that message, not a numbered list of every key."""

    def test_code_plus_message_renders_the_message(self):
        from users.renderers import flatten_errors

        data = {
            "code": "RESET_LOCKED",
            "message": "Paused.",
            "locked_until": "2026-01-01T00:00:00+00:00",
            "retry_after_seconds": 60,
        }
        self.assertEqual(flatten_errors(data), "Paused.")

    def test_billing_style_error_plus_code_is_unchanged(self):
        from users.renderers import flatten_errors

        self.assertEqual(
            flatten_errors({"error": "Not enough credits.", "code": "X"}),
            "Not enough credits.",
        )

    def test_a_field_named_message_without_a_code_is_unchanged(self):
        from users.renderers import flatten_errors

        self.assertEqual(
            flatten_errors({"message": ["Too long."], "title": ["Required."]}),
            "1. Message: Too long.\n 2. Title: Required.\n",
        )
