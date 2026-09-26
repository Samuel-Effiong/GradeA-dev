"""
UserActivityMiddleware: throttling, and never failing the request.

This middleware runs on every authenticated request and had no test file at
all. Two properties matter and neither was pinned down:

  * It is THROTTLED. It used to write a UserActivity row and run a
    CreditWallet.get_or_create() on every single authenticated request -
    two DB round trips per request per user, and unbounded table growth.
  * It NEVER fails the request. Its work is bookkeeping that happens after
    the view has already produced a response; a Redis or DB problem here
    must cost an activity row, not the user's request.
"""

from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from users.middleware import (
    ACTIVE_WINDOW_SECONDS,
    ONLINE_SET_KEY,
    UserActivityMiddleware,
    heartbeat_key_for,
)
from users.models import CustomUser, UserActivity, UserTypes

# Pinned to LocMem, like every other suite in this app - now for isolation
# alone rather than to dodge a defect.
#
# Historical note, because it explains the class below: this used to be
# order-dependent on the shared real Redis. users.signals.clear_user_cache
# fires `delete_pattern("*user*")` on every CustomUser and Settings save,
# and the heartbeat key was called `active_user:<type>:<id>` - which that
# glob matches. An unrelated suite creating a user wiped the heartbeat
# mid-test and let a second activity row through.
#
# That was never only a test problem. In production the same collision
# released the throttle window early on every unrelated user save, and
# wiped the concurrent-users presence set with it. The keys are now named
# outside the swept namespace (users/middleware.py), and
# HeartbeatKeyNamespaceTests below keeps them there.
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM_CACHE)
class UserActivityMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = CustomUser.objects.create_user(
            email="activity@gmail.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Active",
            last_name="Teacher",
        )
        cache.clear()
        self.addCleanup(cache.clear)
        UserActivity.objects.filter(user=self.user).delete()

    def _run(self, user=None):
        def get_response(request):
            return HttpResponse("ok")

        request = self.factory.get("/")
        request.user = user if user is not None else self.user
        return UserActivityMiddleware(get_response)(request)

    def _activity_count(self):
        return UserActivity.objects.filter(user=self.user).count()

    def test_records_activity_on_a_first_request(self):
        response = self._run()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._activity_count(), 1)

    def test_repeat_requests_inside_the_window_write_only_one_row(self):
        """The whole point of the throttle: 10 requests, 1 row."""
        for _ in range(10):
            self._run()

        self.assertEqual(self._activity_count(), 1)

    def test_a_new_row_is_written_once_the_heartbeat_expires(self):
        self._run()
        self.assertEqual(self._activity_count(), 1)

        # Simulate the heartbeat key ageing out of the cache.
        cache.delete(heartbeat_key_for(self.user.user_type, self.user.id))
        self._run()

        self.assertEqual(self._activity_count(), 2)

    def test_the_window_is_claimed_atomically_for_the_active_window(self):
        """
        `add`, not get-then-set: the claim has to be atomic or simultaneous
        requests all pass the check together. See the middleware comment.
        """
        with patch("users.middleware.cache") as mock_cache:
            mock_cache.add.return_value = True
            self._run()

        args, _ = mock_cache.add.call_args
        self.assertEqual(args[2], ACTIVE_WINDOW_SECONDS)
        mock_cache.set.assert_not_called()

    def test_a_lost_claim_skips_the_write_entirely(self):
        with patch("users.middleware.cache") as mock_cache:
            mock_cache.add.return_value = False

            response = self._run()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._activity_count(), 0)

    def test_anonymous_requests_record_nothing(self):
        response = self._run(user=AnonymousUser())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserActivity.objects.count(), 0)

    # ---------- must never break the request ----------

    def test_a_failing_cache_read_does_not_break_the_request(self):
        """
        Regression: the throttle's cache.get() was briefly placed OUTSIDE
        this middleware's try/except, so a Redis outage turned every
        authenticated request into a 500 - the exact failure the try/except
        was written to prevent.
        """
        with patch("users.middleware.cache") as mock_cache:
            mock_cache.add.side_effect = ConnectionError("redis down")

            with self.assertLogs("users.middleware", level="WARNING"):
                response = self._run()

        self.assertEqual(response.status_code, 200)

    def test_a_failing_online_set_write_does_not_break_the_request(self):
        with patch("users.middleware.cache") as mock_cache:
            mock_cache.add.return_value = True
            mock_cache.sadd.side_effect = ConnectionError("redis down")

            with self.assertLogs("users.middleware", level="WARNING"):
                response = self._run()

        self.assertEqual(response.status_code, 200)

    def test_a_failing_activity_write_does_not_break_the_request(self):
        with patch(
            "users.middleware.UserActivity.objects.create",
            side_effect=Exception("user was deleted in the view"),
        ):
            with self.assertLogs("users.middleware", level="WARNING"):
                response = self._run()

        self.assertEqual(response.status_code, 200)

    def test_failures_are_logged_rather_than_silently_swallowed(self):
        """
        The except block used to be a bare `pass`, so a permanently broken
        heartbeat was invisible until someone noticed the concurrent-users
        dashboard was wrong.
        """
        with patch("users.middleware.cache") as mock_cache:
            mock_cache.add.side_effect = ConnectionError("redis down")

            with self.assertLogs("users.middleware", level="WARNING") as logs:
                self._run()

        self.assertIn(str(self.user.id), logs.output[0])


