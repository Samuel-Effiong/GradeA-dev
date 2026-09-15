"""
UserActivityMiddleware under real concurrency, against REAL Redis.

users/tests_activity_middleware.py exercises the logic with LocMem and
mocks. That cannot prove the property that actually matters in production:
the heartbeat throttle has to hold when many requests for the SAME user
arrive at the same instant, which is precisely when a check-then-act
sequence fails.

The original implementation read the heartbeat and then wrote it. Under a
burst every request read "absent" before any of them wrote, so all of them
proceeded and the throttle wrote a row per request - it degraded to no
throttle exactly under the load it exists to absorb. It is now an atomic
`cache.add` (SET NX). These tests are what distinguishes the two: with the
old code they fail, with the atomic claim they pass.

LocMem is not good enough here, because each Django process gets its own
LocMem instance and its operations are not the ones production uses. These
tests therefore run against the real Redis this project is configured with,
under a key prefix unique to the run so a concurrent session (or a real
local dev server) cannot be disturbed and cannot disturb them.
"""

import os
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.db import connection
from django.http import HttpResponse
from django.test import (
    LiveServerTestCase,
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from rest_framework_simplejwt.tokens import RefreshToken

from users.middleware import ONLINE_SET_KEY, UserActivityMiddleware, heartbeat_key_for
from users.models import Settings, UserActivity, UserTypes

User = get_user_model()

REDIS_URL = getattr(settings, "CACHES", {}).get("default", {}).get("LOCATION")

# A prefix nothing else in this repo (or another session's test run) uses,
# so these tests can hammer the real Redis without touching anyone's keys.
# The prefix-scoped backend matters as much as the prefix: the plain
# backend's clear() is FLUSHDB, which ignores any prefix (H-9).
RUN_PREFIX = f"acttest-{uuid.uuid4().hex[:8]}"

REAL_REDIS_CACHE = {
    "default": {
        "BACKEND": "AutoGrader.test_cache.PrefixScopedRedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": RUN_PREFIX,
    }
}

SKIP_REASON = "Real Redis not reachable; this suite is specifically about Redis."


def redis_available():
    if not REDIS_URL or not str(REDIS_URL).startswith("redis"):
        return False
    try:
        with override_settings(CACHES=REAL_REDIS_CACHE):
            probe = caches["default"]
            probe.set("ping", "pong", 5)
            return probe.get("ping") == "pong"
    except Exception:
        return False


REDIS_OK = redis_available()


def make_user(email, user_type=UserTypes.TEACHER):
    return User.objects.create_user(
        email=email,
        password="load-test-password-42",  # pragma: allowlist secret
        first_name="Load",
        last_name="Tester",
        user_type=user_type,
        is_active=True,
    )


@override_settings(CACHES=REAL_REDIS_CACHE)
class HeartbeatThrottleConcurrencyTests(TransactionTestCase):
    """
    TransactionTestCase, not TestCase: worker threads use their own DB
    connections, and an outer atomic block would hide their writes from
    each other - the race would look "safe" no matter how badly it raced.
    """

    WORKERS = 40

    def setUp(self):
        if not REDIS_OK:
            self.skipTest(SKIP_REASON)
        cache.clear()
        self.addCleanup(cache.clear)
        self.factory = RequestFactory()
        self.user = make_user("load.activity@gmail.com")
        UserActivity.objects.all().delete()

    def _hit(self, user):
        """One trip through the middleware, on this thread's own connection."""

        def get_response(request):
            return HttpResponse("ok")

        request = self.factory.get("/")
        request.user = user
        try:
            return UserActivityMiddleware(get_response)(request).status_code
        finally:
            connection.close()

    def test_a_simultaneous_burst_writes_exactly_one_row(self):
        """
        The core property. With the old get-then-set this produced up to
        WORKERS rows; the atomic claim admits exactly one winner.
        """
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            futures = [pool.submit(self._hit, self.user) for _ in range(self.WORKERS)]
            statuses = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(statuses), self.WORKERS)
        self.assertTrue(all(code == 200 for code in statuses))
        self.assertEqual(
            UserActivity.objects.filter(user=self.user).count(),
            1,
            "the heartbeat throttle lost the race - every concurrent request "
            "wrote its own activity row",
        )

    def test_a_burst_never_fails_a_single_request(self):
        """Bookkeeping must never turn into a 500, least of all under load."""
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            futures = [pool.submit(self._hit, self.user) for _ in range(self.WORKERS)]
            statuses = [f.result() for f in as_completed(futures)]

        self.assertTrue(all(code == 200 for code in statuses), statuses)

    def test_concurrent_bursts_for_different_users_do_not_collide(self):
        """
        The claim is per-user. One busy user must not suppress another
        user's heartbeat, and each must still get exactly one row.
        """
        users = [make_user(f"load.multi{i}@gmail.com") for i in range(5)]
        jobs = [u for u in users for _ in range(10)]

        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            futures = [pool.submit(self._hit, u) for u in jobs]
            statuses = [f.result() for f in as_completed(futures)]

        self.assertTrue(all(code == 200 for code in statuses))
        for user in users:
            self.assertEqual(
                UserActivity.objects.filter(user=user).count(),
                1,
                f"{user.email} did not get exactly one row",
            )

    def test_a_second_burst_after_the_window_expires_writes_one_more(self):
        """
        Expiry still works after the switch to an atomic claim: drop the
        key (as a TTL would) and the next burst elects exactly one winner
        again - not zero (throttle stuck on) and not many (race back).
        """
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            [
                f.result()
                for f in [
                    pool.submit(self._hit, self.user) for _ in range(self.WORKERS)
                ]
            ]

        self.assertEqual(UserActivity.objects.filter(user=self.user).count(), 1)

        cache.delete(heartbeat_key_for(self.user.user_type, self.user.id))

        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            [
                f.result()
                for f in [
                    pool.submit(self._hit, self.user) for _ in range(self.WORKERS)
                ]
            ]

        self.assertEqual(UserActivity.objects.filter(user=self.user).count(), 2)

    def test_the_online_set_records_the_user_once(self):
        """The presence index that feeds the concurrent-users dashboard."""
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            [
                f.result()
                for f in [
                    pool.submit(self._hit, self.user) for _ in range(self.WORKERS)
                ]
            ]

        members = cache.smembers(ONLINE_SET_KEY)
        decoded = {m.decode() if isinstance(m, bytes) else m for m in members}
        self.assertIn(f"{self.user.user_type}:{self.user.id}", decoded)


