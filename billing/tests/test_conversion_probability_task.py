"""
billing/tests/test_conversion_probability_task.py
=================================================
F4 — the scoring engine that nothing ever called.

`AnalyticsService.calculate_conversion_probability` carries the docstring
"Called by midnight", but there was no Beat entry, no signal and no view
invoking it. Meanwhile BetaProfileViewSet.intent_signals SORTS on
`conversion_probability` and renders it as "score", so every teacher in the
sales-lead list ranked and displayed at a permanent 0.0 — silently wrong
data driving sales decisions, not a crash anyone would notice.

`billing.tasks.recalculate_conversion_probabilities` is the missing
trigger. These tests pin three things:

  1. the task actually scores profiles, and the score reflects the
     documented rubric rather than staying 0.0;
  2. one unscoreable profile does not abort the sweep, and does not
     discard the scores already written (the task is deliberately NOT
     wrapped in a single transaction);
  3. the Beat entry exists and is wired to this task name — a scorer with
     no schedule is exactly the bug being fixed, so the schedule is part
     of the contract.

Also covers the fourth, unguarded division by `initial_beta_credits` in
the sales-leads endpoint (views.py). Three sibling sites already guarded
it; this one did not, so a profile seeded from a plan whose
`monthly_credits` is still the 0 default returned 500 for the WHOLE list,
not just that row.
"""

from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import BetaProfile
from billing.services import AnalyticsService
from billing.tasks import recalculate_conversion_probabilities
from users.models import UserTypes

CustomUser = get_user_model()


def make_teacher(email):
    return CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        is_active=True,
    )


def set_profile(user, **fields):
    """
    Writes straight through the queryset: `joined_beta_at` is auto_now_add,
    so it cannot be set via save().
    """
    profile, _ = BetaProfile.objects.get_or_create(user=user)
    BetaProfile.objects.filter(pk=profile.pk).update(**fields)
    profile.refresh_from_db()
    return profile


