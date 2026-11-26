"""Step 2 (owner go, 2026-11-25): two intent signals instead of four.

A teacher's signals, the same on every surface (billing.intent_signals):
- at_60_percent: credits used >= 60% of the allocation (the ratio the 80%
  flag uses, at 0.6; allocation 0 -> False);
- active_last_week: last_active_at >= now - 7 days (inclusive; null False).
frequent_user and is_power_grader are no longer signals; signal_count is
0-2. The conversion score is 50 + 50, with the flag's exact 7-day rule, and
a data migration rescores every profile once.

    python manage.py test billing.tests.test_two_intent_signals
"""

import importlib
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.apps import apps as django_apps
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from freezegun import freeze_time
from rest_framework.test import APITestCase

from billing import intent_signals as signal_rules
from billing.models import BetaProfile
from billing.services import AnalyticsService
from users.models import CustomUser, UserTypes

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
PW = "Two-signals-pass-1"  # pragma: allowlist secret
MIGRATION = "billing.migrations.0091_rescore_two_intent_signals"


def person(tag, user_type=UserTypes.TEACHER):
    return CustomUser.objects.create_user(
        email=f"two.signals.{tag}@x.example",
        password=PW,
        user_type=user_type,
        is_active=True,
    )


def profile(user, **fields):
    """Written through the queryset: joined_beta_at is auto_now_add."""
    beta, _ = BetaProfile.objects.get_or_create(user=user)
    base = {
        "initial_beta_credits": 10_000,
        "total_credits_used": 0,
        "joined_beta_at": NOW - timedelta(days=10),
        "last_active_at": None,
        "distinct_login_days": 0,
        "credits_used_grading": 0,
        "credits_used_creation": 0,
        "has_hit_80_percent": False,
    }
    base.update(fields)
    BetaProfile.objects.filter(pk=beta.pk).update(**base)
    beta.refresh_from_db()
    return beta


class Frozen(TestCase):
    def setUp(self):
        freezer = freeze_time(NOW)
        freezer.start()
        self.addCleanup(freezer.stop)


class RuleTests(Frozen):
    def flags_of(self, user):
        row = (
            signal_rules.annotate_signals(BetaProfile.objects.filter(user=user), NOW)
            .values(*signal_rules.FLAGS, "signal_count")
            .get()
        )
        return row

    def test_the_two_flags(self):
        self.assertEqual(signal_rules.FLAGS, ("at_60_percent", "active_last_week"))
        self.assertEqual(tuple(signal_rules.SIGNAL_COUNTS), (0, 1, 2))

    def test_sixty_percent_boundary(self):
        below = person("below")
        profile(below, total_credits_used=5_999)
        at = person("at")
        profile(at, total_credits_used=6_000)
        self.assertFalse(self.flags_of(below)["at_60_percent"])
        self.assertTrue(self.flags_of(at)["at_60_percent"])

    def test_no_allocation_is_false(self):
        user = person("noalloc")
        profile(user, initial_beta_credits=0, total_credits_used=500)
        self.assertFalse(self.flags_of(user)["at_60_percent"])

    def test_the_old_signals_no_longer_count(self):
        user = person("old")
        profile(
            user,
            distinct_login_days=20,
            credits_used_grading=9,
            credits_used_creation=1,
            has_hit_80_percent=True,
            total_credits_used=5_000,
        )
        self.assertEqual(self.flags_of(user)["signal_count"], 0)

    def test_both_signals(self):
        user = person("both")
        profile(user, total_credits_used=7_000, last_active_at=NOW - timedelta(days=7))
        row = self.flags_of(user)
        self.assertEqual(row["signal_count"], 2)
        self.assertTrue(row["active_last_week"])


class ScoreTests(Frozen):
    made = 0

    def score(self, **fields):
        ScoreTests.made += 1
        beta = profile(person(f"score{ScoreTests.made}"), **fields)
        return AnalyticsService.conversion_score(beta, now=NOW)[0]

    def test_fifty_fifty(self):
        self.assertEqual(self.score(), 0.0)
        self.assertEqual(self.score(total_credits_used=6_000), 50.0)
        self.assertEqual(self.score(last_active_at=NOW - timedelta(days=1)), 50.0)
        self.assertEqual(
            self.score(total_credits_used=6_000, last_active_at=NOW), 100.0
        )

    def test_the_old_signals_score_nothing(self):
        self.assertEqual(
            self.score(
                distinct_login_days=20,
                credits_used_grading=9,
                credits_used_creation=1,
                has_hit_80_percent=True,
            ),
            0.0,
        )

    def test_the_flags_exact_seven_day_rule(self):
        self.assertEqual(self.score(last_active_at=NOW - timedelta(days=7)), 50.0)
        # 7 days and 23 hours used to score; the flag says no.
        self.assertEqual(
            self.score(last_active_at=NOW - timedelta(days=7, hours=23)), 0.0
        )

    def test_no_allocation_scores_nothing_for_usage(self):
        self.assertEqual(
            self.score(initial_beta_credits=0, total_credits_used=6_000), 0.0
        )


