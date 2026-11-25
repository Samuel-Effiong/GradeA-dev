"""Step 1 (owner go, 2026-11-25): the super admin's analytics show AI credits,
the unit the wallet shows (raw / CONVERSION_FACTOR), not raw units.

One helper, billing.credit_units.display_credits, rounds to the nearest
(half up): whole credits for totals and per-user figures, 2 decimals for the
per-action averages (avg_tokens_*). Ratios (percentages, the 80% and cap
flags) stay raw over raw. Field names and URLs are unchanged.

    python manage.py test billing.tests.test_analytics_credit_units
"""

import importlib
from datetime import timedelta

from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from billing.tests.test_r2_beta_tallies import (
    CREATION,
    GRADING,
    TallyFixture,
    set_profile,
)
from users.models import CustomUser, UserTypes

PW = "Analytics-credits-pass-1"  # pragma: allowlist secret


def units():
    return importlib.import_module("billing.credit_units")


class DisplayCreditsTests(SimpleTestCase):
    def test_whole_credits_round_to_the_nearest(self):
        display = units().display_credits
        self.assertEqual(display(34_000_000), 34_000)
        self.assertEqual(display(1_499), 1)
        self.assertEqual(display(2_500), 3)  # half up, not to even
        self.assertEqual(display(0), 0)
        self.assertEqual(display(None), 0)
        self.assertIsInstance(display(1_499), int)

    def test_two_decimals_for_per_action_averages(self):
        display = units().display_credits
        self.assertEqual(display(9_150, 2), 9.15)
        self.assertEqual(display(2_345, 2), 2.35)  # half up
        self.assertEqual(display(10_000.0, 2), 10.0)


class AdminFixture(TallyFixture, APITestCase):
    """A signed-in super admin and a GET helper. No tests."""

    def sign_in_admin(self):
        cache.clear()
        self.admin = CustomUser.objects.create_user(
            email="credits.admin@x.example",
            password=PW,
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.admin)

    def get(self, name, **kwargs):
        response = self.client.get(reverse(name, kwargs=kwargs or None))
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        return body.get("data", body)


class AnalyticsInCreditsTests(AdminFixture):
    """One teacher: two grading calls (9,150 and 10,850 raw) and one
    creation call (2,345 raw), all now; 22,345 raw = 22 credits."""

    def setUp(self):
        self.sign_in_admin()
        self.user = self.teacher()
        set_profile(
            self.user,
            joined_beta_at=timezone.now() - timedelta(days=3),
            initial_beta_credits=20_000_000,
            total_credits_used=22_345,
            credits_used_grading=20_000,
            credits_used_creation=2_345,
            credits_used_feedback=0,
            analytics_view_count=4,
        )
        self.charge(self.user, 9_150, GRADING)
        self.charge(self.user, 10_850, GRADING)
        self.charge(self.user, 2_345, CREATION)

    def today(self, rows, key="credits"):
        date = timezone.now().date().isoformat()
        (row,) = [r for r in rows if r["date"] == date]
        return row[key]

    def test_daily_credit_consumption(self):
        rows = self.get("beta-chart-daily-credit-consumption")
        self.assertEqual(self.today(rows), 22)

    def test_weekly_credit_trends(self):
        (row,) = self.get("beta-chart-weekly-credit-trends")
        self.assertEqual(row["total_credits"], 22)

    def test_peak_usage_hours(self):
        (row,) = self.get("beta-chart-peak-usage-hours")
        self.assertEqual(row["total_credits"], 22)

    def test_credits_per_submission_grouping(self):
        rows = self.get("beta-chart-daily-credit-consumption-grouping")
        date = timezone.now().date().isoformat()
        (row,) = [r for r in rows if r["date"] == date]
        self.assertEqual(row["avg_tokens_grading"], 10.0)
        self.assertEqual(row["avg_tokens_creation"], 2.35)
        self.assertEqual(row["avg_tokens_feedback"], 0)

    def test_feature_usage_mix_averages_and_unchanged_shares(self):
        body = self.get("beta-chart-feature-usage-mix")
        self.assertEqual(body["avg_tokens_grading"], 10.0)
        self.assertEqual(body["avg_tokens_creation"], 2.35)
        self.assertEqual(body["avg_tokens_feedback"], 0)
        self.assertEqual(body["avg_tokens_other"], 0)
        # The shares are what they were: 20,000 and 2,345 of 22,345 raw.
        self.assertEqual(body["grading_percent"], 89.51)
        self.assertEqual(body["creation_percent"], 10.49)
        self.assertEqual(body["feedback_percent"], 0)
        self.assertEqual(body["other_percent"], 0)

    def test_feature_usage_mix_counts_each_profile_once(self):
        """SM ruling 2026-11-25: the profile sums were taken over a join with
        the usage logs, so each was multiplied by the teacher's log rows (3
        here): 30,000 raw a grading call instead of 10,000, and 12 views
        instead of 4."""
        body = self.get("beta-chart-feature-usage-mix")
        self.assertEqual(body["total_analytics_views"], 4)
        self.assertEqual(body["views_per_user"], 4.0)
        self.assertEqual(body["avg_tokens_grading"], 10.0)

    def test_summary_average_in_credits_and_unused_ratio_unchanged(self):
        body = self.get("analytics-summary")
        self.assertEqual(body["avg_credits_used"], 22)
        # (20,000,000 - 22,345) / 20,000,000, the same ratio as before.
        self.assertEqual(body["percent_unused_credits"], 99.89)

    def test_credit_usage_stats_in_credits(self):
        body = self.get("analytics-credit-usage-stats")
        self.assertEqual(body["standard_allocation"], 20_000)
        self.assertEqual(body["average_credit_used"], 22)
        self.assertEqual(body["median_credit_used"], 22)
        self.assertEqual(body["p90_credit_used"], 22)
        self.assertEqual(body["percent_unused_credits"], 99.89)

    def test_intent_signal_detail_daily_usage(self):
        body = self.get("analytics-intent-signal-detail", user_id=self.user.pk)
        self.assertEqual(self.today(body["daily_usage"]), 22)
        # Already in AI credits through the user list; unchanged.
        self.assertEqual(body["metrics"]["credit_usage"], 22)


class HalfUpOnTheEndpointsTests(AdminFixture):
    """2,500 raw is 3 credits (half up), not 2 (half to even)."""

    def setUp(self):
        self.sign_in_admin()
        self.user = self.teacher()
        set_profile(self.user, total_credits_used=2_500, initial_beta_credits=1)

    def test_summary_rounds_half_up(self):
        self.assertEqual(self.get("analytics-summary")["avg_credits_used"], 3)

    def test_stats_round_half_up(self):
        body = self.get("analytics-credit-usage-stats")
        self.assertEqual(body["average_credit_used"], 3)
        self.assertEqual(body["median_credit_used"], 3)
        self.assertEqual(body["p90_credit_used"], 3)
