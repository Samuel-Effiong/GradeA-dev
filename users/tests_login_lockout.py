"""
Per-account login lockout tests.

Before this, LoginThrottle (users/throttling.py) was the only defense
against password brute-forcing, and it keys on IP - an attacker spreading
guesses across many IPs (proxies, a botnet) could try one account's
password at effectively unlimited speed. CustomUser.register_failed_login /
is_account_locked / reset_login_lockout (users/models.py) add a per-account
budget on top of that, mirroring the existing PasswordResetOTP
attempts/locked_until pattern.

Two mechanics worth knowing when editing this file:

- LoginThrottle would otherwise interfere with tests that submit more than
  10 requests/min against the same IP, so every test here widens that rate
  via `tightened_rate` (see users.tests_throttling) rather than disabling
  throttling altogether - we want to prove the lockout works *underneath*
  the throttle, not with it removed from the picture.
- The concurrency test uses TransactionTestCase, not APITestCase. DRF's
  APITestCase wraps each test in an outer atomic transaction, so writes
  made by worker threads (each on their own DB connection) would not be
  visible to each other or to the main thread until the test ends - i.e.
  it would look "safe" no matter how badly register_failed_login raced.
  TransactionTestCase commits for real, which is required to actually
  exercise the race.
"""

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import LiveServerTestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import UserTypes
from users.serializers import CustomTokenObtainPairSerializer
from users.tests_throttling import tightened_rate

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

PASSWORD = "correct-horse-battery-staple-1"  # pragma: allowlist secret
WRONG_PASSWORD = "definitely-the-wrong-password"  # pragma: allowlist secret

# WHY THE CONCURRENCY CLASSES BELOW OVERRIDE THE HASHER
# -----------------------------------------------------
# Every login attempt, right or wrong, runs a full password verification:
# PBKDF2-SHA256 at 1,000,000 iterations, deliberately expensive. That is
# correct in production and ruinous here, because these tests fire 25-30
# of them AT ONCE into a single process.
#
# It broke CI on 2026-09-17 (run 35271899755, beta 301d915): 30 concurrent
# verifies saturated the LiveServer on a 4-vCPU runner under coverage, and
# a worker's urlopen hit its 15 s socket timeout waiting for a response
# line that the server had not got round to sending. The same test passes
# on an 8-core box in ~15 s — the test was measuring the runner's CPU, not
# the behaviour it claims to test.
#
# A fast hasher removes that coupling. It weakens nothing here: these
# tests assert that failed attempts are counted exactly once, that the
# account locks, and that a correct password is refused while locked.
# None of that depends on how the hash is computed. Password hashing
# strength is a production setting, not a property of the lockout counter.
FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# A HANG DETECTOR, NOT A RACE BOUNDARY. With the hashing cost gone, these
# requests finish in a fraction of this; a wait anywhere near it means the
# server has genuinely stopped answering. Deliberately not tuned to "how
# long the test usually takes on the slowest runner we know of" — that is
# the kind of number that quietly becomes a flake again when the suite
# grows or the runner shrinks.
NO_RESPONSE_TIMEOUT_SECONDS = 120

# WHY THE COUNTING TESTS RAISE THE THRESHOLD
# ------------------------------------------
# A locked account is refused BEFORE its password is checked
# (users/serializers.py:414), so once the lock lands, further attempts are
# rejected without ever reaching register_failed_login - by design.
#
# That makes "N concurrent wrong passwords produce exactly N failures" true
# only while every request gets past the lock gate before the lock is
# written, which is to say: only while password hashing is slow enough to
# hold them all in flight. It is a property of the hasher, not of the
# counter, and it was the assertion that broke when the hashing cost was
# removed.
#
# So the two claims are proved separately, and neither depends on timing:
# a burst run with the budget raised ABOVE the worker count proves no
# increment is lost, and a burst run at the real budget proves the attack
# is stopped.
UNREACHABLE_LOGIN_BUDGET = 10_000

# The two rejections the login path actually produces, read from the code
# rather than retyped, so a wording change cannot make this test lie.
LOCKED_MESSAGE = CustomTokenObtainPairSerializer.LOCKED_MESSAGE
WRONG_PASSWORD_MESSAGE = str(
    CustomTokenObtainPairSerializer.default_error_messages["no_active_account"]
)


def make_user(**overrides):
    defaults = {
        "email": "lockout.login@example.com",
        "password": PASSWORD,
        "first_name": "Login",
        "last_name": "Lockout",
        "user_type": UserTypes.TEACHER,
        "is_active": True,
        "email_verified_at": timezone.now(),
    }
    defaults.update(overrides)
    return User.objects.create_user(**defaults)


