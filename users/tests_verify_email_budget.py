"""H-53: POST /auth/verify has a per-address budget of attempts.

Before: only the per-IP VerifyEmailThrottle (5/hour) stood between an
attacker and a 6-digit code valid for 15 minutes (24 hours for an invite).
With many IPs, whoever registered with someone else's address could guess
the code and activate the account without the email. Now 5 wrong codes for
one address - from any number of IPs - lock the address: every attempt is
refused until the lock ends, a correct or re-sent code included. The stored
code is left alone, because the same field holds invitations. Unknown
addresses lock the same way, so the lock is no oracle.

Every request here comes from a different IP, the attack being fixed.
"""

import itertools
import threading
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from users.models import UserTypes
from users.throttling import (
    _verify_address_key,
    reserve_verify_attempt,
    verify_lock_until,
)

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CODE = "246810"
LOCKED_MESSAGE = (
    "Too many incorrect codes for this email address. Please wait, then "
    "request a new verification email."
)


@override_settings(
    CACHES=LOCMEM_CACHE, VERIFY_EMAIL_MAX_FAILURES=5, VERIFY_EMAIL_LOCK_SECONDS=1800
)
class VerifyEmailBudgetTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.ips = (f"10.53.{i // 250}.{i % 250 + 1}" for i in itertools.count())
        self.user = self.pending("h53.victim@example.com")
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)

    def pending(self, email):
        return User.objects.create_user(
            email=email,
            password="attacker-chosen-Pw-1",  # pragma: allowlist secret
            first_name="Pend",
            last_name="Ing",
            user_type=UserTypes.TEACHER,
            is_active=False,
            activation_token=CODE,
            activation_expires=timezone.now() + timedelta(minutes=15),
        )

    def verify(self, email, token):
        return self.client.post(
            reverse("auth-verify"),
            {"email": email, "token": token},
            format="json",
            REMOTE_ADDR=next(self.ips),
        )

    def spend_budget(self, email):
        for _ in range(5):
            self.assertEqual(self.verify(email, "000000").status_code, 400)

    def test_five_wrong_codes_from_five_ips_lock_the_address(self):
        """The correct code is refused once the budget is spent."""
        self.spend_budget(self.user.email)

        response = self.verify(self.user.email, CODE)

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(
            response.json()["message"].split(" Expected available")[0],
            LOCKED_MESSAGE,
        )
        self.assertTrue(response.has_header("Retry-After"))
        self.assertLessEqual(int(response["Retry-After"]), 1800)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertIsNone(self.user.email_verified_at)

    def test_guesses_under_way_count_before_they_are_answered(self):
        """The burst: five guesses from five IPs have each spent their
        attempt but none has been answered yet, so no lock exists. A sixth,
        even with the correct code, is refused - the attempt is spent before
        the code is checked, not counted after a wrong answer."""
        for _ in range(5):
            reserve_verify_attempt(self.user.email)

        response = self.verify(self.user.email, CODE)

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_an_expired_code_spends_the_budget_too(self):
        """Otherwise the fifth attempt, an expired code, would fill the budget
        without locking: /auth/otp would keep sending codes that verify
        refuses."""
        for _ in range(4):
            self.verify(self.user.email, "000000")
        User.objects.filter(pk=self.user.pk).update(
            activation_expires=timezone.now() - timedelta(minutes=1)
        )

        expired = self.verify(self.user.email, CODE)

        self.assertEqual(expired.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsNotNone(verify_lock_until(self.user.email))

    def test_a_lock_leaves_the_stored_code_alone(self):
        """activation_token also holds invitations: a lock must not let
        anyone destroy one with five wrong guesses. The code is dead only
        while the lock lasts."""
        self.spend_budget(self.user.email)

        self.user.refresh_from_db()
        self.assertEqual(self.user.activation_token, CODE)

    def test_four_wrong_codes_then_the_right_one_still_verifies(self):
        """A person who mistypes a few times is not locked out."""
        for _ in range(4):
            self.verify(self.user.email, "000000")

        response = self.verify(self.user.email, CODE)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_a_successful_verify_refunds_the_budget(self):
        for _ in range(4):
            self.verify(self.user.email, "000000")

        self.assertEqual(self.verify(self.user.email, CODE).status_code, 202)

        self.assertEqual(reserve_verify_attempt(self.user.email), 1)

    def test_an_unknown_address_locks_the_same_way(self):
        """No account-existence oracle: an unknown address answers exactly as
        a real one does, 400s then the 429."""
        responses = [self.verify("nobody.h53@example.com", "000000") for _ in range(5)]
        self.assertEqual([r.status_code for r in responses], [400] * 5)

        response = self.verify("nobody.h53@example.com", "000000")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_a_resent_code_neither_arrives_nor_unlocks(self):
        self.spend_budget(self.user.email)

        with patch("users.views.send_user_activation_email") as send:
            resend = self.client.post(
                reverse("auth-otp"),
                {"email": self.user.email, "otp_type": "VERIFY_EMAIL"},
                format="json",
                REMOTE_ADDR=next(self.ips),
            )

        self.assertEqual(resend.status_code, status.HTTP_202_ACCEPTED)
        send.assert_not_called()
        self.assertEqual(
            self.verify(self.user.email, CODE).status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    def test_after_the_lock_a_new_code_verifies(self):
        self.spend_budget(self.user.email)
        later = timezone.now().timestamp() + 1801

        with patch("users.throttling.time.time", return_value=later):
            User.objects.filter(pk=self.user.pk).update(
                activation_token="135790",
                activation_expires=timezone.now() + timedelta(minutes=15),
            )
            response = self.verify(self.user.email, "135790")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

    def test_one_address_locking_never_affects_another(self):
        other = self.pending("h53.other@example.com")
        self.spend_budget(self.user.email)

        self.assertEqual(
            self.verify(other.email, CODE).status_code, status.HTTP_202_ACCEPTED
        )

    def test_the_address_is_matched_case_insensitively(self):
        """Changing the capitals must not buy a fresh budget."""
        for variant in (
            "H53.victim@example.com",
            "h53.VICTIM@example.com",
            "h53.victim@EXAMPLE.com",
            " h53.victim@example.com ",
            "H53.Victim@Example.Com",
        ):
            self.verify(variant, "000000")

        self.assertEqual(
            self.verify(self.user.email, CODE).status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    def test_both_windows_last_the_whole_lock_period(self):
        """1a's N1: a short attempt window lets a guesser who paces 4 guesses
        a window never reach 5, and a short lock entry reopens early. Both
        entries are created lasting VERIFY_EMAIL_LOCK_SECONDS."""
        from users import throttling

        recording = MagicMock(wraps=throttling.cache)
        with patch.object(throttling, "cache", recording):
            self.spend_budget(self.user.email)

        timeouts = {}
        for call in recording.add.call_args_list:
            kind = call.args[0].split(":")[1]
            timeouts.setdefault(kind, set()).add(call.kwargs["timeout"])
        self.assertEqual(timeouts, {"attempts": {1800}, "locked": {1800}})

    def test_the_budget_fails_open_when_the_cache_is_down(self):
        """A cache outage costs the lock, never the sign-up. Only the budget's
        own cache reference is broken: DRF's per-IP throttle shares the real
        cache object and is not what is under test here."""
        broken = MagicMock()
        for method in ("get", "add", "incr", "set", "delete"):
            getattr(broken, method).side_effect = ConnectionError("cache down")
        with patch("users.throttling.cache", broken):
            self.assertEqual(self.verify(self.user.email, "000000").status_code, 400)
            response = self.verify(self.user.email, CODE)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)


@override_settings(
    CACHES=LOCMEM_CACHE, VERIFY_EMAIL_MAX_FAILURES=5, VERIFY_EMAIL_LOCK_SECONDS=1800
)
class VerifyEmailBurstTests(TransactionTestCase):
    """Gate 3, real threads and real commits: twenty guesses from twenty IPs
    all pass the lock check before any of them is answered. Only the
    budget's five may reach the code check."""

    GUESSES = 20

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        User.objects.create_user(
            email="h53.burst@example.com",
            password="attacker-chosen-Pw-1",  # pragma: allowlist secret
            first_name="Pend",
            last_name="Ing",
            user_type=UserTypes.TEACHER,
            is_active=False,
            activation_token=CODE,
            activation_expires=timezone.now() + timedelta(minutes=15),
        )

    def test_a_simultaneous_burst_gets_no_more_than_the_budget(self):
        from users import views

        barrier = threading.Barrier(self.GUESSES, timeout=30)
        real_lock_check = views.verify_lock_until
        statuses = []

        def lock_check_then_wait_for_the_others(email):
            until = real_lock_check(email)
            barrier.wait()
            return until

        def guess(i):
            try:
                response = APIClient().post(
                    reverse("auth-verify"),
                    {"email": "h53.burst@example.com", "token": "000000"},
                    format="json",
                    REMOTE_ADDR=f"10.53.200.{i + 1}",
                )
                statuses.append(response.status_code)
            finally:
                connection.close()

        with patch(
            "users.views.verify_lock_until", lock_check_then_wait_for_the_others
        ), patch("users.views.sync_user_to_mailerlite"):
            threads = [
                threading.Thread(target=guess, args=(i,)) for i in range(self.GUESSES)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)

        self.assertEqual(
            sorted(statuses),
            [status.HTTP_400_BAD_REQUEST] * 5
            + [status.HTTP_429_TOO_MANY_REQUESTS] * (self.GUESSES - 5),
        )
        self.assertFalse(User.objects.get(email="h53.burst@example.com").is_active)


@override_settings(CACHES=LOCMEM_CACHE)
class VerifyEmailLockDefaultTests(SimpleTestCase):
    def test_a_lock_whose_time_has_passed_is_no_lock(self):
        """The cache's own expiry is not trusted alone: Redis rounds a TTL
        to whole seconds, so the key can outlive the lock it records."""
        cache.set(_verify_address_key("locked", "h53@example.com"), 1.0, None)
        self.addCleanup(cache.clear)

        self.assertIsNone(verify_lock_until("h53@example.com"))

    def test_the_default_lock_outlasts_a_sign_up_code(self):
        """The lock does not clear the code, so the shipped lock must outlive
        a sign-up code (users.services: 15 minutes); otherwise that code
        could be guessed again after the lock."""
        from django.conf import settings

        self.assertGreater(settings.VERIFY_EMAIL_LOCK_SECONDS, 15 * 60)
