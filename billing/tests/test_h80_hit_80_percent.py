"""
H-80: reaching 100% of the allocation also means having reached 80%.

AnalyticsService.record_consumption set has_hit_cap at >= 100% and
has_hit_80_percent only in an `elif` for 80-99%. A teacher whose single use
jumped from below 80% straight to 100% or more never got has_hit_80_percent,
so the at_80_percent signal (billing.intent_signals) and the +30 score in
calculate_conversion_probability missed the most engaged teachers.

The data migration backfills has_hit_80_percent=True on rows that already
have has_hit_cap=True, and touches nothing else.
"""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from billing.models import BetaProfile
from billing.services import AnalyticsService
from users.models import UserTypes

CustomUser = get_user_model()

BACKFILL = "billing.migrations.0074_h80_backfill_has_hit_80_percent"


def _teacher(email):
    return CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        is_active=True,
    )


def _profile(user, allocation, **flags):
    profile, _ = BetaProfile.objects.update_or_create(
        user=user, defaults={"initial_beta_credits": allocation, **flags}
    )
    return profile


class ReachingTheCapAlsoSetsEightyPercentTests(TestCase):
    def setUp(self):
        self.user = _teacher("h80.jump@gmail.com")

    def flags(self):
        profile = BetaProfile.objects.get(user=self.user)
        return profile.has_hit_80_percent, profile.has_hit_cap

    def test_a_jump_from_70_to_110_percent_sets_both_flags(self):
        _profile(self.user, 1_000)
        AnalyticsService.record_consumption(self.user, 700, "Grading Assignment")
        self.assertEqual(self.flags(), (False, False))

        AnalyticsService.record_consumption(self.user, 400, "Grading Assignment")
        self.assertEqual(self.flags(), (True, True))

    def test_a_first_use_of_exactly_100_percent_sets_both_flags(self):
        _profile(self.user, 1_000)
        AnalyticsService.record_consumption(self.user, 1_000, "Grading Assignment")
        self.assertEqual(self.flags(), (True, True))

    def test_85_percent_sets_80_percent_only(self):
        _profile(self.user, 1_000)
        AnalyticsService.record_consumption(self.user, 850, "Grading Assignment")
        self.assertEqual(self.flags(), (True, False))

    def test_79_percent_sets_neither(self):
        _profile(self.user, 1_000)
        AnalyticsService.record_consumption(self.user, 790, "Grading Assignment")
        self.assertEqual(self.flags(), (False, False))

    def test_a_zero_allocation_sets_neither(self):
        _profile(self.user, 0)
        AnalyticsService.record_consumption(self.user, 10_000, "Grading Assignment")
        self.assertEqual(self.flags(), (False, False))


class BackfillTests(TestCase):
    def setUp(self):
        self.migration = importlib.import_module(BACKFILL)
        self.rows = {}
        for name, eighty, cap in (
            ("cap_only", False, True),
            ("both", True, True),
            ("eighty_only", True, False),
            ("neither", False, False),
        ):
            user = _teacher(f"h80.{name}@gmail.com")
            self.rows[name] = _profile(
                user,
                1_000,
                has_hit_80_percent=eighty,
                has_hit_cap=cap,
                total_credits_used=123,
                conversion_probability=42.5,
            )

    def state(self):
        return {
            name: tuple(
                BetaProfile.objects.filter(pk=p.pk).values_list(
                    "has_hit_80_percent",
                    "has_hit_cap",
                    "total_credits_used",
                    "conversion_probability",
                )[0]
            )
            for name, p in self.rows.items()
        }

    def test_the_backfill_sets_80_percent_on_cap_only_rows_and_nothing_else(self):
        self.migration.backfill_hit_80_percent(apps, None)
        self.assertEqual(
            self.state(),
            {
                "cap_only": (True, True, 123, 42.5),
                "both": (True, True, 123, 42.5),
                "eighty_only": (True, False, 123, 42.5),
                "neither": (False, False, 123, 42.5),
            },
        )

    def test_the_backfill_is_safe_to_run_twice(self):
        self.migration.backfill_hit_80_percent(apps, None)
        first = self.state()
        self.migration.backfill_hit_80_percent(apps, None)
        self.assertEqual(self.state(), first)

    def test_the_migration_is_one_reversible_data_step_after_0073(self):
        from django.db import migrations

        migration = self.migration.Migration
        self.assertEqual(
            migration.dependencies,
            [("billing", "0073_schoolcreditallocation_grant_anchor_at")],
        )
        self.assertEqual(len(migration.operations), 1)
        operation = migration.operations[0]
        self.assertIsInstance(operation, migrations.RunPython)
        self.assertTrue(operation.reversible)
        self.assertIs(operation.reverse_code, migrations.RunPython.noop)
