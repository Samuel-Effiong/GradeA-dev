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

from users.middleware import ACTIVE_WINDOW_SECONDS, UserActivityMiddleware
from users.models import CustomUser, UserActivity, UserTypes

# Pinned to LocMem, like every other suite in this app. On the shared real
# Redis this was order-dependent: users.signals.clear_user_cache fires
# `delete_pattern("*user*")` on every CustomUser save, and the heartbeat key
# (`active_user:<type>:<id>`) matches that glob - so an unrelated suite
# creating a user could wipe the heartbeat mid-test and let a second
# activity row through. Real-Redis behaviour is covered deliberately, with
# its own key prefix, in tests_activity_middleware_load.py.
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
        cache.delete(f"active_user:{self.user.user_type}:{self.user.id}")
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
