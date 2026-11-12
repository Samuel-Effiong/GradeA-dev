"""
R2 (beta bug fix, owner go 2026-11-12): the beta tallies follow the wallet
charges. Plan: team/PLAN_beta_tallies_from_wallet_2026-11-12.md.

BetaProfile.total_credits_used and the per-feature counters were running
counts nothing checked. These tests pin:

- reconcile (billing/beta_tallies.py) sets them to the charges
  (CreditUsageLog, is_refunded false, owner Coalesce(user_id, wallet's
  user)), sets or clears the 80%/cap flags and rescores the profile;
- migration 0075 runs it once, the nightly task every night; both clear a
  flag the charges don't support (owner, 2026-11-12: the flags always match
  real usage), and neither writes a profile that already matches;
- a refund lowers the tallies by the whole refunded log amount, as the
  ledger does, even when the bucket refund is clamped, and recomputes the
  flags and the score at once;
- the users list's at_80_percent Swagger text says so;
- allocation 0 leaves the flags alone;
- the beta summary and the lead flags read the corrected numbers.
"""

import importlib
import uuid
from datetime import timedelta
from typing import Any

from django.apps import apps as django_apps
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from billing.models import (
    BetaProfile,
    CreditBucket,
    CreditBucketType,
    CreditUsageLog,
    CreditWallet,
)
from billing.services import AnalyticsService, SubscriptionService
from users.models import CustomUser, UserTypes

PW = "R2-tallies-pass-1"  # pragma: allowlist secret
GRADING = "Grading Assignment"
CREATION = "Assignment Generation"
FEEDBACK = "Formatted Grade"
UNMAPPED = "Custom AI Chat"


def beta_tallies():
    return importlib.import_module("billing.beta_tallies")


def set_profile(user, **fields):
    """Through the queryset: joined_beta_at is auto_now_add."""
    profile, _ = BetaProfile.objects.get_or_create(user=user)
    BetaProfile.objects.filter(pk=profile.pk).update(**fields)
    profile.refresh_from_db()
    return profile


