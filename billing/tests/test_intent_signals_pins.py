"""
Signals sort and filter (brief 4, owner-approved for beta 2026-11-11; SM -31
rulings on checkpoint bc5eafb7c, docs/evidence/signals-beta/DESIGN.md).

The four signal rules are moving into one shared module
(billing/intent_signals.py). Every existing payload that shows them must
stay byte-identical: the sales-leads list (beta/intent-signals) with no
params, the signal distribution chart, the single-lead detail and the
cohort summary.

The snapshot billing/tests/data/intent_signals_pins.json was captured from
the base commit (origin/beta 44904774a), BEFORE any change, by running this
file once with INTENT_SIGNALS_PIN_WRITE=1 (NOTES, step P0). The clock is
frozen, so every date is fixed. Each user's random UUID is replaced by
their email.
"""

import json
import os
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Any

from django.urls import reverse
from freezegun import freeze_time
from rest_framework.test import APITestCase

from billing.models import BetaProfile
from users.models import CustomUser, UserTypes

PIN = Path(__file__).parent / "data" / "intent_signals_pins.json"
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
PW = "Signals-pin-pass-1"  # pragma: allowlist secret


def profile(user, **fields):
    """Written through the queryset: joined_beta_at is auto_now_add."""
    beta, _ = BetaProfile.objects.get_or_create(user=user)
    BetaProfile.objects.filter(pk=beta.pk).update(**fields)
    beta.refresh_from_db()
    return beta


class SignalsFixture(APITestCase):
    """Teachers with every signal combination the rules distinguish, both
    sides of each boundary, and a non-teacher profile (summary counts all
    profiles, the leads list and the chart only teachers)."""

    def setUp(self):
        self.freezer = freeze_time(NOW)
        self.freezer.start()
        self.addCleanup(self.freezer.stop)
        self.admin = CustomUser.objects.create_user(
            email="signals.admin@x.example",
            password=PW,
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.admin)

    def person(self, email, first, last, user_type=UserTypes.TEACHER):
        return CustomUser.objects.create_user(
            email=email,
            password=PW,
            user_type=user_type,
            is_active=True,
            first_name=first,
            last_name=last,
        )

    def build(self):
        week = timedelta(days=7)
        rows: list[tuple[str, str, str, dict[str, Any]]] = [
            # email, first, last, profile fields
            (
                "a.all4.sig@x.example",
                "Ada",
                "Four",
                {
                    "has_hit_80_percent": True,
                    "distinct_login_days": 9,
                    "last_active_at": NOW - timedelta(days=1),
                    "credits_used_grading": 500,
                    "credits_used_creation": 100,
                    "conversion_probability": 91.54,
                    "total_credits_used": 17_000_000,
                    "initial_beta_credits": 20_000_000,
                },
            ),
            (
                "b.three.sig@x.example",
                "Ben",
                "Three",
                {
                    "has_hit_80_percent": True,
                    "distinct_login_days": 8,
                    "last_active_at": NOW - week,
                    "credits_used_grading": 100,
                    "credits_used_creation": 300,
                    "conversion_probability": 72.25,
                    "total_credits_used": 16_500_000,
                },
            ),
            (
                "c.one.sig@x.example",
                "Cy",
                "One",
                {
                    "distinct_login_days": 7,
                    "last_active_at": NOW - week - timedelta(microseconds=1),
                    "credits_used_grading": 300,
                    "credits_used_creation": 200,
                    "conversion_probability": 40.0,
                    "total_credits_used": 500,
                },
            ),
            (
                "d.none.sig@x.example",
                "Di",
                "None",
                {"conversion_probability": 10.5, "initial_beta_credits": 0},
            ),
            (
                "e.two.sig@x.example",
                "Ed",
                "Two",
                {
                    "has_hit_80_percent": True,
                    "distinct_login_days": 12,
                    "credits_used_grading": 50,
                    "credits_used_creation": 50,
                    "conversion_probability": 55.0,
                    "total_credits_used": 16_000_000,
                    "credits_used_feedback": 20,
                    "analytics_view_count": 3,
                    "days_to_first_action": 2,
                    "has_hit_cap": True,
                },
            ),
        ]
        people = {}
        for email, first, last, fields in rows:
            user = self.person(email, first, last)
            profile(user, **fields)
            people[email] = user
        outsider = self.person(
            "g.admin.sig@x.example", "Gil", "Admin", UserTypes.SCHOOL_ADMIN
        )
        profile(
            outsider,
            has_hit_80_percent=True,
            distinct_login_days=20,
            last_active_at=NOW,
            credits_used_grading=9,
            credits_used_creation=1,
            conversion_probability=99.0,
        )
        people[outsider.email] = outsider
        return people

    def anonymize(self, value, people):
        text = json.dumps(value, sort_keys=True, default=str)
        for email, user in people.items():
            text = text.replace(str(user.pk), f"<id:{email}>")
        return json.loads(text)

    def get(self, name, **kwargs):
        response = self.client.get(reverse(name, kwargs=kwargs or None))
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()["data"]

    def payloads(self):
        people = self.build()
        out = {
            "intent_signals": self.get("analytics-intent-signals"),
            "intent_signal_distribution": self.get(
                "beta-chart-intent-signal-distribution"
            ),
            "summary": self.get("analytics-summary"),
        }
        for email in ("a.all4.sig@x.example", "g.admin.sig@x.example"):
            out[f"intent_signal_detail {email}"] = self.get(
                "analytics-intent-signal-detail", user_id=people[email].pk
            )
        return self.anonymize(out, people)


class IntentSignalsPinTests(SignalsFixture):
    def test_the_signal_payloads_are_what_they_were(self):
        payloads = self.payloads()

        if os.environ.get("INTENT_SIGNALS_PIN_WRITE") == "1":
            PIN.parent.mkdir(exist_ok=True)
            PIN.write_text(json.dumps(payloads, indent=2, sort_keys=True) + "\n")
            self.skipTest(f"pin written to {PIN}")

        self.assertEqual(payloads, json.loads(PIN.read_text()))

    def test_the_pin_is_not_vacuous(self):
        pinned = json.loads(PIN.read_text())
        leads = pinned["intent_signals"]["results"]
        flags = {row["email"]: row["flags"] for row in leads}

        # Every flag is True somewhere and False somewhere.
        for name in (
            "at_80_percent",
            "frequent_user",
            "active_last_week",
            "is_power_grader",
        ):
            values = {f[name] for f in flags.values()}
            self.assertEqual(values, {True, False}, name)
        # The 7-day boundary is on both sides.
        self.assertTrue(flags["b.three.sig@x.example"]["active_last_week"])
        self.assertFalse(flags["c.one.sig@x.example"]["active_last_week"])
        # The non-teacher is in summary's cohort only.
        self.assertNotIn("g.admin.sig@x.example", flags)
        self.assertEqual(pinned["summary"]["total_beta_users"], 6)
        counts = {
            d["signal_count"]: d["user_count"]
            for d in pinned["intent_signal_distribution"]["distribution"]
        }
        self.assertEqual(counts, {1: 1, 2: 1, 3: 1, 4: 1})