class MigrationRescoreTests(Frozen):
    def test_every_profile_is_rescored_once(self):
        both = profile(
            person("m.both"),
            total_credits_used=6_000,
            last_active_at=NOW - timedelta(days=2),
            conversion_probability=20.0,
        )
        old = profile(
            person("m.old"),
            distinct_login_days=20,
            has_hit_80_percent=True,
            conversion_probability=60.0,
        )
        importlib.import_module(MIGRATION).rescore(django_apps, None)
        both.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual(both.conversion_probability, 100.0)
        self.assertEqual(old.conversion_probability, 0.0)


class SurfaceTests(APITestCase):
    def setUp(self):
        freezer = freeze_time(NOW)
        freezer.start()
        self.addCleanup(freezer.stop)
        cache.clear()
        self.admin = CustomUser.objects.create_user(
            email="two.signals.admin@x.example",
            password=PW,
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.admin)
        self.none = person("none")
        profile(self.none, distinct_login_days=20, has_hit_80_percent=True)
        self.one = person("one")
        profile(self.one, total_credits_used=6_000)
        self.two = person("two")
        profile(
            self.two,
            total_credits_used=9_000,
            last_active_at=NOW - timedelta(days=1),
            has_hit_80_percent=True,
            credits_used_grading=9,
            credits_used_creation=1,
        )

    def get(self, name, params=None, **kwargs):
        response = self.client.get(reverse(name, kwargs=kwargs or None), params or {})
        return response

    def data(self, name, params=None, **kwargs):
        response = self.get(name, params, **kwargs)
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        return body.get("data", body)

    def test_lead_rows_carry_the_two_flags(self):
        rows = self.data("analytics-intent-signals")["results"]
        for row in rows:
            self.assertEqual(set(row["flags"]), {"at_60_percent", "active_last_week"})
        by_email = {row["email"]: row for row in rows}
        self.assertTrue(by_email[self.two.email]["flags"]["at_60_percent"])
        # primary_use_case is a metric, not a signal: unchanged.
        self.assertEqual(
            by_email[self.two.email]["metrics"]["primary_use_case"], "Grading"
        )

    def test_lead_filters(self):
        rows = self.data("analytics-intent-signals", {"at_60_percent": "true"})[
            "results"
        ]
        self.assertEqual(
            {row["email"] for row in rows}, {self.one.email, self.two.email}
        )
        rows = self.data("analytics-intent-signals", {"signals_min": "2"})["results"]
        self.assertEqual([row["email"] for row in rows], [self.two.email])
        self.assertEqual(
            self.get("analytics-intent-signals", {"signals_min": "3"}).status_code,
            400,
        )

    def test_detail_flags(self):
        body = self.data("analytics-intent-signal-detail", user_id=self.one.pk)
        self.assertEqual(
            body["flags"], {"at_60_percent": True, "active_last_week": False}
        )

    def test_distribution_has_buckets_zero_one_two(self):
        body = self.data("beta-chart-intent-signal-distribution")
        rows = {row["signal_count"]: row for row in body["distribution"]}
        self.assertEqual(sorted(rows), [0, 1, 2])
        self.assertEqual(rows[0]["user_count"], 1)
        self.assertEqual(rows[1]["user_count"], 1)
        self.assertEqual(rows[2]["user_count"], 1)

    def test_summary_sixty_fields_and_kept_cards(self):
        body = self.data("analytics-summary")
        self.assertEqual(body["credit_used_greater_than_60_count"], 2)
        self.assertEqual(body["credit_used_greater_than_60_percent"], 66.67)
        # Removed (owner, 2026-11-25): the 60% fields replace them.
        self.assertNotIn("credit_used_greater_than_80_count", body)
        self.assertNotIn("credit_used_greater_than_80_percent", body)
        # Step 4 (owner, 2026-11-25): the 8-day and grading cards are gone.
        self.assertNotIn("login_greater_than_8_days_count", body)
        self.assertNotIn("grading_percent_greater_than_creation_count", body)
        self.assertEqual(body["active_last_7_days_count"], 1)

    def test_user_list_fields_and_filter(self):
        rows = self.data("user-list", {"at_60_percent": "true"})["results"]
        self.assertEqual(
            {row["email"] for row in rows}, {self.one.email, self.two.email}
        )
        for row in rows:
            self.assertIn("at_60_percent", row)
            for gone in ("at_80_percent", "frequent_user", "is_power_grader"):
                self.assertNotIn(gone, row)
        self.assertEqual(self.get("user-list", {"signals_min": "3"}).status_code, 400)