class TallyFixture:
    _n = 0

    def teacher(self):
        TallyFixture._n += 1
        user = CustomUser.objects.create_user(
            email=f"r2.teacher{TallyFixture._n}@x.example",
            password=PW,
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1_000_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        user.r2_wallet, user.r2_bucket = wallet, bucket
        return user

    def charge(self, user, amount, feature=GRADING, refunded=False, **fields):
        log = CreditUsageLog.record(
            wallet=user.r2_wallet,
            bucket=user.r2_bucket,
            amount=amount,
            feature=feature,
            task_id=str(uuid.uuid4()),
            **fields,
        )
        if refunded:
            CreditUsageLog.objects.filter(pk=log.pk).update(is_refunded=True)
        return log

    def drifted(self, user, **fields):
        """A profile joined 10 days ago, allocation 1000, with no logins."""
        base = {
            "joined_beta_at": timezone.now() - timedelta(days=10, hours=1),
            "initial_beta_credits": 1000,
            "distinct_login_days": 0,
            "last_active_at": None,
        }
        base.update(fields)
        return set_profile(user, **base)


class ReconcileTests(TallyFixture, TestCase):
    def test_tally_above_the_charges_is_corrected_and_unsupported_flags_cleared(self):
        user = self.teacher()
        profile = self.drifted(
            user,
            total_credits_used=5000,
            credits_used_grading=4000,
            credits_used_creation=1000,
            credits_used_feedback=0,
            has_hit_80_percent=True,
            has_hit_cap=True,
            conversion_probability=50.0,
            usage_velocity=500.0,
        )
        self.charge(user, 300, GRADING)
        self.charge(user, 200, CREATION)
        self.charge(user, 100, FEEDBACK)
        self.charge(user, 400, GRADING, refunded=True)
        self.charge(user, 50, UNMAPPED)

        corrections = beta_tallies().reconcile(BetaProfile.objects.all())

        profile.refresh_from_db()
        self.assertEqual(profile.total_credits_used, 650)
        self.assertEqual(profile.credits_used_grading, 300)
        self.assertEqual(profile.credits_used_creation, 200)
        self.assertEqual(profile.credits_used_feedback, 100)
        self.assertFalse(profile.has_hit_80_percent)
        self.assertFalse(profile.has_hit_cap)
        # Rescored: only grading > creation (+20); 650 over 10 days.
        self.assertEqual(profile.conversion_probability, 20.0)
        self.assertEqual(profile.usage_velocity, 65.0)
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0].user_id, user.id)
        self.assertEqual(corrections[0].before["total_credits_used"], 5000)
        self.assertEqual(corrections[0].after["total_credits_used"], 650)
        self.assertEqual(corrections[0].total_gap, -4350)

    def test_tally_below_the_charges_is_corrected_and_reached_flags_set(self):
        user = self.teacher()
        profile = self.drifted(user, total_credits_used=10, credits_used_grading=10)
        self.charge(user, 850, GRADING)

        beta_tallies().reconcile(BetaProfile.objects.all())

        profile.refresh_from_db()
        self.assertEqual(profile.total_credits_used, 850)
        self.assertEqual(profile.credits_used_grading, 850)
        self.assertTrue(profile.has_hit_80_percent)
        self.assertFalse(profile.has_hit_cap)
        # 80% (+30) and grading > creation (+20).
        self.assertEqual(profile.conversion_probability, 50.0)
        self.assertEqual(profile.usage_velocity, 85.0)

    def test_past_the_allocation_sets_both_flags(self):
        user = self.teacher()
        profile = self.drifted(user)
        self.charge(user, 1200, CREATION)
        beta_tallies().reconcile(BetaProfile.objects.all())
        profile.refresh_from_db()
        self.assertTrue(profile.has_hit_cap)
        self.assertTrue(profile.has_hit_80_percent)

    def test_the_cap_alone_is_cleared_between_80_and_100_percent(self):
        user = self.teacher()
        profile = self.drifted(
            user, total_credits_used=1500, has_hit_80_percent=True, has_hit_cap=True
        )
        self.charge(user, 900, GRADING)
        beta_tallies().reconcile(BetaProfile.objects.all())
        profile.refresh_from_db()
        self.assertEqual(profile.total_credits_used, 900)
        self.assertTrue(profile.has_hit_80_percent)
        self.assertFalse(profile.has_hit_cap)

    def test_80_percent_is_set_without_the_cap(self):
        user = self.teacher()
        profile = self.drifted(user)
        self.charge(user, 820, GRADING)
        beta_tallies().reconcile(BetaProfile.objects.all())
        profile.refresh_from_db()
        self.assertTrue(profile.has_hit_80_percent)
        self.assertFalse(profile.has_hit_cap)

    def test_allocation_zero_leaves_the_flags_alone(self):
        on, off = self.teacher(), self.teacher()
        flagged = self.drifted(
            on,
            initial_beta_credits=0,
            has_hit_80_percent=True,
            has_hit_cap=True,
            total_credits_used=7,
        )
        clean = self.drifted(off, initial_beta_credits=0)
        self.charge(off, 50_000, GRADING)

        beta_tallies().reconcile(BetaProfile.objects.all())

        flagged.refresh_from_db()
        clean.refresh_from_db()
        self.assertTrue(flagged.has_hit_80_percent)
        self.assertTrue(flagged.has_hit_cap)
        self.assertEqual(flagged.total_credits_used, 0)
        self.assertFalse(clean.has_hit_80_percent)
        self.assertFalse(clean.has_hit_cap)
        self.assertEqual(clean.total_credits_used, 50_000)

    def test_a_matching_profile_is_not_written(self):
        user = self.teacher()
        self.charge(user, 300, GRADING)
        profile = self.drifted(
            user,
            total_credits_used=300,
            credits_used_grading=300,
            conversion_probability=77.0,
        )
        with CaptureQueriesContext(connection) as queries:
            corrections = beta_tallies().reconcile(BetaProfile.objects.all())
        self.assertEqual(corrections, [])
        writes = [
            q["sql"]
            for q in queries.captured_queries
            if q["sql"].startswith("UPDATE") and "billing_betaprofile" in q["sql"]
        ]
        self.assertEqual(writes, [])
        profile.refresh_from_db()
        self.assertEqual(profile.conversion_probability, 77.0)

    def test_charges_are_matched_by_user_id_then_by_the_wallet(self):
        user = self.teacher()
        profile = self.drifted(user)
        # A log of a wallet that no longer exists (wallet recreated): its
        # own user_id still makes it this teacher's charge.
        CreditUsageLog.objects.create(
            user_id=user.id,
            wallet_id=uuid.uuid4(),
            bucket=user.r2_bucket,
            amount=40,
            feature=GRADING,
        )
        # An older log with no user_id: owned through its wallet.
        CreditUsageLog.objects.create(
            wallet=user.r2_wallet, bucket=user.r2_bucket, amount=2, feature=GRADING
        )
        # Another teacher's charge is not counted.
        self.charge(self.teacher(), 999, GRADING)

        beta_tallies().reconcile(BetaProfile.objects.filter(pk=profile.pk))

        profile.refresh_from_db()
        self.assertEqual(profile.total_credits_used, 42)
        self.assertEqual(profile.credits_used_grading, 42)

    def test_the_reconciled_total_equals_the_users_list_credit_usage(self):
        from users.user_list import ensure

        user = self.teacher()
        profile = self.drifted(user, total_credits_used=99_999)
        self.charge(user, 300, GRADING)
        self.charge(user, 700, CREATION, refunded=True)
        self.charge(user, 25, UNMAPPED)
        CreditUsageLog.objects.create(  # wallet row gone, user_id kept
            user_id=user.id,
            wallet_id=uuid.uuid4(),
            bucket=user.r2_bucket,
            amount=40,
            feature=GRADING,
        )
        CreditUsageLog.objects.create(  # legacy: no user_id
            wallet=user.r2_wallet, bucket=user.r2_bucket, amount=2, feature=GRADING
        )
        self.charge(self.teacher(), 999, GRADING)

        beta_tallies().reconcile(BetaProfile.objects.all())

        profile.refresh_from_db()
        listed = (
            ensure(
                CustomUser.objects.filter(pk=user.pk),
                "credits_used_raw",
                annotate=True,
            )
            .values_list("credits_used_raw", flat=True)
            .get()
        )
        self.assertEqual(profile.total_credits_used, 367)
        self.assertEqual(listed, profile.total_credits_used)

    def test_many_profiles_take_a_fixed_number_of_queries(self):
        users = [self.teacher() for _ in range(6)]
        for user in users:
            self.drifted(user, total_credits_used=1)
            self.charge(user, 5, GRADING)

        def count():
            BetaProfile.objects.update(total_credits_used=1)
            with CaptureQueriesContext(connection) as queries:
                beta_tallies().reconcile(BetaProfile.objects.all())
            return len(queries.captured_queries)

        few = count()
        for _ in range(6):
            user = self.teacher()
            self.drifted(user, total_credits_used=1)
            self.charge(user, 5, GRADING)
        self.assertEqual(count(), few)

    def test_the_migration_corrects_every_profile_and_clears_flags(self):
        migration = importlib.import_module(
            "billing.migrations.0075_r2_beta_tallies_from_wallet"
        )
        user = self.teacher()
        profile = self.drifted(
            user, total_credits_used=9000, has_hit_80_percent=True, has_hit_cap=True
        )
        self.charge(user, 100, CREATION)

        migration.reconcile_all(django_apps, None)

        profile.refresh_from_db()
        self.assertEqual(profile.total_credits_used, 100)
        self.assertEqual(profile.credits_used_creation, 100)
        self.assertFalse(profile.has_hit_80_percent)
        self.assertFalse(profile.has_hit_cap)
        self.assertEqual(profile.usage_velocity, 10.0)


