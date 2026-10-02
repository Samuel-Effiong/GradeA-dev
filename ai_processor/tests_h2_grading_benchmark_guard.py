"""H2: the grading benchmark's production guard.

Command._resolve_user creates a teacher, a plan, a subscription and a
5,000,000-credit bucket in whatever database it runs against, and the
nightly beat job calls it too. Outside DEBUG it now refuses unless the
command gets --allow-non-debug or ENABLE_GRADING_BENCHMARK is set; the
weekly live job also needs ENABLE_AI_LIVE_QA.

runner.execute_benchmark is replaced by a function that raises `Reached`,
so no model is ever called: `Reached` means the guard let the run through.
"""

from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings

from ai_processor.management.commands.grading_benchmark import BenchmarkRefused
from billing.models import CreditBucket, SubscriptionPlan, UserSubscription
from users.models import CustomUser, UserTypes

BENCHMARK_EMAIL = "grading-benchmark@benchmark.local"
PLAN_NAME = "Grading Benchmark Plan"
EXECUTE = "ai_processor.benchmark.runner.execute_benchmark"
TASKS_LOGGER = "ai_processor.tasks"


class Reached(Exception):
    """The benchmark got past the guard to the (replaced) run."""


def reached(*args, **kwargs):
    raise Reached()


class BenchmarkRowsTestCase(TestCase):
    """Assertions on the benchmark's own rows; no tests of its own."""

    def assertNothingCreated(self):
        self.assertFalse(CustomUser.objects.filter(email=BENCHMARK_EMAIL).exists())
        self.assertFalse(SubscriptionPlan.objects.filter(name=PLAN_NAME).exists())
        self.assertFalse(UserSubscription.objects.filter(plan__name=PLAN_NAME).exists())
        self.assertFalse(
            CreditBucket.objects.filter(wallet__user__email=BENCHMARK_EMAIL).exists()
        )

    def assertBenchmarkTeacherCreated(self):
        self.assertTrue(CustomUser.objects.filter(email=BENCHMARK_EMAIL).exists())
        self.assertTrue(
            CreditBucket.objects.filter(wallet__user__email=BENCHMARK_EMAIL).exists()
        )

    def assertOneInfoSkipLine(self, logs, switch):
        """A skip is one INFO line (visible at the default level) that
        names the switch an operator would set.

        The switch is looked for in the line's own text (`record.msg`), not
        in the formatted message: the refusal line appends the exception,
        whose text names the switch too and would mask a line that doesn't.
        """
        self.assertEqual([record.levelname for record in logs.records], ["INFO"])
        template = logs.records[0].msg
        self.assertIn("skip", template)
        self.assertIn(switch, template)


class TheSwitchIsOffByDefaultTests(TestCase):
    def test_enable_grading_benchmark_defaults_to_false(self):
        self.assertIs(settings.ENABLE_GRADING_BENCHMARK, False)


@override_settings(DEBUG=False, ENABLE_GRADING_BENCHMARK=False)
class CommandGuardTests(BenchmarkRowsTestCase):
    def run_command(self, *args):
        call_command("grading_benchmark", *args, stdout=StringIO())

    def test_refused_outside_debug_and_nothing_is_created(self):
        with patch(EXECUTE, new=reached):
            with self.assertRaises(BenchmarkRefused) as caught:
                self.run_command()
        self.assertIn("--allow-non-debug", str(caught.exception))
        self.assertNothingCreated()

    def test_a_named_teacher_is_refused_too(self):
        """The guard is at the top of _resolve_user, before the lookup."""
        CustomUser.objects.create_user(
            email="named@benchmark.test",
            password=None,
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        with patch(EXECUTE, new=reached):
            with self.assertRaises(BenchmarkRefused):
                self.run_command("--teacher-email", "named@benchmark.test")

    def test_the_flag_lets_it_run(self):
        with patch(EXECUTE, new=reached):
            with self.assertRaises(Reached):
                self.run_command("--allow-non-debug")
        self.assertBenchmarkTeacherCreated()

    @override_settings(DEBUG=True)
    def test_debug_lets_it_run(self):
        with patch(EXECUTE, new=reached):
            with self.assertRaises(Reached):
                self.run_command()
        self.assertBenchmarkTeacherCreated()

    @override_settings(ENABLE_GRADING_BENCHMARK=True)
    def test_the_setting_lets_it_run(self):
        with patch(EXECUTE, new=reached):
            with self.assertRaises(Reached):
                self.run_command()
        self.assertBenchmarkTeacherCreated()


@override_settings(DEBUG=False)
class BeatGuardTests(BenchmarkRowsTestCase):
    @override_settings(ENABLE_GRADING_BENCHMARK=False)
    def test_the_nightly_replay_is_skipped_and_creates_nothing(self):
        from ai_processor.tasks import nightly_grading_benchmark_replay

        with patch(EXECUTE, new=reached), self.assertLogs(
            TASKS_LOGGER, "DEBUG"
        ) as logs:
            outcome = nightly_grading_benchmark_replay.apply()
        self.assertEqual(
            outcome.result,
            "Grading benchmark replay skipped: not enabled in this environment.",
        )
        self.assertOneInfoSkipLine(logs, "ENABLE_GRADING_BENCHMARK")
        self.assertNothingCreated()

    @override_settings(ENABLE_GRADING_BENCHMARK=True)
    def test_the_nightly_replay_runs_when_enabled(self):
        from ai_processor.tasks import nightly_grading_benchmark_replay

        with patch(EXECUTE, new=reached):
            outcome = nightly_grading_benchmark_replay.apply()
        self.assertIsInstance(outcome.result, Reached)
        self.assertBenchmarkTeacherCreated()

    @override_settings(ENABLE_AI_LIVE_QA=True, ENABLE_GRADING_BENCHMARK=False)
    def test_the_weekly_live_job_is_skipped_without_the_benchmark_switch(self):
        from ai_processor.tasks import weekly_grading_benchmark_live

        with patch(EXECUTE, new=reached), self.assertLogs(
            TASKS_LOGGER, "DEBUG"
        ) as logs:
            outcome = weekly_grading_benchmark_live.apply()
        self.assertEqual(
            outcome.result,
            "Grading benchmark live skipped: not enabled in this environment.",
        )
        self.assertOneInfoSkipLine(logs, "ENABLE_GRADING_BENCHMARK")
        self.assertNothingCreated()

    @override_settings(ENABLE_AI_LIVE_QA=False, ENABLE_GRADING_BENCHMARK=True)
    def test_the_weekly_live_job_is_skipped_without_live_qa(self):
        from ai_processor.tasks import weekly_grading_benchmark_live

        with patch(EXECUTE, new=reached), self.assertLogs(
            TASKS_LOGGER, "DEBUG"
        ) as logs:
            outcome = weekly_grading_benchmark_live.apply()
        self.assertEqual(
            outcome.result,
            "Grading benchmark live skipped: not enabled in this environment.",
        )
        self.assertOneInfoSkipLine(logs, "ENABLE_AI_LIVE_QA")
        self.assertNothingCreated()

    @override_settings(ENABLE_AI_LIVE_QA=True, ENABLE_GRADING_BENCHMARK=True)
    def test_the_weekly_live_job_runs_with_both_switches(self):
        from ai_processor.tasks import weekly_grading_benchmark_live

        with patch(EXECUTE, new=reached):
            outcome = weekly_grading_benchmark_live.apply()
        self.assertIsInstance(outcome.result, Reached)
        self.assertBenchmarkTeacherCreated()