@override_settings(CACHES=LOCMEM_CACHE)
class LoginLockoutTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.url = reverse("login")

    def tearDown(self):
        cache.clear()

    def login(self, password):
        return self.client.post(
            self.url, {"email": self.user.email, "password": password}
        )

    def test_wrong_passwords_below_threshold_do_not_lock(self):
        with tightened_rate("login", "1000/min"):
            for _ in range(User.MAX_LOGIN_ATTEMPTS - 1):
                response = self.login(WRONG_PASSWORD)
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, User.MAX_LOGIN_ATTEMPTS - 1)
        self.assertFalse(self.user.is_account_locked())

    def test_account_locks_after_max_attempts_and_blocks_correct_password(self):
        """
        The important one: once the attempt budget is spent, the *correct*
        password must stop working too - otherwise an attacker who guesses
        right on the last permitted try still gets in.
        """
        with tightened_rate("login", "1000/min"):
            for _ in range(User.MAX_LOGIN_ATTEMPTS):
                response = self.login(WRONG_PASSWORD)
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

            self.user.refresh_from_db()
            self.assertEqual(self.user.failed_login_attempts, User.MAX_LOGIN_ATTEMPTS)
            self.assertIsNotNone(self.user.locked_until)
            self.assertTrue(self.user.is_account_locked())

            response = self.login(PASSWORD)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            str(response.data["detail"]), CustomTokenObtainPairSerializer.LOCKED_MESSAGE
        )
        self.assertEqual(response.data["detail"].code, "account_locked")
        self.assertNotIn("access", response.data)

    def test_correct_password_within_budget_still_works_and_resets_counter(self):
        """Regression guard: a couple of typos must not break real login."""
        with tightened_rate("login", "1000/min"):
            for _ in range(2):
                self.login(WRONG_PASSWORD)

            response = self.login(PASSWORD)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn("access", response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)

    def test_lockout_clears_once_locked_until_has_passed(self):
        """
        Simulates having waited out LOGIN_LOCKOUT_DURATION: a correct
        password submitted after locked_until has elapsed must succeed and
        clear the counter, without needing a real sleep.
        """
        self.user.failed_login_attempts = User.MAX_LOGIN_ATTEMPTS
        self.user.locked_until = timezone.now() - timezone.timedelta(seconds=1)
        self.user.save(update_fields=["failed_login_attempts", "locked_until"])

        with tightened_rate("login", "1000/min"):
            response = self.login(PASSWORD)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)

    def test_unknown_email_does_not_error_or_touch_any_account(self):
        with tightened_rate("login", "1000/min"):
            response = self.client.post(
                self.url,
                {"email": "no-such-user@example.com", "password": WRONG_PASSWORD},
            )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)

    def test_correct_password_on_inactive_account_does_not_count_as_failure(self):
        """
        An unverified/inactive account rejecting a *correct* password is not
        a brute-force signal and must not consume the lockout budget -
        otherwise a confused legitimate user locks themselves out just by
        retrying before activating.
        """
        inactive_user = make_user(
            email="inactive.lockout@example.com",
            is_active=False,
            email_verified_at=None,
        )

        with tightened_rate("login", "1000/min"):
            response = self.client.post(
                self.url, {"email": inactive_user.email, "password": PASSWORD}
            )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        inactive_user.refresh_from_db()
        self.assertEqual(inactive_user.failed_login_attempts, 0)

    def test_wrong_password_on_inactive_account_still_counts(self):
        """An actual wrong-password guess must still count, active or not."""
        inactive_user = make_user(
            email="inactive.wrong.lockout@example.com",
            is_active=False,
            email_verified_at=None,
        )

        with tightened_rate("login", "1000/min"):
            response = self.client.post(
                self.url, {"email": inactive_user.email, "password": WRONG_PASSWORD}
            )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        inactive_user.refresh_from_db()
        self.assertEqual(inactive_user.failed_login_attempts, 1)

    def test_lockout_is_scoped_to_the_account_not_global(self):
        """A locked account must not block a different account from logging in."""
        other_user = make_user(email="other.lockout@example.com")

        with tightened_rate("login", "1000/min"):
            for _ in range(User.MAX_LOGIN_ATTEMPTS):
                self.login(WRONG_PASSWORD)

            self.user.refresh_from_db()
            self.assertTrue(self.user.is_account_locked())

            response = self.client.post(
                self.url, {"email": other_user.email, "password": PASSWORD}
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)