class RefundRuleTests(TallyFixture, TestCase):
    """The tally drops by what the ledger stops counting: the whole log."""

    def setUp(self):
        self.user = self.teacher()
        self.wallet = self.user.r2_wallet
        self.bucket = self.user.r2_bucket
        set_profile(self.user, initial_beta_credits=1_000_000)

    def consume(self, amount, task_id, feature=GRADING):
        self.wallet.consume_credits(
            amount=amount, feature=feature, task_type="t", task_id=task_id
        )
        AnalyticsService.record_consumption(
            user=self.user, amount=amount, feature=feature
        )

    def charged(self):
        return sum(
            CreditUsageLog.objects.filter(
                wallet=self.wallet, is_refunded=False
            ).values_list("amount", flat=True)
        )

    def test_a_clamped_refund_keeps_the_tally_equal_to_the_ledger(self):
        self.consume(300, "keep", CREATION)
        self.consume(500, "fail")
        # The bucket was reset out of band: the refund can only give back 20
        # of the 500 to the bucket, but the whole log leaves the charges.
        CreditBucket.objects.filter(pk=self.bucket.pk).update(used_credits=20)

        SubscriptionService.refund_credits("fail")

        profile = BetaProfile.objects.get(user=self.user)
        self.assertEqual(self.charged(), 300)
        self.assertEqual(profile.total_credits_used, 300)
        self.assertEqual(profile.credits_used_grading, 0)
        self.assertEqual(profile.credits_used_creation, 300)
        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, 0)

    def test_the_tally_never_goes_below_zero(self):
        self.consume(500, "fail")
        BetaProfile.objects.filter(user=self.user).update(
            total_credits_used=100, credits_used_grading=100
        )
        SubscriptionService.refund_credits("fail")
        profile = BetaProfile.objects.get(user=self.user)
        self.assertEqual(profile.total_credits_used, 0)
        self.assertEqual(profile.credits_used_grading, 0)

    def test_a_refund_below_80_percent_clears_the_flag_and_the_score_at_once(self):
        set_profile(self.user, initial_beta_credits=1000)
        self.consume(770, "keep")
        self.consume(50, "fail")
        profile = BetaProfile.objects.get(user=self.user)
        AnalyticsService.calculate_conversion_probability(profile)
        profile.refresh_from_db()
        self.assertTrue(profile.has_hit_80_percent)  # 82%
        before = profile.conversion_probability

        SubscriptionService.refund_credits("fail")

        profile.refresh_from_db()
        self.assertEqual(profile.total_credits_used, 770)  # 77%
        self.assertFalse(profile.has_hit_80_percent)
        self.assertFalse(profile.has_hit_cap)
        self.assertEqual(profile.conversion_probability, before - 30)

    def test_a_refund_below_the_cap_clears_the_cap_only(self):
        set_profile(self.user, initial_beta_credits=1000)
        self.consume(900, "keep")
        self.consume(200, "fail")
        profile = BetaProfile.objects.get(user=self.user)
        self.assertTrue(profile.has_hit_cap)
        SubscriptionService.refund_credits("fail")
        profile.refresh_from_db()
        self.assertFalse(profile.has_hit_cap)
        self.assertTrue(profile.has_hit_80_percent)

    def test_a_refund_with_no_allocation_leaves_the_flags(self):
        set_profile(self.user, initial_beta_credits=0, has_hit_80_percent=True)
        self.consume(50, "fail")
        SubscriptionService.refund_credits("fail")
        profile = BetaProfile.objects.get(user=self.user)
        self.assertEqual(profile.total_credits_used, 0)
        self.assertTrue(profile.has_hit_80_percent)


