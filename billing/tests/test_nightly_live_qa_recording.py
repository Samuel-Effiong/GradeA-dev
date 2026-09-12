"""
billing/tests/test_nightly_live_qa_recording.py
===============================================
Every scheduled live-QA run must leave a row saying what happened.

WHY THIS FILE EXISTS
--------------------
The nightly `nightly_stripe_live_qa` task was scheduled correctly and had
fired 20 times on the deployed worker (`django_celery_beat` PeriodicTask:
enabled, total_run_count=20, last_run_at 2026-09-06 01:00). It wrote
nothing. `LiveQARun.objects.create` existed in exactly ONE place —
`qa_console.py`, the manual console — so from the database these three were
indistinguishable:

    * ran, everything passed
    * ran, ten scenarios failed against real Stripe
    * did nothing, because this worker is not a QA worker

Scenario failures went to worker logs at ERROR, and the "not enabled" path
logged at DEBUG (invisible at production log levels). A real-Stripe suite
whose status you cannot determine is not a safety net — it is the
appearance of one, which is worse, because it stops anyone looking.

WHAT IS PINNED
--------------
A terminal row exists after EVERY path:

    not enabled here      -> SKIPPED
    misconfigured         -> ERROR
    crashed unexpectedly  -> ERROR   (never left stuck at RUNNING)
    ran, all passed       -> PASSED
    ran, something failed -> FAILED

plus that the summary and per-scenario detail are persisted, so the morning
after a failure you can read what broke without going log-diving.
"""

from unittest.mock import patch

from django.test import TestCase

from billing.models import LiveQARun, LiveQARunKind, LiveQARunStatus
from billing.stripe_live_qa import (
    Check,
    LiveQAConfigurationError,
    LiveQARefused,
    ScenarioResult,
    SuiteResult,
)
from billing.tasks import nightly_stripe_live_qa


def suite(*, passed=True):
    """A SuiteResult shaped like the real one, pass or fail."""
    if passed:
        scenarios = [ScenarioResult(name="renewals", passed=True, duration_seconds=1.0)]
    else:
        scenarios = [
            ScenarioResult(name="renewals", passed=True, duration_seconds=1.0),
            ScenarioResult(
                name="void_or_refund_compensating_path",
                passed=False,
                duration_seconds=2.0,
                checks=[
                    Check(
                        name="a duplicate PAID invoice was refunded",
                        passed=False,
                        detail="refunds for the side-effect PaymentIntent: []",
                    )
                ],
            ),
        ]
    return SuiteResult(run_id="testrun", scenarios=scenarios)