@override_settings(CACHES=LOCMEM_CACHE)
@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class LoginLockoutConcurrencyTests(TransactionTestCase):
    """
    Stress-tests register_failed_login against real concurrent traffic
    against the live login endpoint, to lock in that the F()-based counter
    increment does not lose updates under contention (the failure mode a
    naive `self.failed_login_attempts += 1; self.save()` read-modify-write
    would have under simultaneous requests).
    """

    WORKERS = 25

    def setUp(self):
        cache.clear()
        self.user = make_user(email="concurrency.lockout@example.com")
        self.url = reverse("login")

    def tearDown(self):
        cache.clear()

    def _post_login(self, password):
        # Each worker thread needs its own DB connection and its own DRF
        # test client; Django connections are not safe to share across
        # threads.
        from rest_framework.test import APIClient

        client = APIClient()
        try:
            response = client.post(
                self.url, {"email": self.user.email, "password": password}
            )
            return response.status_code
        finally:
            connection.close()

    def test_concurrent_wrong_passwords_do_not_lose_increments(self):
        """
        The F()-based UPDATE must not lose a single increment under
        contention (a naive read-modify-write would).

        The budget is raised beyond reach for this one test, so no attempt
        can be short-circuited by the lock and every one MUST be counted.
        Exact equality then proves the counter, not the hasher's speed.
        """
        attempts = self.WORKERS

        with patch.object(User, "MAX_LOGIN_ATTEMPTS", UNREACHABLE_LOGIN_BUDGET):
            with tightened_rate("login", "100000/min"):
                with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
                    futures = [
                        pool.submit(self._post_login, WRONG_PASSWORD)
                        for _ in range(attempts)
                    ]
                    statuses = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(statuses), attempts)
        self.assertTrue(all(code == status.HTTP_401_UNAUTHORIZED for code in statuses))

        self.user.refresh_from_db()
        self.assertEqual(
            self.user.failed_login_attempts,
            attempts,
            "an increment was lost (or double-counted) under concurrency",
        )
        # Also the proof that the patched threshold really reached the
        # worker threads: at the real budget of 5, this account would be
        # locked long before the 25th attempt.
        self.assertFalse(
            self.user.is_account_locked(),
            "the raised budget was not in force inside the worker threads, so "
            "the exact count above proves nothing about lost increments",
        )

    def test_a_concurrent_burst_locks_the_account_at_the_real_budget(self):
        """
        The security property, asserted the way it survives any speed:
        the burst is refused and the account ends up locked. HOW MANY
        guesses land before the lock is timing-dependent by design (a
        locked account is refused before its password is checked), so it
        is bounded, not pinned.
        """
        with tightened_rate("login", "100000/min"):
            with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
                futures = [
                    pool.submit(self._post_login, WRONG_PASSWORD)
                    for _ in range(self.WORKERS)
                ]
                statuses = [f.result() for f in as_completed(futures)]

        self.assertTrue(all(code == status.HTTP_401_UNAUTHORIZED for code in statuses))

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_account_locked())
        self.assertGreaterEqual(
            self.user.failed_login_attempts, User.MAX_LOGIN_ATTEMPTS
        )
        self.assertLessEqual(
            self.user.failed_login_attempts,
            self.WORKERS,
            "more failures were counted than attempts were made",
        )

    def test_concurrent_correct_password_attempts_never_succeed_once_locked(self):
        self.user.failed_login_attempts = self.user.MAX_LOGIN_ATTEMPTS
        self.user.locked_until = timezone.now() + timezone.timedelta(minutes=15)
        self.user.save(update_fields=["failed_login_attempts", "locked_until"])

        with tightened_rate("login", "100000/min"):
            with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
                futures = [
                    pool.submit(self._post_login, PASSWORD) for _ in range(self.WORKERS)
                ]
                statuses = [f.result() for f in as_completed(futures)]

        self.assertTrue(all(code == status.HTTP_401_UNAUTHORIZED for code in statuses))

    def test_mixed_concurrent_traffic_one_account_locks_another_is_unaffected(self):
        """
        Simulates the realistic 'most strenuous' shape: many concurrent
        wrong-password guesses against a victim account interleaved with
        legitimate traffic on an unrelated account, run from many threads
        at once. The victim must end up locked; the bystander account must
        be entirely unaffected. The victim's count is bounded rather than
        pinned, for the reason given at UNREACHABLE_LOGIN_BUDGET.
        """
        bystander = make_user(email="bystander.lockout@example.com")
        victim_attempts = self.WORKERS
        bystander_logins = 10

        jobs = [(self._post_login, WRONG_PASSWORD)] * victim_attempts

        def bystander_login():
            from rest_framework.test import APIClient

            client = APIClient()
            try:
                response = client.post(
                    self.url, {"email": bystander.email, "password": PASSWORD}
                )
                return response.status_code
            finally:
                connection.close()

        with tightened_rate("login", "100000/min"):
            with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
                victim_futures = [pool.submit(fn, arg) for fn, arg in jobs]
                bystander_futures = [
                    pool.submit(bystander_login) for _ in range(bystander_logins)
                ]
                victim_statuses = [f.result() for f in victim_futures]
                bystander_statuses = [f.result() for f in bystander_futures]

        # Assert what every request actually got back, not only the account
        # state afterwards. The state alone cannot tell success from failure:
        # a bystander login that errors (a 500 when Postgres is out of
        # connections, say) also leaves 0 failures and no lock, and victim
        # requests that error simply go uncounted, which the bounded victim
        # count below would accept. Both went green under a starved run
        # (H-31, docs/evidence/h31_users_false_pass/).
        self.assertEqual(
            victim_statuses,
            [status.HTTP_401_UNAUTHORIZED] * victim_attempts,
            "every wrong-password attempt must be rejected with 401",
        )
        self.assertEqual(
            bystander_statuses,
            [status.HTTP_200_OK] * bystander_logins,
            "every bystander login must succeed",
        )

        self.user.refresh_from_db()
        bystander.refresh_from_db()

        self.assertTrue(self.user.is_account_locked())
        self.assertGreaterEqual(
            self.user.failed_login_attempts, User.MAX_LOGIN_ATTEMPTS
        )
        self.assertLessEqual(
            self.user.failed_login_attempts,
            victim_attempts,
            "more failures were counted than the victim received",
        )
        self.assertEqual(bystander.failed_login_attempts, 0)
        self.assertIsNone(bystander.locked_until)