class NightlyTaskTests(TallyFixture, TestCase):
    def run_task(self):
        tasks = importlib.import_module("billing.tasks")
        return tasks.reconcile_beta_tallies()

    def test_the_task_corrects_drift_and_logs_it(self):
        user = self.teacher()
        profile = self.drifted(user, total_credits_used=5000, credits_used_grading=5000)
        self.charge(user, 650, GRADING)

        with self.assertLogs("billing.tasks", level="INFO") as logs:
            summary = self.run_task()

        profile.refresh_from_db()
        self.assertEqual(profile.total_credits_used, 650)
        self.assertEqual(profile.credits_used_grading, 650)
        self.assertEqual(profile.usage_velocity, 65.0)
        line = [m for m in logs.output if str(user.id) in m]
        self.assertEqual(len(line), 1, logs.output)
        self.assertIn("5000 -> 650", line[0])
        self.assertIn("-4350", line[0])
        self.assertIn("1 profile(s) corrected", summary)

    def test_the_task_leaves_a_matching_profile_untouched(self):
        user = self.teacher()
        self.charge(user, 300, GRADING)
        profile = self.drifted(
            user,
            total_credits_used=300,
            credits_used_grading=300,
            conversion_probability=77.0,
            usage_velocity=1.5,
        )
        with self.assertLogs("billing.tasks", level="INFO") as logs:
            summary = self.run_task()
        profile.refresh_from_db()
        self.assertEqual(profile.conversion_probability, 77.0)
        self.assertEqual(profile.usage_velocity, 1.5)
        self.assertFalse([m for m in logs.output if str(user.id) in m])
        self.assertIn("0 profile(s) corrected", summary)

    def test_the_task_clears_an_unsupported_flag(self):
        user = self.teacher()
        profile = self.drifted(
            user,
            total_credits_used=950,
            has_hit_80_percent=True,
            has_hit_cap=True,
            conversion_probability=50.0,
        )
        self.charge(user, 10, GRADING)
        self.run_task()
        profile.refresh_from_db()
        self.assertEqual(profile.total_credits_used, 10)
        self.assertFalse(profile.has_hit_80_percent)
        self.assertFalse(profile.has_hit_cap)
        self.assertEqual(profile.conversion_probability, 20.0)

    def test_the_beat_entry_runs_nightly_before_the_scoring(self):
        entry = settings.CELERY_BEAT_SCHEDULE["reconcile-beta-tallies"]
        self.assertEqual(entry["task"], "billing.tasks.reconcile_beta_tallies")
        scoring = settings.CELERY_BEAT_SCHEDULE["recalculate-conversion-probabilities"]
        mine: Any = entry["schedule"]
        theirs: Any = scoring["schedule"]
        self.assertEqual(mine.hour, {0})
        self.assertEqual(theirs.hour, {0})
        self.assertLess(min(mine.minute), min(theirs.minute))