class NightlyLiveQARecordingTests(TestCase):
    def setUp(self):
        self.assertEqual(LiveQARun.objects.count(), 0)

    def _only_run(self):
        self.assertEqual(
            LiveQARun.objects.count(),
            1,
            "the scheduled run left no record of what happened",
        )
        return LiveQARun.objects.get()

    # --- the path that was silent in production ------------------------

    def test_a_worker_where_qa_is_disabled_records_SKIPPED(self):
        """
        THE REGRESSION. This is what the deployed worker does every night,
        and it used to write nothing at all.
        """
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=False):
            nightly_stripe_live_qa()

        run = self._only_run()
        self.assertEqual(run.status, LiveQARunStatus.SKIPPED)
        self.assertIn("not enabled", run.summary)
        self.assertIsNotNone(run.finished_at, "the row was left non-terminal")

    def test_the_skipped_row_says_what_would_switch_it_on(self):
        """A row that records a skip without saying why is barely better."""
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=False):
            nightly_stripe_live_qa()

        self.assertIn("ENABLE_STRIPE_LIVE_QA", self._only_run().summary)

    # --- ran, and the outcome ------------------------------------------

    def test_a_clean_run_records_PASSED_with_a_summary(self):
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=True), patch(
            "billing.stripe_live_qa_scenarios.run_suite",
            return_value=suite(passed=True),
        ), patch(
            "billing.stripe_live_qa_scenarios.scenarios_for_tier",
            return_value=["renewals"],
        ):
            nightly_stripe_live_qa()

        run = self._only_run()
        self.assertEqual(run.status, LiveQARunStatus.PASSED)
        self.assertTrue(run.summary)
        self.assertIsNotNone(run.finished_at)

    def test_a_failing_run_records_FAILED(self):
        """
        The case that matters: ten scenarios were failing nightly and the
        database said nothing.
        """
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=True), patch(
            "billing.stripe_live_qa_scenarios.run_suite",
            return_value=suite(passed=False),
        ), patch(
            "billing.stripe_live_qa_scenarios.scenarios_for_tier", return_value=["x"]
        ):
            nightly_stripe_live_qa()

        run = self._only_run()
        self.assertEqual(run.status, LiveQARunStatus.FAILED)

    def test_the_failing_scenario_is_identifiable_from_the_row(self):
        """
        You must be able to tell WHAT broke from the record, not by
        correlating timestamps against worker logs.
        """
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=True), patch(
            "billing.stripe_live_qa_scenarios.run_suite",
            return_value=suite(passed=False),
        ), patch(
            "billing.stripe_live_qa_scenarios.scenarios_for_tier", return_value=["x"]
        ):
            nightly_stripe_live_qa()

        run = self._only_run()
        blob = f"{run.summary} {run.result_data}"
        self.assertIn("void_or_refund_compensating_path", blob)

    # --- the failure modes that must not leave a stuck row ---------------

    def test_a_misconfigured_environment_records_ERROR_not_FAILED(self):
        """
        ERROR (our environment is wrong) must be distinguishable from
        FAILED (Stripe disagrees with the billing code). Conflating them
        trains people to ignore red.
        """
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=True), patch(
            "billing.stripe_live_qa_scenarios.run_suite",
            side_effect=LiveQARefused("no test keys"),
        ), patch(
            "billing.stripe_live_qa_scenarios.scenarios_for_tier", return_value=["x"]
        ):
            nightly_stripe_live_qa()

        run = self._only_run()
        self.assertEqual(run.status, LiveQARunStatus.ERROR)
        self.assertIn("no test keys", run.summary)

    def test_a_configuration_error_also_records_ERROR(self):
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=True), patch(
            "billing.stripe_live_qa_scenarios.run_suite",
            side_effect=LiveQAConfigurationError("unknown scenario"),
        ), patch(
            "billing.stripe_live_qa_scenarios.scenarios_for_tier", return_value=["x"]
        ):
            nightly_stripe_live_qa()

        self.assertEqual(self._only_run().status, LiveQARunStatus.ERROR)

    def test_an_unexpected_crash_never_leaves_the_row_RUNNING(self):
        """
        A row stuck at RUNNING forever is the same blind spot in a new
        costume: nobody can tell whether it is still going or died.
        """
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=True), patch(
            "billing.stripe_live_qa_scenarios.run_suite",
            side_effect=RuntimeError("stripe exploded"),
        ), patch(
            "billing.stripe_live_qa_scenarios.scenarios_for_tier", return_value=["x"]
        ):
            nightly_stripe_live_qa()

        run = self._only_run()
        self.assertEqual(run.status, LiveQARunStatus.ERROR)
        self.assertNotEqual(run.status, LiveQARunStatus.RUNNING)
        self.assertIsNotNone(run.finished_at)
        self.assertIn("stripe exploded", run.summary)

    # --- shape of the record --------------------------------------------

    def test_the_run_is_marked_as_a_scenario_run(self):
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=False):
            nightly_stripe_live_qa()

        self.assertEqual(self._only_run().kind, LiveQARunKind.SCENARIO)

    def test_the_run_records_which_tier_it_ran(self):
        """Without the tier you cannot tell a nightly fast run from a
        weekly deep one when reading history."""
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=False):
            nightly_stripe_live_qa()

        self.assertEqual(self._only_run().tier, "fast")

    def test_every_night_leaves_its_own_row(self):
        """History, not a single overwritten latest-status row."""
        with patch("billing.stripe_live_qa.live_qa_enabled", return_value=False):
            nightly_stripe_live_qa()
            nightly_stripe_live_qa()
            nightly_stripe_live_qa()

        self.assertEqual(LiveQARun.objects.count(), 3)