@override_settings(CACHES=LOCMEM_CACHE, PASSWORD_HASHERS=FAST_HASHERS)
class LoginLockoutLiveServerTests(LiveServerTestCase):
    """
    The genuine article: real HTTP requests (via urllib, no Django test
    client involved) against `self.live_server_url`, a real `runserver`
    process Django spins up for this test class and binds to the isolated
    test database - not the app's test client, which never touches a
    socket. This is the closest thing to a real brute-force attack this
    suite can exercise without leaving the isolated test DB.
    """

    WORKERS = 30

    def setUp(self):
        cache.clear()
        self.user = make_user(email="live.lockout@example.com")

    def tearDown(self):
        cache.clear()

    def _post(self, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.live_server_url + reverse("login"),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(
                req, timeout=NO_RESPONSE_TIMEOUT_SECONDS
            ) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
        except TimeoutError as exc:
            # Re-raised with context rather than swallowed: a timeout here
            # is a real failure (the server stopped answering), and the
            # bare "TimeoutError: timed out" that CI reported said nothing
            # about which request, how long, or under what load.
            raise AssertionError(
                f"no response to a login POST after "
                f"{time.monotonic() - started:.1f}s "
                f"(limit {NO_RESPONSE_TIMEOUT_SECONDS}s) with "
                f"{self.WORKERS} concurrent requests in flight: the live "
                f"server stopped responding."
            ) from exc

    def test_concurrent_brute_force_against_live_http_server_is_held(self):
        with tightened_rate("login", "100000/min"):
            with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
                futures = [
                    pool.submit(
                        self._post,
                        {"email": self.user.email, "password": WRONG_PASSWORD},
                    )
                    for _ in range(self.WORKERS)
                ]
                results = [f.result() for f in as_completed(futures)]

            statuses = [code for code, _ in results]
            self.assertTrue(all(code == 401 for code in statuses), statuses)

            # Every rejection must be one of the two the system actually
            # gives: a wrong-password refusal, or the lockout refusal once
            # the budget is spent. Which mix arrives depends on how many
            # requests clear the lock gate first, so the mix is reported,
            # not pinned.
            # The API wraps errors as {"success": false, "message": ...}
            # (users/renderers.py APIJSONRenderer), so the reason is in
            # "message", not DRF's raw "detail".
            details = [str(body.get("message", "")) for _, body in results]
            locked = [d for d in details if LOCKED_MESSAGE in d]
            refused = [d for d in details if WRONG_PASSWORD_MESSAGE in d]
            self.assertEqual(
                len(locked) + len(refused),
                self.WORKERS,
                f"unexpected rejection reason(s): {set(details)}",
            )

            self.user.refresh_from_db()
            self.assertTrue(self.user.is_account_locked())
            self.assertGreaterEqual(
                self.user.failed_login_attempts, User.MAX_LOGIN_ATTEMPTS
            )
            self.assertLessEqual(
                self.user.failed_login_attempts,
                self.WORKERS,
                "more failures were counted than requests were sent",
            )

            # The correct password, over real HTTP, against the now-locked
            # account: must be rejected and must not hand out a token.
            status_code, body = self._post(
                {"email": self.user.email, "password": PASSWORD}
            )

        self.assertEqual(status_code, 401)
        self.assertNotIn("access", body)