class ConversionProbabilityTaskTests(TestCase):
    def test_the_task_scores_a_profile_that_was_stuck_at_zero(self):
        """
        The regression. Before the task existed, this field could only ever
        be 0.0 in production because nothing computed it.
        """
        user = make_teacher("scored@gmail.com")
        now = timezone.now()
        profile = set_profile(
            user,
            distinct_login_days=10,  # +30
            has_hit_80_percent=True,  # +30
            last_active_at=now - timedelta(days=1),  # +20
            credits_used_grading=900,  # +20 (grading > creation)
            credits_used_creation=100,
            total_credits_used=1000,
            joined_beta_at=now - timedelta(days=10),
        )
        self.assertEqual(profile.conversion_probability, 0.0)

        recalculate_conversion_probabilities()

        profile.refresh_from_db()
        self.assertEqual(profile.conversion_probability, 100.0)
        self.assertEqual(profile.usage_velocity, 100.0)

    def test_a_low_engagement_profile_scores_low_rather_than_uniformly_high(self):
        """The task must compute a real score, not stamp a constant."""
        user = make_teacher("lowscore@gmail.com")
        now = timezone.now()
        set_profile(
            user,
            distinct_login_days=1,
            has_hit_80_percent=False,
            last_active_at=now - timedelta(days=30),
            credits_used_grading=10,
            credits_used_creation=900,
            total_credits_used=910,
            joined_beta_at=now - timedelta(days=30),
        )

        recalculate_conversion_probabilities()

        profile = BetaProfile.objects.get(user=user)
        self.assertEqual(profile.conversion_probability, 0.0)
        # But the velocity still had to be computed.
        self.assertAlmostEqual(profile.usage_velocity, 910 / 30, places=4)

    def test_every_profile_is_scored_not_just_the_first(self):
        now = timezone.now()
        for index in range(3):
            user = make_teacher(f"batch{index}@gmail.com")
            set_profile(
                user,
                distinct_login_days=10,
                total_credits_used=100,
                joined_beta_at=now - timedelta(days=5),
            )

        summary = recalculate_conversion_probabilities()

        self.assertEqual(
            BetaProfile.objects.filter(conversion_probability=30.0).count(), 3
        )
        self.assertIn("3 scored", summary)

    def test_a_zero_allocation_profile_does_not_crash_the_sweep(self):
        """
        Ties the task to the F5 guard: `initial_beta_credits=0` used to be a
        ZeroDivisionError, and a nightly sweep is exactly where that would
        have taken out every profile after it.
        """
        user = make_teacher("zeroalloc@gmail.com")
        set_profile(user, initial_beta_credits=0, total_credits_used=50)

        summary = recalculate_conversion_probabilities()

        self.assertIn("1 scored", summary)
        self.assertIn("0 failed", summary)

    def test_one_bad_profile_does_not_abort_the_others(self):
        """
        The task is deliberately not one big transaction. A failure on one
        profile must be logged and skipped, leaving earlier scores written.
        """
        now = timezone.now()
        good = make_teacher("survivor@gmail.com")
        set_profile(
            good,
            distinct_login_days=10,
            total_credits_used=100,
            joined_beta_at=now - timedelta(days=5),
        )
        bad = make_teacher("exploder@gmail.com")
        bad_profile = set_profile(bad, total_credits_used=1)

        # Captured before patching, and taken off the class rather than the
        # task module: the task is a Celery proxy object with no __globals__.
        real_scorer = AnalyticsService.calculate_conversion_probability

        def _explode_for_one(profile):
            if profile.pk == bad_profile.pk:
                raise RuntimeError("boom")
            return real_scorer(profile)

        with patch(
            "billing.tasks.AnalyticsService.calculate_conversion_probability",
            side_effect=_explode_for_one,
        ):
            summary = recalculate_conversion_probabilities()

        self.assertIn("1 failed", summary)
        # The healthy profile was still scored and its write survived.
        self.assertEqual(
            BetaProfile.objects.get(user=good).conversion_probability, 30.0
        )

    def test_the_failure_is_logged_with_the_profile_identity(self):
        user = make_teacher("logged@gmail.com")
        set_profile(user, total_credits_used=1)

        with patch(
            "billing.tasks.AnalyticsService.calculate_conversion_probability",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertLogs("billing.tasks", level="ERROR") as logs:
                recalculate_conversion_probabilities()

        self.assertTrue(
            any("Conversion scoring failed" in line for line in logs.output),
            logs.output,
        )

    def test_an_empty_cohort_is_not_an_error(self):
        summary = recalculate_conversion_probabilities()
        self.assertIn("0 scored", summary)


class ConversionProbabilityScheduleTests(TestCase):
    """
    The scorer existing but never running IS the bug. The schedule entry is
    therefore part of the fix and is asserted, not assumed.
    """

    def test_the_task_is_registered_in_the_beat_schedule(self):
        entry = settings.CELERY_BEAT_SCHEDULE["recalculate-conversion-probabilities"]
        self.assertEqual(
            entry["task"], "billing.tasks.recalculate_conversion_probabilities"
        )

    def test_it_runs_nightly_clear_of_the_licence_renewal_job(self):
        entry = settings.CELERY_BEAT_SCHEDULE["recalculate-conversion-probabilities"]
        schedule = entry["schedule"]
        self.assertEqual(schedule.hour, {0})
        self.assertEqual(schedule.minute, {30})

        # process-license-renewals owns 00:00; analytics must not contend
        # with the billing jobs.
        renewals = settings.CELERY_BEAT_SCHEDULE["process-license-renewals"]["schedule"]
        self.assertNotEqual(
            (schedule.hour, schedule.minute),
            (renewals.hour, renewals.minute),
        )


class SalesLeadsUsagePercentageTests(TestCase):
    """
    The fourth division site. views.py:2202 divided by the raw
    `initial_beta_credits` while its three siblings guarded it.
    """

    def setUp(self):
        self.client = APIClient()
        # BetaAnalyticViewSet is gated by IsSuperAdmin, which requires BOTH
        # user_type == SUPER_ADMIN and is_superuser.
        self.staff = CustomUser.objects.create_user(
            email="leads.staff@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.staff)

    def _url(self):
        return reverse("analytics-intent-signals")

    def test_a_zero_allocation_profile_does_not_500_the_whole_list(self):
        """
        The regression: one profile with initial_beta_credits=0 raised
        ZeroDivisionError while building the response, taking out the
        entire sales-leads page rather than just that row.
        """
        teacher = make_teacher("zerolead@gmail.com")
        set_profile(teacher, initial_beta_credits=0, total_credits_used=500)

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)

    def test_a_normal_profile_still_reports_a_real_percentage(self):
        """The guard must not flatten the metric it protects."""
        teacher = make_teacher("normallead@gmail.com")
        set_profile(teacher, initial_beta_credits=1000, total_credits_used=250)

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        # APIJSONRenderer wraps every response as
        # {"success", "message", "data": <paginated payload>}.
        rows = response.json()["data"]["results"]
        match = [r for r in rows if r["email"] == "normallead@gmail.com"]
        self.assertEqual(len(match), 1, rows)
        self.assertEqual(match[0]["metrics"]["usage_percentage"], 25.0)
