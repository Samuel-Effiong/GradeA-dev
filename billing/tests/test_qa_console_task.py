"""
billing.tasks.run_live_qa_console_job: status transitions and result
serialization, against mocked run_suite / run_chaos_walk /
shrink_chaos_failure so this costs nothing and never touches Stripe.
Suite/chaos/shrink CORRECTNESS is covered where each is defined
(test_stripe_live_qa.py, test_chaos.py) -- this file only proves the
task dispatches to the right one and leaves the row in the right
terminal state.
"""

from unittest.mock import patch

from django.test import TestCase

from billing.live_qa.chaos import ChaosWalkResult, ExecutedStep, ShrinkResult
from billing.models import LiveQARun, LiveQARunKind, LiveQARunStatus
from billing.stripe_live_qa import ScenarioResult, SuiteResult
from billing.tasks import run_live_qa_console_job


class RunLiveQaConsoleJobTests(TestCase):
    def _run(self, **kwargs):
        return LiveQARun.objects.create(**kwargs)

    # -- scenario runs -----------------------------------------------------

    def test_passing_scenario_run_is_marked_passed(self):
        run = self._run(kind=LiveQARunKind.SCENARIO, scenario_names=["renewals"])
        suite_result = SuiteResult(run_id="x")
        suite_result.scenarios.append(ScenarioResult(name="renewals", passed=True))

        with patch(
            "billing.stripe_live_qa_scenarios.run_suite", return_value=suite_result
        ) as mock_run_suite:
            run_live_qa_console_job(str(run.id))

        mock_run_suite.assert_called_once_with(["renewals"])
        run.refresh_from_db()
        self.assertEqual(run.status, LiveQARunStatus.PASSED)
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.result_data["run_id"], "x")

    def test_failing_scenario_run_is_marked_failed(self):
        run = self._run(kind=LiveQARunKind.SCENARIO, tier="fast")
        suite_result = SuiteResult(run_id="x")
        suite_result.scenarios.append(ScenarioResult(name="renewals", passed=False))

        with patch(
            "billing.stripe_live_qa_scenarios.scenarios_for_tier",
            return_value=["renewals"],
        ), patch(
            "billing.stripe_live_qa_scenarios.run_suite", return_value=suite_result
        ):
            run_live_qa_console_job(str(run.id))

        run.refresh_from_db()
        self.assertEqual(run.status, LiveQARunStatus.FAILED)

    # -- chaos runs ----------------------------------------------------

    def test_passing_chaos_walk_is_marked_passed(self):
        run = self._run(kind=LiveQARunKind.CHAOS, seed=1, steps=5)
        walk_result = ChaosWalkResult(seed=1, steps=5)
        walk_result.executed = [
            ExecutedStep(index=0, action="advance_boundary", note="ok")
        ]

        with patch(
            "billing.live_qa.chaos.run_chaos_walk", return_value=walk_result
        ) as mock_walk:
            run_live_qa_console_job(str(run.id))

        mock_walk.assert_called_once_with(1, 5)
        run.refresh_from_db()
        self.assertEqual(run.status, LiveQARunStatus.PASSED)

    def test_failing_chaos_walk_is_marked_failed(self):
        run = self._run(kind=LiveQARunKind.CHAOS, seed=1, steps=5)
        walk_result = ChaosWalkResult(seed=1, steps=5, infra_error="boom")

        with patch("billing.live_qa.chaos.run_chaos_walk", return_value=walk_result):
            run_live_qa_console_job(str(run.id))

        run.refresh_from_db()
        self.assertEqual(run.status, LiveQARunStatus.FAILED)

    def test_shrink_is_always_marked_passed_regardless_of_whether_it_found_a_repro(
        self,
    ):
        """A shrink's job is to find a minimal repro, not to report
        whether billing behaved correctly -- see the task's docstring."""
        run = self._run(kind=LiveQARunKind.CHAOS, seed=1, steps=20, shrink=True)
        shrink_result = ShrinkResult(seed=1, original_steps=20, minimal_steps=None)

        with patch(
            "billing.live_qa.chaos.shrink_chaos_failure", return_value=shrink_result
        ) as mock_shrink:
            run_live_qa_console_job(str(run.id))

        mock_shrink.assert_called_once_with(1, 20)
        run.refresh_from_db()
        self.assertEqual(run.status, LiveQARunStatus.PASSED)

    # -- crash handling --------------------------------------------------

    def test_a_crash_before_completion_leaves_the_row_failed_not_stuck_running(self):
        run = self._run(kind=LiveQARunKind.SCENARIO, scenario_names=["renewals"])

        with patch(
            "billing.stripe_live_qa_scenarios.run_suite",
            side_effect=RuntimeError("boom"),
        ):
            run_live_qa_console_job(str(run.id))

        run.refresh_from_db()
        self.assertEqual(run.status, LiveQARunStatus.FAILED)
        self.assertIn("boom", run.summary)
        self.assertIsNotNone(run.finished_at)