@override_settings(CACHES=REAL_REDIS_CACHE)
class HeartbeatThrottleLiveServerTests(LiveServerTestCase):
    """
    The genuine article: real HTTP requests over a socket, through the full
    middleware stack, against a real server process - no Django test client
    involved. This is the closest this suite gets to production traffic.
    """

    WORKERS = 30

    def setUp(self):
        if not REDIS_OK:
            self.skipTest(SKIP_REASON)
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("live.activity@gmail.com")
        self.token = str(RefreshToken.for_user(self.user).access_token)
        UserActivity.objects.all().delete()

    def _get_me(self):
        request = urllib.request.Request(
            self.live_server_url + reverse("user-me"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code
        finally:
            connection.close()

    def test_real_http_burst_writes_exactly_one_activity_row(self):
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            futures = [pool.submit(self._get_me) for _ in range(self.WORKERS)]
            statuses = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(statuses), self.WORKERS)
        self.assertTrue(
            all(code == 200 for code in statuses),
            f"authenticated requests did not all succeed: {statuses}",
        )
        self.assertEqual(
            UserActivity.objects.filter(user=self.user).count(),
            1,
            "over real HTTP the throttle admitted more than one writer",
        )


class RedisAvailabilityTests(TransactionTestCase):
    """
    Guard against this whole suite silently skipping forever. If Redis is
    reachable the load tests MUST have run; if it is not, that has to be a
    deliberate local condition, not an unnoticed permanent skip in CI.
    """

    def test_redis_probe_result_is_reported(self):
        if REDIS_OK:
            self.assertTrue(REDIS_OK)
        else:
            self.skipTest(
                "Redis unreachable at "
                f"{REDIS_URL!r} (CI_REQUIRE_REDIS={os.environ.get('CI_REQUIRE_REDIS')})"
            )

    def test_redis_is_required_when_ci_says_so(self):
        """
        Set CI_REQUIRE_REDIS=1 in any environment where these load tests
        must not be allowed to skip.
        """
        if os.environ.get("CI_REQUIRE_REDIS") == "1":
            self.assertTrue(
                REDIS_OK,
                "CI_REQUIRE_REDIS=1 but Redis was unreachable, so the "
                "middleware load tests silently skipped.",
            )
        else:
            self.skipTest("CI_REQUIRE_REDIS not set")


class UnrelatedUserSaveDoesNotClearPresenceTests(TestCase):
    """
    The production defect behind this suite's long-standing flakiness,
    pinned against real Redis.

    users.signals.clear_user_cache runs delete_pattern("*user*") on every
    CustomUser and Settings save. The heartbeat throttle key and the
    concurrent-users presence set both used to contain "user", so ANY
    unrelated user save - registration, a profile edit, a settings change,
    or the create_default_settings_and_wallet signal chain - wiped both:

      * releasing the SET NX throttle window early, so the next request
        wrote another UserActivity row and ran another
        CreditWallet.get_or_create, which is exactly the per-request
        double DB round trip the throttle exists to prevent; and
      * silently resetting the concurrent-users figure to zero, which
        reads as a traffic dip rather than as a bug.

    Measured before the fix: both keys gone, every time.
    """

    def setUp(self):
        if not REDIS_OK:
            self.skipTest(SKIP_REASON)
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("presence.holder@gmail.com")

    def _claim_window(self):
        key = heartbeat_key_for(self.user.user_type, self.user.id)
        self.assertTrue(cache.add(key, 1, 300), "could not claim the window")
        cache.sadd(ONLINE_SET_KEY, f"{self.user.user_type}:{self.user.id}")
        return key

    def test_an_unrelated_settings_save_leaves_the_throttle_window_held(self):
        key = self._claim_window()

        other = make_user("presence.unrelated@gmail.com")
        settings_row = Settings.objects.filter(user=other).first()
        self.assertIsNotNone(settings_row, "fixture assumption: a Settings row exists")
        settings_row.save()

        self.assertIsNotNone(
            cache.get(key), "an unrelated Settings save released the throttle window"
        )
        # And the window still refuses a second writer, which is the
        # property that actually matters.
        self.assertFalse(
            cache.add(key, 2, 300), "the window is no longer held exclusively"
        )

    def test_an_unrelated_user_save_leaves_the_presence_set_intact(self):
        self._claim_window()
        self.assertEqual(cache.scard(ONLINE_SET_KEY), 1)

        other = make_user("presence.unrelated2@gmail.com")
        other.first_name = "Renamed"
        other.save()

        self.assertEqual(
            cache.scard(ONLINE_SET_KEY),
            1,
            "an unrelated user save wiped the concurrent-users presence set",
        )

    def test_the_per_user_json_cache_is_still_swept(self):
        """
        The other half: moving these keys out of the way must not stop
        clear_user_cache doing its actual job.
        """
        cache.set("users:user_id__1:query__abc", {"stale": True}, 300)
        self._claim_window()

        other = make_user("presence.unrelated3@gmail.com")
        other.first_name = "Renamed"
        other.save()

        self.assertIsNone(
            cache.get("users:user_id__1:query__abc"),
            "clear_user_cache stopped clearing the payloads it exists for",
        )


__all__ = [
    "UnrelatedUserSaveDoesNotClearPresenceTests",
    "HeartbeatThrottleConcurrencyTests",
    "HeartbeatThrottleLiveServerTests",
    "RedisAvailabilityTests",
]