@override_settings(CACHES=LOCMEM_CACHE)
class HeartbeatKeyNamespaceTests(TestCase):
    """
    The heartbeat and presence keys must not be collateral damage of
    anybody else's cache invalidation.

    This is a pure string check on purpose: it needs no Redis, so it runs
    everywhere and fails immediately if someone renames a key back into a
    swept namespace - which is precisely how the original defect arrived.
    """

    # Every delete_pattern glob used anywhere in the project, gathered from
    # users/signals.py, classrooms/signals.py, assignments/signals.py,
    # students/signals.py and students/views.py.
    SWEPT_SUBSTRINGS = [
        "superadmin",
        "schooladmin",
        "teacheradmin",
        "studentadmin",
        "user",
        "school",
        "course",
        "studentcourse",
        "settings",
        "assignmentgenerationsession",
    ]
    SWEPT_PREFIXES = [
        "courses:",
        "sessions:",
        "assignments:",
        "studentsubmissions:",
        "studentcourses:",
        "topics:",
        "schools:",
    ]

    def _assert_unswept(self, key):
        for fragment in self.SWEPT_SUBSTRINGS:
            self.assertNotIn(
                fragment,
                key,
                f"{key!r} contains {fragment!r} and would be destroyed by "
                f'delete_pattern("*{fragment}*")',
            )
        for prefix in self.SWEPT_PREFIXES:
            self.assertFalse(
                key.startswith(prefix),
                f"{key!r} would be destroyed by delete_pattern('{prefix}*')",
            )

    def test_the_heartbeat_key_is_outside_every_sweep(self):
        for user_type in ("TEACHER", "STUDENT", "SCHOOL_ADMIN", "SUPER_ADMIN"):
            with self.subTest(user_type=user_type):
                self._assert_unswept(
                    heartbeat_key_for(user_type, "0e1f2a3b-4c5d-6e7f-8a9b-0c1d2e3f4a5b")
                )

    def test_the_presence_set_key_is_outside_every_sweep(self):
        self._assert_unswept(ONLINE_SET_KEY)

    def test_the_old_names_would_have_failed_this_check(self):
        """
        Proves the guard above is actually load-bearing rather than
        trivially true, by running it against the names that shipped.
        """
        for old_key in ("active_user:TEACHER:abc", "online_users_set"):
            with self.subTest(old_key=old_key):
                with self.assertRaises(AssertionError):
                    self._assert_unswept(old_key)