class ReadersTests(TallyFixture, APITestCase):
    """The readers are unchanged; they show the corrected numbers."""

    def setUp(self):
        cache.clear()
        self.admin = CustomUser.objects.create_user(
            email="r2.admin@x.example",
            password=PW,
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.admin)
        self.user = self.teacher()
        # Drifted: the tallies claim 90% of the allocation, mostly grading;
        # the charges are 10%, mostly creation.
        self.profile = self.drifted(
            self.user,
            total_credits_used=900,
            credits_used_grading=800,
            credits_used_creation=100,
            has_hit_80_percent=True,
        )
        self.charge(self.user, 30, GRADING)
        self.charge(self.user, 70, CREATION)
        beta_tallies().reconcile(BetaProfile.objects.all())

    def test_the_beta_summary_reads_the_corrected_tallies(self):
        response = self.client.get(reverse("analytics-summary"))
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        body = body.get("data", body)
        self.assertEqual(body["avg_credits_used"], 100)
        self.assertEqual(body["credit_used_greater_than_80_count"], 0)
        self.assertEqual(body["grading_percent_greater_than_creation_count"], 0)

    def test_the_lead_flags_read_the_corrected_tallies(self):
        response = self.client.get(reverse("analytics-intent-signals"))
        self.assertEqual(response.status_code, 200, response.content[:300])
        rows = [
            row
            for row in response.json()["data"]["results"]
            if row["email"] == self.user.email
        ]
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["flags"]["at_80_percent"])
        self.assertFalse(rows[0]["flags"]["is_power_grader"])


class AtEightyPercentSwaggerTests(APITestCase):
    """The users list's at_80_percent text (approved wording, 2026-11-12)."""

    NEW = (
        "It is worked out from the credits they have really used, after "
        "refunds, so a refund that takes them below 80% removes it."
    )

    def test_the_swagger_text_says_a_refund_below_80_percent_removes_it(self):
        from users.tests_user_list_docs import list_parameters, own_text

        text = " ".join(
            own_text(list_parameters(self)["at_80_percent"].get("description")).split()
        )
        self.assertIn(self.NEW, text)
        self.assertNotIn("Once marked, it stays marked.", text)
        self.assertTrue(text.startswith("true: keeps teachers who have been marked"))
