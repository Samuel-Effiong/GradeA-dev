"""
users/services.py - the online-presence helpers and reporting aggregates.

This module sat at ~39% coverage. The Redis-set reconciliation
(`cleanup_expired_users`) and the concurrent-user reporting helpers had no
tests at all, despite feeding the admin dashboard's headline numbers and a
scheduled Beat job.

The presence helpers use django-redis set operations (`smembers`, `srem`,
`has_key`) that LocMem does not implement, so the cache is mocked here
rather than swapped for a different backend - the point is the
reconciliation logic, not Redis itself.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from classrooms.models import School
from users.models import ConcurrentUserSnapshot, Settings, UserTypes
from users.services import (
    cleanup_expired_users,
    get_current_concurrent_users,
    get_opted_in_school_admins,
    get_peak_concurrent_users,
    get_peak_time_of_day,
    get_time_range,
)

User = get_user_model()


class CleanupExpiredUsersTests(TestCase):
    """Drops members from the online set once their heartbeat key has aged out."""

    def test_returns_zero_and_does_nothing_on_an_empty_set(self):
        with patch("users.services.cache") as mock_cache:
            mock_cache.smembers.return_value = set()

            self.assertEqual(cleanup_expired_users(), 0)

        mock_cache.srem.assert_not_called()

    def test_members_with_a_live_heartbeat_are_kept(self):
        with patch("users.services.cache") as mock_cache:
            mock_cache.smembers.return_value = {"TEACHER:1", "TEACHER:2"}
            mock_cache.has_key.return_value = True

            self.assertEqual(cleanup_expired_users(), 0)

        mock_cache.srem.assert_not_called()

    def test_members_whose_heartbeat_expired_are_removed(self):
        with patch("users.services.cache") as mock_cache:
            mock_cache.smembers.return_value = {"TEACHER:stale"}
            mock_cache.has_key.return_value = False

            self.assertEqual(cleanup_expired_users(), 1)

        mock_cache.srem.assert_called_once_with("online_users_set", "TEACHER:stale")

    def test_only_the_expired_members_are_removed(self):
        def has_key(key):
            return key == "active_user:TEACHER:live"

        with patch("users.services.cache") as mock_cache:
            mock_cache.smembers.return_value = {"TEACHER:live", "TEACHER:stale"}
            mock_cache.has_key.side_effect = has_key

            self.assertEqual(cleanup_expired_users(), 1)

        mock_cache.srem.assert_called_once_with("online_users_set", "TEACHER:stale")

    def test_bytes_members_are_decoded_before_use(self):
        """redis-py can hand back bytes; a b'...' key would never match."""
        with patch("users.services.cache") as mock_cache:
            mock_cache.smembers.return_value = {b"TEACHER:bytes"}
            mock_cache.has_key.return_value = False

            self.assertEqual(cleanup_expired_users(), 1)

        mock_cache.has_key.assert_called_once_with("active_user:TEACHER:bytes")
        mock_cache.srem.assert_called_once_with("online_users_set", "TEACHER:bytes")


class CurrentConcurrentUsersTests(TestCase):
    def test_counts_the_members_of_the_online_set(self):
        with patch("users.services.cache") as mock_cache:
            mock_cache.smembers.return_value = {"TEACHER:1", "STUDENT:2", "TEACHER:3"}

            self.assertEqual(get_current_concurrent_users(), 3)

    def test_an_empty_set_is_zero_not_none(self):
        with patch("users.services.cache") as mock_cache:
            mock_cache.smembers.return_value = set()

            self.assertEqual(get_current_concurrent_users(), 0)

    def test_a_missing_set_is_zero(self):
        with patch("users.services.cache") as mock_cache:
            mock_cache.smembers.return_value = None

            self.assertEqual(get_current_concurrent_users(), 0)


class ConcurrentUserReportingTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def _snapshot(self, count, when):
        snapshot = ConcurrentUserSnapshot.objects.create(concurrent_users=count)
        ConcurrentUserSnapshot.objects.filter(pk=snapshot.pk).update(timestamp=when)
        return snapshot

    def test_peak_is_the_highest_recorded_value(self):
        self._snapshot(5, self.now - timedelta(hours=1))
        self._snapshot(42, self.now - timedelta(hours=2))
        self._snapshot(7, self.now - timedelta(hours=3))

        self.assertEqual(get_peak_concurrent_users(), 42)

    def test_peak_is_zero_when_nothing_has_been_recorded(self):
        self.assertEqual(get_peak_concurrent_users(), 0)

    def test_peak_respects_the_time_window(self):
        self._snapshot(99, self.now - timedelta(days=10))
        self._snapshot(11, self.now - timedelta(hours=1))

        recent = get_peak_concurrent_users(start=self.now - timedelta(days=1))

        self.assertEqual(recent, 11)

    def test_peak_time_of_day_reports_the_busiest_hour_with_a_label(self):
        busy = self.now.replace(hour=14, minute=0, second=0, microsecond=0)
        quiet = self.now.replace(hour=3, minute=0, second=0, microsecond=0)
        self._snapshot(100, busy)
        self._snapshot(1, quiet)

        result = get_peak_time_of_day()

        self.assertEqual(result["hour"], 14)
        self.assertEqual(result["label"], "14:00 - 15:00")
        self.assertEqual(result["average_users"], 100)

    def test_peak_time_of_day_averages_within_the_hour(self):
        hour = self.now.replace(hour=9, minute=0, second=0, microsecond=0)
        self._snapshot(10, hour)
        self._snapshot(20, hour + timedelta(minutes=30))

        result = get_peak_time_of_day()

        self.assertEqual(result["hour"], 9)
        self.assertEqual(result["average_users"], 15)

    def test_peak_time_of_day_with_no_data_says_so(self):
        result = get_peak_time_of_day()

        self.assertIsNone(result["hour"])
        self.assertEqual(result["label"], "No data available")
        self.assertEqual(result["average_users"], 0)


class TimeRangeTests(TestCase):
    def test_known_ranges_span_the_expected_number_of_days(self):
        for key, days in (("daily", 1), ("weekly", 7), ("monthly", 30)):
            with self.subTest(key=key):
                start, end = get_time_range(key)
                self.assertAlmostEqual(
                    (end - start).total_seconds(),
                    timedelta(days=days).total_seconds(),
                    delta=5,
                )

    def test_an_unknown_range_is_rejected(self):
        with self.assertRaises(ValueError):
            get_time_range("fortnightly")


class OptedInSchoolAdminTests(TestCase):
    """Tenant-scoped queryset: it must return one school's admins only."""

    def setUp(self):
        self.school = School.objects.create(name="Opted In School")
        self.other_school = School.objects.create(name="Other School")

        self.admin = self._admin("optin.admin@school.test", self.school, True)
        self.opted_out = self._admin("optout.admin@school.test", self.school, False)
        self.other_admin = self._admin(
            "other.admin@school.test", self.other_school, True
        )

    def _admin(self, email, school, opted_in, **overrides):
        defaults = {
            "email": email,
            "password": "password123",  # pragma: allowlist secret
            "first_name": "Opt",
            "last_name": "Admin",
            "user_type": UserTypes.SCHOOL_ADMIN,
            "school": school,
            "is_active": True,
        }
        defaults.update(overrides)
        user = User.objects.create_user(**defaults)
        Settings.objects.filter(user=user).update(notify_weekly_summary=opted_in)
        return user

    def _result(self, school):
        return list(get_opted_in_school_admins(school, flag="notify_weekly_summary"))

    def test_returns_only_opted_in_admins_for_that_school(self):
        result = self._result(self.school)

        self.assertIn(self.admin, result)
        self.assertNotIn(self.opted_out, result)

    def test_does_not_leak_admins_from_another_school(self):
        result = self._result(self.school)

        self.assertNotIn(self.other_admin, result)

    def test_inactive_admins_are_excluded(self):
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])

        self.assertNotIn(self.admin, self._result(self.school))

    def test_non_admin_roles_are_excluded(self):
        teacher = self._admin(
            "teacher.optin@school.test",
            self.school,
            True,
            user_type=UserTypes.TEACHER,
        )

        self.assertNotIn(teacher, self._result(self.school))

    def test_no_school_returns_an_empty_queryset_rather_than_everyone(self):
        """A None school must not fall through to an unscoped list."""
        self.assertEqual(
            list(get_opted_in_school_admins(None, flag="notify_weekly_summary")), []
        )
